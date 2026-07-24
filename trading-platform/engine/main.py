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
from datetime import datetime, timedelta

import httpx
import redis.asyncio as aioredis

from api.services.stock_radar import supply_demand
from api.services.stock_score import compute_score
from api.services.stock_signal import light_pillar, pillar_guide, trade_levels
from api.services.stock_value import load_quotes
from collector.stock.kis import effective_watchlist
from collector.stock.toss import TossClient
from engine.orders import place_gated_order
from engine.plan import (
    entry_decision,
    exit_plan,
    sell_checks,
    stage1_rank,
    suggest_qty,
    swing_metrics,
)
from engine.risk import evaluate_risk
from engine.screener import final_score, quant_filter
from engine.telegram_cmd import command_loop
from notifier.telegram import TelegramSender
from engine.intraday import add_tick, intraday_signal, krx_intraday
from shared.redis_keys import (
    ASSET_HIST_KEY,
    DAY_POS_KEY,
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
    stock_intraday_key,
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


def _paper_auto() -> bool:
    """자동매매가 모의(가짜 돈) 계좌로 도는가 — 모의면 실계좌 기준 현금잠금을 우회.

    자동매매는 KIS 계좌로 나가는데(broker=kis), 리스크 실드의 현금/MDD는 토스
    실계좌 스냅샷 기준이라 모의 리허설을 실계좌 상태가 막는 모순이 생긴다.
    """
    return settings.auto_trade_broker == "kis" and settings.kis_paper


async def _assets(redis: aioredis.Redis) -> tuple[float | None, float | None]:
    """(총자산=평가액+현금, 현금) — 토스 실계좌 기준. 100억 로드맵/스냅샷용."""
    hold = await _json_get(redis, TOSS_HOLDINGS_KEY)
    acc = await _json_get(redis, TOSS_ACCOUNT_KEY)
    ev = hold.get("total_eval")
    cash = acc.get("buying_power")
    if ev is None and cash is None:
        return None, None
    return (ev or 0.0) + (cash or 0.0), cash


async def _trade_assets(redis: aioredis.Redis,
                        kis=None) -> tuple[float | None, float | None]:
    """자동매매 리스크 실드·사이징용 자산 — '실제 주문이 나가는 계좌' 기준.

    모의(KIS_PAPER)면 KIS 모의계좌 잔고를 쓴다(토스 실계좌가 아니라). 그래야 종목당
    한도(5%)·현금바닥·MDD가 리허설 계좌를 반영. 조회 실패 시 (None, None)이며 주문은
    paper 우회로 진행(max_order 사이징). 실전 모드는 토스 실계좌(_assets).
    ※ 100억 로드맵은 항상 토스 실계좌 = _assets(별개).
    """
    if _paper_auto() and kis is not None and getattr(kis, "enabled", False):
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                bal = await kis.fetch_balance(c)
                total, cash = bal.get("total_eval"), bal.get("cash")
                # 미장 리허설이 켜져 있으면 해외(USD) 평가·예수금을 환율 환산해 합산.
                if settings.us_auto_enabled and total is not None:
                    try:
                        ob = await kis.fetch_overseas_balance(c)
                        fx = await _fx_rate(redis)
                        if fx and ob.get("eval"):
                            total += ob["eval"] * fx
                            if cash is not None and ob.get("cash"):
                                cash += ob["cash"] * fx
                    except Exception:
                        pass                          # 해외잔고 실패 → 국내만(무회귀)
            if total is not None:
                return total, cash
        except Exception as exc:
            logger.warning("[DATA_ERROR] KIS 모의잔고 조회 실패: %s", exc)
        return None, None
    return await _assets(redis)


async def _fx_rate(redis: aioredis.Redis) -> float | None:
    """USD/KRW 환율(fx:usdkrw). 없으면 None."""
    raw = await redis.get(FX_USDKRW_KEY)
    if not raw:
        return None
    try:
        return float(json.loads(raw).get("rate") or 0) or None
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


async def _update_risk(redis: aioredis.Redis, sender: TelegramSender,
                       kis=None) -> dict:
    total, cash = await _trade_assets(redis, kis)
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


async def _prev_failed(redis: aioredis.Redis, code: str) -> bool:
    """직전 자동주문이 '거부(ok=False)'였나 — 반복 거부 텔레그램 스팸 억제용."""
    raw = await redis.hget(ENGINE_AUTO_KEY, code)
    if not raw:
        return False
    try:
        return json.loads(raw).get("ok") is False
    except (json.JSONDecodeError, TypeError):
        return False


async def _auto_cooldown(redis: aioredis.Redis, code: str, now: float) -> bool:
    """자동매수 재시도 억제 판정. 직전 주문이 성공이면 7일 잠금(중복매수 금지),
    실패면 auto_retry_sec(짧게)만 대기 — '장시작전' 같은 일시 거부가 7일 잠기지 않게."""
    raw = await redis.hget(ENGINE_AUTO_KEY, code)
    if not raw:
        return False
    try:
        rec = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return False
    cd = settings.auto_trade_cooldown_sec if rec.get("ok") else settings.auto_retry_sec
    return now - (rec.get("ts") or 0) < cd


async def _auto_buy(redis: aioredis.Redis, toss: TossClient, kis,
                    sender: TelegramSender, risk: dict, rows: list[dict]) -> None:
    """자동매수(옵트인): 필터 통과 신규 종목을 추천 매수가에 지정가 주문.

    브로커 = AUTO_TRADE_BROKER(기본 kis — 토스는 수동 매매 전용). 조건:
    AUTO_TRADE_ENABLED + 브로커 매매 플래그 + BUY_LOCK 아님 + 상승추세 +
    미보유 + 쿨다운(7일) 밖. 예산 = min(종목당 5% 한도, 브로커 주문 한도).
    """
    if not settings.auto_trade_enabled:
        return
    if risk.get("buy_lock") and not _paper_auto():   # 모의는 실계좌 잠금 우회
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
        if await _auto_cooldown(redis, code, now):   # 성공=7일 잠금 / 실패=짧게 재시도
            continue
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
        # 하이브리드 진입 — 현재가가 추천가 대비 밴드 초과면 매수 안 함(눌림목 대기·유령주문 방지).
        live = await _live_price(redis, code) or r.get("price")
        dec = entry_decision(entry, live, settings.entry_chase_band_pct)
        if dec is None:
            logger.info("[auto/%s] %s 과확장(현재 %s > 추천 %.0f) — 눌림목 대기",
                        broker, code, f"{live:.0f}" if live else "?", entry)
            continue                              # 쿨다운 안 걸어 다음에 눌리면 매수
        order_price, note = dec
        budget = min(risk.get("per_stock_cap") or max_order, max_order)
        qty = int(budget // order_price)
        if qty < 1:
            continue
        prev_failed = await _prev_failed(redis, code)
        ok, msg = await place_gated_order(redis, side="BUY", code=code,
                                          qty=qty, price=order_price, broker=broker,
                                          kis=kis, toss=toss)
        await redis.hset(ENGINE_AUTO_KEY, code, json.dumps(
            {"ts": now, "ok": ok, "qty": qty, "price": order_price, "broker": broker},
            ensure_ascii=False))
        if not (ok or not prev_failed):           # 반복 거부는 조용히(첫 거부·성공만 알림)
            logger.info("[auto/%s] %s BUY x%d @%.0f → %s(반복거부 무알림)",
                        broker, code, qty, order_price, ok)
            continue
        await sender.send(("🤖 자동매수 " + ("접수 ✅" if ok else "거부 🚫")) +
                          f"\n{r['name']}({code}) {qty}주 @{order_price:,.0f}원 "
                          f"(최종 {r['final']:.0f}점 · {note})\n{msg}\n"
                          f"손절 {r.get('stop') or 0:,.0f} · 목표 {r.get('target') or 0:,.0f}")
        logger.info("[auto/%s] %s BUY x%d @%.0f → %s", broker, code, qty, order_price, ok)


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
    # 상위 후보는 일봉을 온디맨드로 채운다 — 캔들이 없으면 추세·타이밍 축이 0으로
    # 눌려 최종점수가 실제보다 낮게 나와 전부 탈락한다(스윙 플랜과 동일한 보정, 6h 캐시).
    top = scored[:30]
    if toss.enabled:
        async with httpx.AsyncClient(timeout=15) as tc:
            refreshed = []
            for qscore, q, sc, closes in top:
                if len(closes) < 60:
                    try:
                        candles = await toss.fetch_daily_history(tc, q["code"])
                    except Exception:
                        candles = []
                    if candles:
                        await redis.set(stock_ohlcv_key(q["code"]),
                                        json.dumps(candles, ensure_ascii=False), ex=21600)
                        closes = [c["close"] for c in candles
                                  if isinstance(c, dict) and c.get("close")]
                        sc = compute_score(q, closes)
                        qscore = sc["score"]
                refreshed.append((qscore, q, sc, closes))
            refreshed.sort(key=lambda x: x[0], reverse=True)
            top = refreshed

    inv_raw = await redis.hgetall(RESEARCH_INV_KEY)
    now = time.time()
    rows, requested = [], 0
    prev = await _json_get(redis, ENGINE_BUYLIST_KEY)
    prev_codes = {r.get("code") for r in prev.get("rows", [])}
    for qscore, q, sc, closes in top:          # 상위 30개(캔들 보정 후)만 관리
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
    # 자동매수는 매수리스트 '전체'를 대상으로 — 중복은 쿨다운(성공 7일/실패 30분)이 막는다.
    # (신규 여부로 거르면 이미 리스트에 있던 종목이 영영 매수 시도조차 안 됨 = 버그.)
    await _auto_buy(redis, toss, kis, sender, risk, rows)


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
            continue        # 보유 구간은 기록을 지우지 않는다(스탑 근처 진동 시 재알림 스팸 방지)
        stage = ep["stage"]
        try:
            last = json.loads(prev[code]) if code in prev else {}
        except (json.JSONDecodeError, TypeError):
            last = {}
        since = time.time() - (last.get("ts") or 0)
        if last.get("ts"):
            same = last.get("kind") == stage
            # 같은 상태는 24h 1회, 다른 상태(트레일링↔손절 진동 등)도 쿨다운 내 억제.
            if (same and since < 86400) or (not same and since < settings.alert_cooldown_sec):
                continue
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
            risk = await _update_risk(redis, sender, kis)
            await _holdings_alerts(redis, sender)
            await _pillar_scan(redis, sender)
            await _pipeline(redis, sender, risk, toss, kis)
            await _swing_plan(redis, toss, risk, kis, sender)
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


async def _swing_plan(redis: aioredis.Redis, toss: TossClient, risk: dict,
                      kis=None, sender: TelegramSender | None = None) -> None:
    """오늘의 매매 플랜(설문 맞춤: 실적+추세 스윙 · 후보 3개+근거 · 중립 · KR+US).

    1차(실적·52주 위치)로 전 시장에서 상위 40개 → 일봉(없으면 토스 온디맨드,
    6h 캐시)으로 스윙 점수 → 매수 후보 3. 보유는 매도 신호 심각도 상위 3.
    """
    quotes = await load_quotes(redis)
    qmap = {q.get("code"): q for q in quotes if q.get("code")}
    hold = await _json_get(redis, TOSS_HOLDINGS_KEY)
    holdings = hold.get("holdings", [])
    held = {h.get("symbol") for h in holdings if h.get("symbol")}
    asset, _cash = await _trade_assets(redis, kis)   # 사이징도 주문 나가는 계좌 기준
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
    # 미장 자동매매(옵트인): 스윙 상위 미국 후보를 KIS 해외(모의 지원)로 자동매수.
    # 국내=가치(2단계 필터), 미국=모멘텀(스윙) — 전략 분리 유지.
    if kis is not None and sender is not None:
        us_buys = [b for b in buys if b.get("currency") == "USD"]
        await _auto_buy_us(redis, kis, sender, risk, held, us_buys)


async def _auto_buy_us(redis: aioredis.Redis, kis, sender: TelegramSender,
                       risk: dict, held: set, us_buys: list[dict]) -> None:
    """미장 자동매수(옵트인) — 스윙 상위 미국 후보를 KIS 해외주식으로 주문.

    조건: US_AUTO_ENABLED + AUTO_TRADE_ENABLED + BUY_LOCK 아님 + 미보유 +
    쿨다운(7일) 밖. place_gated_order가 브로커 게이트·한도·리스크 실드 재검증.
    """
    if not (settings.auto_trade_enabled and settings.us_auto_enabled):
        return
    if risk.get("buy_lock") and not _paper_auto():   # 모의는 실계좌 잠금 우회
        return
    now = time.time()
    for b in us_buys[:2]:                            # 상위 2개만(과도한 자동주문 억제)
        code, entry = b["code"], b.get("entry")
        qty = b.get("qty")
        if not entry or not qty or qty < 1 or code in held or code.isdigit():
            continue
        if await _auto_cooldown(redis, code, now):   # 성공=7일 잠금 / 실패=짧게 재시도
            continue
        # 하이브리드 진입 — 현재가가 추천가 대비 밴드 초과면 매수 안 함(눌림목 대기).
        dec = entry_decision(entry, b.get("price"), settings.entry_chase_band_pct)
        if dec is None:
            logger.info("[auto/kis-us] %s 과확장(현재 %s > 추천 %.2f) — 눌림목 대기",
                        code, b.get("price"), entry)
            continue
        order_price, note = dec
        if order_price > entry:                      # 진입가 상향 시 예산 유지(수량 재산정)
            qty = int(qty * entry // order_price) or qty
        prev_failed = await _prev_failed(redis, code)
        ok, msg = await place_gated_order(redis, side="BUY", code=code,
                                          qty=qty, price=order_price, broker="kis",
                                          kis=kis, toss=None)
        await redis.hset(ENGINE_AUTO_KEY, code, json.dumps(
            {"ts": now, "ok": ok, "qty": qty, "price": order_price, "broker": "kis-us"},
            ensure_ascii=False))
        logger.info("[auto/kis-us] %s BUY x%s @%.2f → %s", code, qty, order_price, ok)
        if not (ok or not prev_failed):           # 반복 거부는 조용히(첫 거부·성공만 알림)
            continue
        await sender.send(("🌎 미장 자동매수 " + ("접수 ✅" if ok else "거부 🚫")) +
                          f"\n{b.get('name', '')}({code}) {qty:g}주 @${order_price:,.2f} "
                          f"(스윙 {b.get('swing')}점 · {note})\n{msg}\n"
                          f"손절 ${b.get('stop') or 0:,.2f} · 목표 ${b.get('target') or 0:,.2f}")


async def _day_trade_loop(redis: aioredis.Redis, kis, toss: TossClient,
                          sender: TelegramSender) -> None:
    """데이 스윙(분~시간) — 장중 분봉 신호 진입 + 익절/손절/장마감 청산. 옵트인(기본 OFF).

    scalp_experiment면 더 빠른 주기로 같은 로직(초단타 실험 — 실전 금지·모의 전용).
    거래가 잦아 비용에 민감 → 성적표 net(비용 차감)으로만 판단할 것.
    """
    scalp = settings.scalp_experiment
    if not (settings.auto_trade_enabled and (settings.day_trade_enabled or scalp)):
        return
    if scalp and not settings.kis_paper:
        logger.warning("[scalp] 초단타 실험은 모의(KIS_PAPER=true) 전용 — 비활성")
        return
    interval = settings.scalp_interval_sec if scalp else settings.day_trade_interval_sec
    tag = "scalp" if scalp else "day"
    logger.info("[%s] 데이%s 루프 시작(주기 %.0fs)", tag,
                "(초단타 실험)" if scalp else " 스윙", interval)
    while True:
        try:
            now = datetime.utcnow() + timedelta(hours=9)   # KST
            state = krx_intraday(now)
            if state != "closed":
                await _day_cycle(redis, kis, toss, sender, state, scalp, tag)
        except Exception as exc:
            logger.warning("[DATA_ERROR] %s 루프 실패: %s", tag, exc)
        await asyncio.sleep(interval)


async def _day_cycle(redis: aioredis.Redis, kis, toss: TossClient,
                     sender: TelegramSender, state: str, scalp: bool,
                     tag: str) -> None:
    """분봉 갱신 + (진입가능 구간) 신호 진입 + 보유 데이포지션 익절/손절/장마감 청산."""
    watch = await effective_watchlist(redis)
    codes = [w["code"] for w in watch if is_kr_code(w["code"])][:41]
    names = {w["code"]: w.get("name", "") for w in watch}
    pos = await _json_get(redis, DAY_POS_KEY)
    broker = settings.auto_trade_broker
    budget = settings.kis_max_order_krw if broker == "kis" else settings.toss_max_order_krw
    icon = "⚡초단타" if scalp else "📈데이"
    changed = False
    for code in codes:
        live = await _live_price(redis, code)
        if not live or live <= 0:
            continue
        key = stock_intraday_key(code)
        raw = await redis.get(key)
        try:
            bars = json.loads(raw) if raw else []
        except (json.JSONDecodeError, TypeError):
            bars = []
        bars = add_tick(bars, live, time.time(), settings.intraday_bar_sec)
        await redis.set(key, json.dumps(bars), ex=3600)

        if code in pos:                                    # 보유 데이포지션 → 청산 판정
            p = pos[code]
            entry = p.get("entry") or live
            ret = (live / entry - 1) * 100
            reason = None
            if state == "flatten":
                reason = "장마감 정리"
            elif ret >= settings.day_trade_take_pct:
                reason = f"익절 +{ret:.1f}%"
            elif ret <= -settings.day_trade_stop_pct:
                reason = f"손절 {ret:.1f}%"
            if reason:
                ok, msg = await place_gated_order(
                    redis, side="SELL", code=code, qty=p.get("qty") or 1,
                    price=live, broker=broker, kis=kis, toss=toss)
                if ok:
                    pos.pop(code, None)
                    changed = True
                    await sender.send(f"{icon} 청산 {names.get(code, code)}({code}) "
                                      f"{p.get('qty')}주 @{live:,.0f}원 · {reason}\n{msg}")
        elif state == "entry" and len(pos) < settings.day_max_positions:
            sig = intraday_signal(bars)
            if sig.get("action") != "buy":
                continue
            qty = int(budget // live)
            if qty < 1:
                continue
            ok, msg = await place_gated_order(
                redis, side="BUY", code=code, qty=qty, price=live,
                broker=broker, kis=kis, toss=toss)
            if ok:
                pos[code] = {"entry": live, "qty": qty, "ts": time.time()}
                changed = True
                await sender.send(f"{icon} 진입 {names.get(code, code)}({code}) "
                                  f"{qty}주 @{live:,.0f}원 · {sig.get('reason')}\n{msg}")
    if changed:
        await redis.set(DAY_POS_KEY, json.dumps(pos, ensure_ascii=False))


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
            _day_trade_loop(redis, kis, toss, sender),   # 데이 스윙/초단타(옵트인)
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
