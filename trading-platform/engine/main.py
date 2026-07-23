"""매매 엔진 — 1시간 주기 리스크 실드 + 2단계 시그널 파이프라인.

⚠️ 이 모듈은 **주문을 내지 않는다.** 하는 일:
  1) 토스 잔고(collector가 Redis에 적재)로 총자산·현금 점검, 최고점(peak) 추적.
  2) 멍거 리스크 실드 평가(MDD 서킷브레이커·현금 바닥·종목당 한도) → engine:risk.
     BUY_LOCK 전환 시 텔레그램 알림. 수동 주문 API도 이 상태를 게이트로 사용.
  3) 2단계 필터: 정량(ROE>10·PBR<1.5·PER<15, 데이터 완전 종목만) →
     AI 역방향 감점(research 큐) → 최종 70점 이상만 engine:buylist.
실행: python -m engine.main  (docker service: engine)
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

import httpx
import redis.asyncio as aioredis

from api.services.stock_radar import supply_demand
from api.services.stock_score import compute_score
from api.services.stock_signal import light_pillar, pillar_guide, trade_levels
from api.services.stock_value import load_quotes
from collector.stock.kis import effective_watchlist
from collector.stock.toss import TossClient
from engine.orders import place_gated_order
from engine.plan import exit_plan, sell_checks, stage1_rank, suggest_qty, swing_metrics
from engine.risk import evaluate_risk
from engine.screener import final_score, quant_filter
from engine.telegram_cmd import command_loop
from notifier.telegram import TelegramSender
from shared.redis_keys import (
    ASSET_HIST_KEY,
    ENGINE_ALERTS_KEY,
    ENGINE_TRAIL_KEY,
    ENGINE_AUTO_KEY,
    ENGINE_PILLAR_KEY,
    ENGINE_BUYLIST_KEY,
    ENGINE_PEAK_KEY,
    ENGINE_PLAN_KEY,
    ENGINE_RISK_KEY,
    FWD_DONE_KEY,
    FX_USDKRW_KEY,
    COACH_KEY,
    COACH_WD_KEY,
    RESEARCH_HB_KEY,
    RESEARCH_INV_KEY,
    RESEARCH_INV_REQ_KEY,
    STOCK_QUOTE_KEY,
    TOSS_ACCOUNT_KEY,
    TOSS_HOLDINGS_KEY,
    fwd_scores_key,
    stock_ohlcv_key,
)
from shared.settings import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("engine")


async def _json_get(redis: aioredis.Redis, key: str) -> dict:
    raw = await redis.get(key)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


async def _assets(redis: aioredis.Redis) -> tuple[float | None, float | None]:
    """(총자산=평가액+현금, 현금). 데이터 없으면 (None, None)."""
    hold = await _json_get(redis, TOSS_HOLDINGS_KEY)
    acc = await _json_get(redis, TOSS_ACCOUNT_KEY)
    ev = hold.get("total_eval")
    cash = acc.get("buying_power")
    if ev is None and cash is None:
        return None, None
    return (ev or 0.0) + (cash or 0.0), cash


async def _update_risk(redis: aioredis.Redis, sender: TelegramSender) -> dict:
    total, cash = await _assets(redis)
    peak = None
    raw = await redis.get(ENGINE_PEAK_KEY)
    if raw:
        try:
            peak = float(raw)
        except (TypeError, ValueError):
            peak = None
    if total and (peak is None or total > peak):
        peak = total
        await redis.set(ENGINE_PEAK_KEY, str(peak))
    risk = evaluate_risk(total, peak, cash,
                         mdd_limit_pct=settings.mdd_limit_pct,
                         max_stock_pct=settings.max_stock_pct,
                         cash_floor_pct=settings.cash_floor_pct)
    prev = await _json_get(redis, ENGINE_RISK_KEY)
    risk_out = {**risk, "total_asset": total, "peak_asset": peak, "ts": time.time()}
    await redis.set(ENGINE_RISK_KEY, json.dumps(risk_out, ensure_ascii=False))
    if risk["buy_lock"] and not prev.get("buy_lock"):
        await sender.send("🛑 리스크 실드 발동 — 자동 매수 잠금\n"
                          + "\n".join(risk["reasons"]))
        logger.warning("BUY_LOCK 발동: %s", risk["reasons"])
    elif not risk["buy_lock"] and prev.get("buy_lock"):
        await sender.send("✅ 리스크 실드 해제 — 매수 허용 범위 복귀")
    return risk_out


async def _closes(redis: aioredis.Redis, code: str) -> list:
    raw = await redis.get(stock_ohlcv_key(code))
    if not raw:
        return []
    try:
        return [c["close"] for c in json.loads(raw)
                if isinstance(c, dict) and c.get("close")]
    except (json.JSONDecodeError, TypeError):
        return []


async def _auto_buy(redis: aioredis.Redis, toss: TossClient, kis,
                    sender: TelegramSender, risk: dict, rows: list[dict]) -> None:
    """자동매수(옵트인): 필터 통과 신규 종목을 추천 매수가에 지정가 주문.

    브로커 = AUTO_TRADE_BROKER(기본 kis — 토스는 수동 매매 전용). 조건:
    AUTO_TRADE_ENABLED + 브로커 매매 플래그 + BUY_LOCK 아님 + 상승추세 +
    미보유 + 쿨다운(7일) 밖. 예산 = min(종목당 5% 한도, 브로커 주문 한도).
    """
    if not settings.auto_trade_enabled or risk.get("buy_lock"):
        return
    broker = settings.auto_trade_broker
    max_order = (settings.kis_max_order_krw if broker == "kis"
                 else settings.toss_max_order_krw)
    hold = await _json_get(redis, TOSS_HOLDINGS_KEY)
    held = {h.get("symbol") for h in hold.get("holdings", [])}
    now = time.time()
    for r in rows:
        code, entry = r["code"], r.get("entry")
        if not entry or code in held or r.get("trend_ok") is False:
            continue
        raw = await redis.hget(ENGINE_AUTO_KEY, code)
        if raw:
            try:
                if now - (json.loads(raw).get("ts") or 0) < settings.auto_trade_cooldown_sec:
                    continue                     # 쿨다운 내 재매수 금지
            except (json.JSONDecodeError, TypeError):
                pass
        # 수급 확인 게이트: 외인·기관이 '분산(순매도)' 중인 종목은 자동매수 보류.
        # (스마트머니가 파는데 규칙만 믿고 사지 않기 — 매수는 확인, 매도는 항상 허용 원칙과 일관.)
        if code.isdigit() and toss.enabled:
            try:
                async with httpx.AsyncClient(timeout=12) as sdc:
                    inv = await toss.fetch_investor_trading(sdc, code, count=5)
                sd = supply_demand(inv)
                if (sd.get("net_eok") or 0) <= -settings.auto_supply_block_eok:
                    await sender.send(
                        f"⏸️ 자동매수 보류 — {r['name']}({code})\n"
                        f"필터는 통과했으나 외인+기관 5일 {sd['net_eok']:.0f}억 순매도(분산) "
                        f"— 스마트머니 매도 중이라 매수 보류.")
                    logger.info("[auto] %s 수급 분산(%.0f억) — 매수 보류", code,
                                sd.get("net_eok") or 0)
                    continue
            except Exception:
                pass                             # 수급 조회 실패는 게이트 통과(막지 않음)
        budget = min(risk.get("per_stock_cap") or max_order, max_order)
        qty = int(budget // entry)
        if qty < 1:
            continue
        ok, msg = await place_gated_order(redis, side="BUY", code=code,
                                          qty=qty, price=entry, broker=broker,
                                          kis=kis, toss=toss)
        await redis.hset(ENGINE_AUTO_KEY, code, json.dumps(
            {"ts": now, "ok": ok, "qty": qty, "price": entry, "broker": broker},
            ensure_ascii=False))
        await sender.send(("🤖 자동매수 " + ("접수 ✅" if ok else "거부 🚫")) +
                          f"\n{r['name']}({code}) {qty}주 @{entry:,.0f}원 "
                          f"(최종 {r['final']:.0f}점)\n{msg}\n"
                          f"손절 {r.get('stop') or 0:,.0f} · 목표 {r.get('target') or 0:,.0f}")
        logger.info("[auto/%s] %s BUY x%d @%.0f → %s", broker, code, qty, entry, ok)


async def _pipeline(redis: aioredis.Redis, sender: TelegramSender,
                    risk: dict, toss: TossClient, kis) -> None:
    """2단계 필터 실행 → engine:buylist 저장(+신규 진입 알림·옵트인 자동매수)."""
    quotes = await load_quotes(redis)
    cands = quant_filter(quotes,
                         roe_min=10.0, pbr_max=1.5, per_max=15.0)
    # 정량 매력도 상위 순으로 정렬(감점 검증 우선순위)
    scored = []
    for q in cands:
        closes = await _closes(redis, q["code"])
        sc = compute_score(q, closes)
        scored.append((sc["score"], q, sc, closes))
    scored.sort(key=lambda x: x[0], reverse=True)

    inv_raw = await redis.hgetall(RESEARCH_INV_KEY)
    now = time.time()
    rows, requested = [], 0
    prev = await _json_get(redis, ENGINE_BUYLIST_KEY)
    prev_codes = {r.get("code") for r in prev.get("rows", [])}
    for qscore, q, sc, closes in scored[:30]:  # 상위 30개만 관리(부하·토큰 절약)
        code = q["code"]
        inv = None
        if code in inv_raw:
            try:
                inv = json.loads(inv_raw[code])
            except (json.JSONDecodeError, TypeError):
                inv = None
        fresh = inv and (now - (inv.get("ts") or 0) < settings.inversion_fresh_sec)
        if not fresh:
            if requested < settings.inversion_max_per_cycle:
                await redis.sadd(RESEARCH_INV_REQ_KEY, code)
                requested += 1
            continue                            # 감점 검증 전 — 리스트 보류
        penalty = inv.get("penalty")
        final = final_score(qscore, penalty)
        if final is None or final < settings.buy_score_min:
            continue
        lv = trade_levels(closes, q.get("price")) or {}
        rows.append({"code": code, "name": q.get("name", ""),
                     "price": q.get("price"), "quant_score": qscore,
                     "penalty": penalty, "final": final,
                     "roe": q.get("roe"), "per": q.get("per"), "pbr": q.get("pbr"),
                     "entry": lv.get("entry"), "stop": lv.get("stop"),
                     "target": lv.get("target"), "trend_ok": lv.get("trend_ok"),
                     "risk_summary": (inv.get("report") or "").strip()[:200]})
    rows.sort(key=lambda r: r["final"], reverse=True)
    await redis.set(ENGINE_BUYLIST_KEY, json.dumps(
        {"rows": rows, "buy_lock": risk.get("buy_lock"), "ts": now},
        ensure_ascii=False))
    logger.info("[engine] 후보 %d → 검증대기 %d → 매수리스트 %d",
                len(cands), requested, len(rows))
    new = [r for r in rows if r["code"] not in prev_codes]
    if new and not risk.get("buy_lock"):
        lines = []
        for r in new[:5]:
            line = (f"· {r['name']}({r['code']}) 최종 {r['final']:.0f}점 "
                    f"(정량 {r['quant_score']:.0f} − 감점 {r['penalty']})")
            if r.get("entry"):
                line += (f"\n  매수 {r['entry']:,.0f} · 손절 {r['stop']:,.0f} · "
                         f"목표 {r['target']:,.0f}")
                if r.get("trend_ok") is False:
                    line += " ⚠️하락추세"
            lines.append(line)
        await sender.send("🎯 매수 후보 진입(2단계 필터 통과)\n" + "\n".join(lines)
                          + ("\n※ 자동매수 활성 — 조건 충족 시 자동 주문"
                             if settings.auto_trade_enabled
                             else "\n※ 자동 주문 아님 — 최종 결정은 직접"))
    if new:
        await _auto_buy(redis, toss, kis, sender, risk, new)


async def _live_price(redis: aioredis.Redis, code: str) -> float | None:
    """stock:quote의 실시간가(웹소켓/토스) — 2분 내 신선한 것만."""
    raw = await redis.hget(STOCK_QUOTE_KEY, code)
    if not raw:
        return None
    try:
        rec = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    p, ts = rec.get("price"), rec.get("ts")
    if p and ts and time.time() - ts < 120:
        return float(p)
    return None


async def _holdings_alerts(redis: aioredis.Redis, sender: TelegramSender) -> None:
    """보유 종목이 추천 목표가/손절선에 닿으면 알림(종목·종류당 24h 1회).

    가격 가이드(trade_levels)와 같은 기준. 구간을 벗어나면 상태 해제 → 재진입 시 재알림.
    가격은 잔고 스냅샷(30초)보다 신선한 실시간 시세(웹소켓)가 있으면 그걸 쓴다.
    """
    hold = await _json_get(redis, TOSS_HOLDINGS_KEY)
    prev = await redis.hgetall(ENGINE_ALERTS_KEY)
    trail_state = await redis.hgetall(ENGINE_TRAIL_KEY)
    for h in hold.get("holdings", []):
        code = h.get("symbol")
        name = h.get("name") or code
        cur, avg = h.get("cur_price"), h.get("avg_price")
        cur = await _live_price(redis, code) or cur
        if not code or not cur or not avg:
            continue
        kr = code.isdigit()
        # 진입 후 고점(peak) 추적 — 트레일링 스탑의 기준
        try:
            st = json.loads(trail_state[code]) if code in trail_state else {}
        except (json.JSONDecodeError, TypeError):
            st = {}
        peak = max(st.get("peak") or 0.0, cur, avg)
        ep = exit_plan(avg, cur, peak, await _closes(redis, code), kr=kr,
                       trail_pct=settings.trail_stop_pct,
                       half_taken=bool(st.get("half_taken")))
        if not ep:
            continue
        await redis.hset(ENGINE_TRAIL_KEY, code, json.dumps(
            {"peak": ep["peak"], "half_taken": bool(st.get("half_taken")),
             "ts": time.time()}))
        if ep["action"] == "보유":
            if code in prev:
                await redis.hdel(ENGINE_ALERTS_KEY, code)   # 알림 구간 이탈 → 리셋
            continue
        stage = ep["stage"]
        try:
            last = json.loads(prev[code]) if code in prev else {}
        except (json.JSONDecodeError, TypeError):
            last = {}
        if last.get("kind") == stage and time.time() - (last.get("ts") or 0) < 86400:
            continue                                        # 같은 상태 24h 내 재알림 금지
        fmt = (lambda v: f"{v:,.0f}원") if kr else (lambda v: f"${v:,.2f}")
        icon = {"트레일링 스탑 도달": "📉", "목표 도달": "🎯",
                "손절선 이탈": "🛑"}.get(stage, "🔔")
        await sender.send(
            f"{icon} {ep['action']} — {name}({code})\n"
            f"현재 {fmt(cur)} · 평단 대비 {ep['pnl_pct']:+.1f}% · "
            f"트레일링 스탑 {fmt(ep['trail_stop'])}\n{ep['reason']}\n"
            "※ 판단 보조 — 최종 결정은 직접")
        await redis.hset(ENGINE_ALERTS_KEY, code,
                         json.dumps({"kind": stage, "ts": time.time()}))
        logger.info("[alert] %s %s (cur %.0f, stop %.0f)",
                    code, stage, cur, ep["trail_stop"])


async def _coach_watchdog(redis: aioredis.Redis, sender: TelegramSender) -> None:
    """아침 점검 미발송 감시견(엔진은 도커라 확실히 24h 생존).

    코치 시각+20분이 지나도 오늘 리포트가 없으면 — 호스트 research가 죽었거나
    멈춘 것 — 원인 진단(하트비트 유무)과 복구 명령을 텔레그램으로 하루 1회 통보.
    """
    from research.coach import overdue
    if not settings.coach_enabled:
        return
    raw = await redis.get(COACH_KEY)
    last_ts = 0.0
    if raw:
        try:
            last_ts = float(json.loads(raw).get("ts") or 0)
        except (json.JSONDecodeError, TypeError, ValueError):
            last_ts = 0.0
    if not overdue(time.time(), last_ts, settings.coach_hour_kst):
        return
    today = time.strftime("%Y-%m-%d")
    if await redis.get(COACH_WD_KEY) == today:
        return                                  # 오늘 이미 경고함
    hb = await redis.get(RESEARCH_HB_KEY)
    if hb:
        diag = ("research 프로세스는 살아 있는데 점검이 안 나갔어요 — "
                "호스트에서 tail -50 /tmp/research.log 확인"
                "(claude 로그인 만료/타임아웃 가능). 텔레그램 '점검'으로 수동 재시도 가능.")
    else:
        diag = ("호스트 research 프로세스가 죽어 있어요. Pi에서(일반 사용자로):\n"
                "sudo pkill -f research.main\n"
                "cd ~/SundayDeepLearning/trading-platform\n"
                "nohup bash deploy/run-research-host.sh >/tmp/research.log 2>&1 &")
    await sender.send(f"⏰ 아침 점검({settings.coach_hour_kst}시) 미발송 감지\n{diag}")
    await redis.set(COACH_WD_KEY, today)
    logger.warning("[watchdog] 아침 점검 미발송 — 경고 발송(hb=%s)", bool(hb))


async def _guard_loop(redis: aioredis.Redis, sender: TelegramSender) -> None:
    """고속 가드 — 목표가/손절선 감시만 20초 주기(실시간 시세 대응).

    무거운 파이프라인(플랜·필터·리스크)은 _cycle_loop(10분)에 남기고,
    '지금 팔아야 하는 순간'의 감지만 빠르게 돈다. 알림 dedup은
    ENGINE_ALERTS_KEY가 담당하므로 사이클 루프와 중복 실행해도 안전.
    """
    await asyncio.sleep(15)                    # 기동 직후 잔고 적재 여유
    while True:
        try:
            await _holdings_alerts(redis, sender)
            await _coach_watchdog(redis, sender)
        except Exception as exc:
            logger.warning("[DATA_ERROR] guard 실패: %s", exc)
        await asyncio.sleep(settings.guard_interval_sec)


async def _cycle_loop(redis: aioredis.Redis, sender: TelegramSender,
                      toss: TossClient, kis) -> None:
    while True:
        try:
            risk = await _update_risk(redis, sender)
            await _holdings_alerts(redis, sender)
            await _pillar_scan(redis, sender)
            await _pipeline(redis, sender, risk, toss, kis)
            await _swing_plan(redis, toss, risk)
            await _forward_log(redis)
            await _asset_snapshot(redis)
        except Exception as exc:
            # 어떤 오류도 엔진을 죽이지 않는다 — 기록 후 다음 주기.
            logger.warning("[DATA_ERROR] engine 사이클 실패: %s", exc)
        await asyncio.sleep(settings.engine_interval_sec)


async def _asset_snapshot(redis: aioredis.Redis) -> None:
    """총자산 일 1회 스냅샷(100억 로드맵 페이스 계산용). ~730일 보존."""
    today = time.strftime("%Y-%m-%d")
    hist = await redis.lrange(ASSET_HIST_KEY, -1, -1)
    if hist:
        try:
            if json.loads(hist[0]).get("date") == today:
                return                              # 오늘 이미 기록
        except (json.JSONDecodeError, TypeError):
            pass
    asset, _cash = await _assets(redis)
    if not asset:
        return
    await redis.rpush(ASSET_HIST_KEY, json.dumps(
        {"date": today, "ts": time.time(), "eval": round(asset, 0)}))
    await redis.ltrim(ASSET_HIST_KEY, -730, -1)


async def _forward_log(redis: aioredis.Redis) -> None:
    """포워드 로깅(Validation First) — 매일 1회 전 종목 점수·가격 스냅샷.

    T+5/20/60 시점에 현재가와 비교해 '점수가 실제로 수익률을 예측하는가'
    (캘리브레이션·축 IC·중복 반영)를 측정하는 원료. 120일 보존.
    """
    today = time.strftime("%Y-%m-%d")
    if await redis.get(FWD_DONE_KEY) == today:
        return
    quotes = [q for q in await load_quotes(redis)
              if q.get("price") and q.get("code")]
    key = fwd_scores_key(today)
    n = 0
    for q in quotes:
        closes = await _closes(redis, q["code"])
        sc = compute_score(q, closes)
        await redis.hset(key, q["code"], json.dumps({
            "s": sc["score"], "p": q["price"], "c": sc.get("confidence"),
            "v": sc.get("value"), "q": sc.get("quality"), "g": sc.get("growth"),
            "m": sc.get("momentum"), "t": sc.get("timing")}))
        n += 1
    await redis.expire(key, 120 * 86400)
    await redis.set(FWD_DONE_KEY, today)
    logger.info("[fwd] 점수 스냅샷 %d종목 저장(%s)", n, today)


async def _swing_plan(redis: aioredis.Redis, toss: TossClient, risk: dict) -> None:
    """오늘의 매매 플랜(설문 맞춤: 실적+추세 스윙 · 후보 3개+근거 · 중립 · KR+US).

    1차(실적·52주 위치)로 전 시장에서 상위 40개 → 일봉(없으면 토스 온디맨드,
    6h 캐시)으로 스윙 점수 → 매수 후보 3. 보유는 매도 신호 심각도 상위 3.
    """
    quotes = await load_quotes(redis)
    qmap = {q.get("code"): q for q in quotes if q.get("code")}
    hold = await _json_get(redis, TOSS_HOLDINGS_KEY)
    holdings = hold.get("holdings", [])
    held = {h.get("symbol") for h in holdings if h.get("symbol")}
    asset, _cash = await _assets(redis)
    fx_raw = await redis.get(FX_USDKRW_KEY)
    fx = None
    if fx_raw:
        try:
            fx = float(json.loads(fx_raw).get("rate") or 0) or None
        except (json.JSONDecodeError, TypeError, ValueError):
            fx = None

    buys: list[dict] = []
    async with httpx.AsyncClient(timeout=15) as client:
        for q in stage1_rank(quotes, held, top=40):
            code = q["code"]
            candles: list = []
            raw_c = await redis.get(stock_ohlcv_key(code))
            if raw_c:
                try:
                    candles = json.loads(raw_c)
                except (json.JSONDecodeError, TypeError):
                    candles = []
            if len(candles) < 60 and toss.enabled:   # 일봉 없으면 온디맨드(6h 캐시)
                try:
                    candles = await toss.fetch_daily_history(client, code)
                    if candles:
                        await redis.set(stock_ohlcv_key(code),
                                        json.dumps(candles, ensure_ascii=False),
                                        ex=21600)
                except Exception:
                    continue
            closes = [c["close"] for c in candles
                      if isinstance(c, dict) and c.get("close")]
            m = swing_metrics(q, candles, today=time.strftime("%Y%m%d"))
            if not m:
                continue
            kr = code.isdigit()
            lv = trade_levels(closes, q.get("price"), kr=kr) or {}
            qty = suggest_qty(lv.get("entry") or 0, asset,
                              risk.get("per_stock_cap"), fx=fx, usd=not kr)
            buys.append({"code": code, "name": q.get("name", ""),
                         "price": q.get("price"), "currency": q.get("currency", "KRW"),
                         "swing": m["swing"], "reasons": m["reasons"],
                         "entry": lv.get("entry"), "stop": lv.get("stop"),
                         "target": lv.get("target"), "qty": qty})
    buys.sort(key=lambda b: b["swing"], reverse=True)

    sells: list[dict] = []
    for h in holdings:
        code = h.get("symbol") or ""
        if not code:
            continue
        q = qmap.get(code, {})
        g = q.get("flash_ni_yoy")
        if g is None:
            g = q.get("flash_op_yoy")
        if g is None:
            g = q.get("ni_growth_q_pct")
        h2 = {**h, "_growth": g, "_chg": q.get("change_pct")}
        chk = sell_checks(h2, await _closes(redis, code))
        # 약한 단일 신호(예: 추세 이탈 하나)는 소음 — 심각도 3 이상만 목록에
        if chk["severity"] >= 3:
            sells.append({"code": code, "name": h.get("name", ""),
                          "pnl_pct": h.get("pnl_pct"), **chk})
    sells.sort(key=lambda s: s["severity"], reverse=True)

    await redis.set(ENGINE_PLAN_KEY, json.dumps(
        {"style": "실적+추세 스윙 · 중립 리스크 · 국내 전체+미국",
         "buys": buys[:3], "sells": sells[:3], "ts": time.time()},
        ensure_ascii=False))
    logger.info("[plan] 매수 후보 %d(검증 %d) · 매도 점검 %d",
                min(3, len(buys)), len(buys), min(3, len(sells)))


async def _pillar_scan(redis: aioredis.Redis, sender: TelegramSender) -> None:
    """빛의기둥(수급 포착) — 관심+보유 종목의 최신 일봉 검사, 종목당 하루 1회 알림.

    조건: 거래대금 20억↑ · 양봉 · 고가 마감(몸통>윗꼬리×1.2) · 평소(직전2일)의 3배 수급.
    ※ 캔들은 6시간 주기 갱신이라 장중 감지는 지연될 수 있음(정확도 우선).
    """
    watch = await effective_watchlist(redis)
    hold = await _json_get(redis, TOSS_HOLDINGS_KEY)
    names = {w["code"]: w.get("name", "") for w in watch}
    for h in hold.get("holdings", []):
        if h.get("symbol"):
            names.setdefault(h["symbol"], h.get("name", ""))
    today = time.strftime("%Y-%m-%d")
    for code, name in names.items():
        if not code.isdigit():
            continue   # 빛의기둥 기준(거래대금 억원)은 국내 전용
        raw = await redis.get(stock_ohlcv_key(code))
        if not raw:
            continue
        try:
            candles = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        lp = light_pillar(candles)
        if not lp or not lp.get("pillar"):
            continue
        if await redis.hget(ENGINE_PILLAR_KEY, code) == today:
            continue                                    # 하루 1회
        await redis.hset(ENGINE_PILLAR_KEY, code, today)
        guide = pillar_guide(candles, kr=code.isdigit())
        await sender.send(
            f"💡 빛의기둥(수급 포착) — {name or code}({code})\n"
            f"거래대금 {lp['value_eok']:,.0f}억 · 평소의 {lp['surge_x']:.1f}배 · "
            "고가 마감 장대양봉\n"
            + (guide + "\n" if guide else "")
            + "※ 테마 동반 여부 확인 · 판단 보조")
        logger.info("[pillar] %s %.0f억 x%.1f", code, lp["value_eok"], lp["surge_x"])


async def run() -> None:
    from collector.stock.kis import KISClient

    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    sender = TelegramSender()
    toss = TossClient()
    kis = KISClient()
    logger.info("engine start (interval=%ss, MDD %s%%, 종목한도 %s%%, 현금바닥 %s%%, "
                "자동매매=%s/%s%s)", settings.engine_interval_sec,
                settings.mdd_limit_pct, settings.max_stock_pct,
                settings.cash_floor_pct, settings.auto_trade_enabled,
                settings.auto_trade_broker,
                "·모의" if settings.kis_paper else "")
    try:
        await asyncio.gather(
            _cycle_loop(redis, sender, toss, kis),
            _guard_loop(redis, sender),       # 목표/손절 실시간 감시(20초)
            command_loop(redis, toss, kis),   # 텔레그램 주문지시(확인 회신 필수)
        )
    finally:
        await kis.aclose()
        await redis.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("engine stopped")
