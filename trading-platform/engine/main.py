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
from collector.stock.kis import effective_watchlist, is_derivative_etf, is_kr_code
from collector.stock.kis_ws import kr_movers, pick_subs
from engine.regime import classify_regime
from engine.strategies import DEFAULT_ACTIVE, run_strategies
from collector.stock.toss import TossClient
from engine.orders import place_gated_order
from engine.plan import (
    entry_decision,
    exit_plan,
    sell_checks,
    stage1_rank,
    suggest_qty,
)
from engine.risk import evaluate_risk
from engine.screener import final_score, quant_filter
from engine.telegram_cmd import command_loop
from notifier.telegram import TelegramSender, dashboard_buttons
from engine.intraday import add_tick, intraday_signal, krx_intraday
from engine.agent import (
    build_context,
    due_slots,
    market_of,
    parse_slots,
    tradeable_markets,
)
from shared.redis_keys import (
    AGENT_DONE_KEY,
    AGENT_LAST_KEY,
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
    KIS_ASSET_KEY,
    MARKET_INDICATORS_KEY,
    MARKET_RANKINGS_KEY,
    ENGINE_REGIME_KEY,
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

_DAY_HB: dict[str, float] = {}   # 데이/초단타 하트비트 스로틀(tag → 마지막 로그 ts)


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
                krw_cash, usd_cash = cash, None       # 통화별 예수금(별도 풀)
                # 미장 리허설이 켜져 있으면 해외(USD) 평가·예수금을 환율 환산해 합산.
                if settings.us_auto_enabled and total is not None:
                    try:
                        ob = await kis.fetch_overseas_balance(c)
                        fx = await _fx_rate(redis)
                        usd_cash = ob.get("cash")
                        if fx and ob.get("eval"):
                            total += ob["eval"] * fx
                            if cash is not None and ob.get("cash"):
                                cash += ob["cash"] * fx
                    except Exception:
                        pass                          # 해외잔고 실패 → 국내만(무회귀)
            if total is not None:                     # 정상 조회 → 캐시(일시 실패 대비)
                await redis.set(KIS_ASSET_KEY, json.dumps(
                    {"total": total, "cash": cash, "krw_cash": krw_cash,
                     "usd_cash": usd_cash, "ts": time.time()}))
                return total, cash
        except Exception as exc:
            logger.warning("[DATA_ERROR] KIS 모의잔고 조회 실패: %s", exc)
        cached = await _json_get(redis, KIS_ASSET_KEY)   # 일시 실패 → 마지막 정상값 유지
        if cached.get("total") is not None:
            logger.info("[risk] KIS 잔고 일시 조회 실패 — 캐시값 사용(%s원)",
                        f"{cached['total']:,.0f}")
            return cached.get("total"), cached.get("cash")
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


async def _cap_by_cash(redis: aioredis.Redis, code: str, qty: int,
                       order_price: float) -> int:
    """예수금 통화 풀로 매수 수량 상한 — 국내=원화 예수금, 미국=외화(USD) 예수금.

    한국장·미장은 별도 통화 계좌라 원화로 미국을, 외화로 국내를 살 수 없다(통합증거금
    별도). 해당 통화 예수금이 양수로 확인될 때만 상한 적용 — 모르면(조회 실패) 미적용.
    order_price 통화와 풀 통화가 일치(국내 원, 미국 달러)하므로 그대로 나눔.
    """
    a = await _json_get(redis, KIS_ASSET_KEY)
    pool = a.get("krw_cash") if code.isdigit() else a.get("usd_cash")
    if not pool or pool <= 0 or not order_price:
        return qty                                    # 풀 미확인 → 기존 사이징 유지
    return min(qty, int(pool // order_price))


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
    # 모의모드에서 '자산 데이터 없음'(일시 조회 실패)은 실제 위험이 아니고, 주문도
    # paper 우회로 계속 나가므로 🛑 알람을 보내지 않는다(노이즈). 실계좌·실제 MDD만 알림.
    no_asset = total is None or (total or 0) <= 0
    quiet = _paper_auto() and no_asset
    if risk["buy_lock"] and not prev.get("buy_lock"):
        if quiet:
            logger.info("[risk] 모의 — 자산 조회 일시 실패(알람 생략, 주문은 paper 우회로 계속)")
        else:
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
    exp = await _exposure_frac(redis)                # 국면 비중 오버레이(매수 사이징 곱)
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
        budget = min(risk.get("per_stock_cap") or max_order, max_order) * exp
        qty = int(budget // order_price)
        qty = await _cap_by_cash(redis, code, qty, order_price)  # 원화 예수금 상한
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


async def _stock_flow(redis: aioredis.Redis, kis, client,
                      code: str) -> dict | None:
    """종목별 외국인·기관 5일 순매수(수급) — 30분 캐시. S6 수급 모멘텀 입력.

    KIS 주식현재가 투자자(종목별)로 조회 — 토스 지수 전용 엔드포인트와 달리 개별 종목
    수급을 준다(한국장 급등 선행지표). supply_demand 결과 {net_eok,...}. 국내 6자리만.
    """
    if not (code and code.isdigit()) or not (kis and kis.enabled):
        return None
    ck = f"stock:flow:{code}"
    cached = await redis.get(ck)
    if cached:
        try:
            return json.loads(cached)
        except (json.JSONDecodeError, TypeError):
            pass
    try:
        from api.services.stock_radar import supply_demand
        sd = supply_demand(await kis.fetch_investor_trading(client, code, count=5))
    except Exception as exc:                       # 실패 원인 노출(종목당 30분 throttle)
        hbk = f"flowerr:{code}"
        if time.time() - _DAY_HB.get(hbk, 0) >= 1800:
            _DAY_HB[hbk] = time.time()
            logger.warning("[flow] %s 종목별 수급 조회 실패: %s", code, exc)
        return None
    await redis.set(ck, json.dumps(sd, ensure_ascii=False), ex=1800)
    return sd


async def _exposure_frac(redis: aioredis.Redis) -> float:
    """국면 목표 노출도(0~1) — 매수 사이징 곱. 강세=1.0, 위험회피=0.2. 국면 없으면 1.0.

    국면이 '어떤 전략'뿐 아니라 '얼마나 실을지'까지 조절 — 위험회피장엔 같은 신호라도
    포지션을 줄여 하방을 방어한다(전략 리포트 §04 국면 라우터의 비중 오버레이).
    """
    reg = await _json_get(redis, ENGINE_REGIME_KEY)
    exp = reg.get("exposure_pct")
    return 1.0 if exp is None else max(0.1, min(1.0, exp / 100.0))


async def _quote_price(redis: aioredis.Redis, code: str) -> float | None:
    """저장된 마지막 시세가(신선도 무관) — 청산 판정 폴백용(실시간 끊겨도 관리 지속)."""
    raw = await redis.hget(STOCK_QUOTE_KEY, code)
    if not raw:
        return None
    try:
        p = json.loads(raw).get("price")
    except (json.JSONDecodeError, TypeError):
        return None
    return float(p) if p and p > 0 else None


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


async def _update_regime(redis: aioredis.Redis, toss: TossClient) -> dict:
    """시황 4국면 판정(전략 라우터 입력) → 저장·로그.

    코스피 200일선·변동성·외국인 수급으로 강세추세/중립/횡보/위험회피를 분류하고,
    각 국면에 맞는 전략 ID(S1~S5)를 낸다. 지수 일봉은 토스 지표 캔들에서 조회.
    """
    def _closes(cs):
        rows = sorted((c for c in (cs or []) if c.get("close") and c.get("date")),
                      key=lambda c: str(c["date"]))
        return [c["close"] for c in rows]

    # 지수 일봉 = KODEX 200(069500) 일봉(코스피200 추종 프록시). 토스 KOSPI 지표 캔들은
    # count=230에 invalid-request라 사용 불가 → 일반 종목 일봉 조회(200봉+ 안정적)로 대체.
    closes: list[float] = []
    src = "-"
    if toss and toss.enabled:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                closes = _closes(await toss.fetch_daily_history(client, "069500"))
            src = f"KODEX200({len(closes)})"
        except Exception as exc:
            logger.warning("[regime] 지수 프록시(069500) 일봉 실패: %s", exc)
    ind = await _json_get(redis, MARKET_INDICATORS_KEY)
    inv = (ind.get("investor") or {}).get("kospi") or {}
    reg = classify_regime(closes, foreign_net_eok=inv.get("foreigner"))
    reg["ts"] = time.time()
    await redis.set(ENGINE_REGIME_KEY, json.dumps(reg, ensure_ascii=False))
    logger.info("[regime] %s(%s) · 활성전략 %s · 지수봉 %s · %s",
                reg["label"], reg["posture"], "+".join(reg["strategies"]), src,
                " · ".join(reg["reasons"][:2]))
    return reg


async def _cycle_loop(redis: aioredis.Redis, sender: TelegramSender,
                      toss: TossClient, kis) -> None:
    while True:
        try:
            risk = await _update_risk(redis, sender, kis)
            await _update_regime(redis, toss)
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

    # 시황 라우터: 이 국면에 켜진 전략만 후보를 고른다(전략 리포트 §04).
    regime = await _json_get(redis, ENGINE_REGIME_KEY)
    active = regime.get("strategies") or DEFAULT_ACTIVE
    stage1 = stage1_rank(quotes, held, top=40)
    s1_kr = sum(1 for q in stage1 if str(q.get("code", "")).isdigit())
    kr_diag: list[str] = []                       # 국내 후보 탈락 진단(봉수·수급·전략결과)
    buys: list[dict] = []
    async with httpx.AsyncClient(timeout=15) as client:
        for q in stage1:
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
            if is_derivative_etf(q.get("name", "")):     # 레버리지·인버스·ETN 자동매매 제외
                continue
            if "S6" in active and kis and kis.enabled:   # 수급 모멘텀 활성 → 종목별 수급 주입
                fl = await _stock_flow(redis, kis, client, code)
                if fl and fl.get("net_eok") is not None:
                    q["flow_net_eok"] = fl.get("net_eok")
                    q["flow_foreign_eok"] = fl.get("foreign_eok")
            pick = run_strategies(active, q, candles)     # 국면 활성 전략 → 최고 픽
            if code.isdigit() and len(kr_diag) < 8:       # 국내 탈락 사유 진단(봉·수급·결과)
                fnet = q.get("flow_net_eok")
                kr_diag.append(
                    f"{(q.get('name') or code)[:6]}(봉{len(closes)}"
                    + (f"·수급{fnet:+.0f}억" if fnet is not None else "·수급-")
                    + (f"·✅{pick['strategy']}{pick['score']:.0f})" if pick else "·✗)"))
            if not pick:
                continue
            kr = code.isdigit()
            qty = suggest_qty(pick.get("entry") or q.get("price") or 0, asset,
                              risk.get("per_stock_cap"), fx=fx, usd=not kr)
            buys.append({"code": code, "name": q.get("name", ""),
                         "price": q.get("price"), "currency": q.get("currency", "KRW"),
                         "swing": pick["score"], "strategy": pick["strategy"],
                         "strategy_label": pick["label"], "reasons": pick["reasons"],
                         "entry": pick.get("entry"), "stop": pick.get("stop"),
                         "target": pick.get("target"), "qty": qty})
    buys.sort(key=lambda b: b["swing"], reverse=True)
    # 확신 하한: 스윙 미달 후보는 버린다(슬롯 강제충전 금지 — 미달이면 그 슬롯은 현금).
    n_all = len(buys)
    buys = [b for b in buys if b.get("swing", 0) >= settings.plan_min_swing]
    if n_all and not buys:
        logger.info("[plan] 확신 하한(%.0f) 미달 — 매수 후보 없음(현금 보유)",
                    settings.plan_min_swing)
    # 국내·미국 각각 스윙 상위 N개 확보 → 국장/미장 슬롯이 각자 매매할 후보를 갖게 한다.
    # (전엔 swing 순 상위 3만 담아 미국 모멘텀주가 독식 → 국장 슬롯이 늘 빈손이었음.)
    kr_b = [b for b in buys if b.get("currency") != "USD"][:settings.plan_kr_buys]
    us_b = [b for b in buys if b.get("currency") == "USD"][:settings.plan_us_buys]
    plan_buys = sorted(kr_b + us_b, key=lambda b: b["swing"], reverse=True)

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

    regime = await _json_get(redis, ENGINE_REGIME_KEY)   # 시황 라우터(별 루프서 갱신)
    await redis.set(ENGINE_PLAN_KEY, json.dumps(
        {"style": "실적+추세 스윙 · 중립 리스크 · 국내 전체+미국",
         "buys": plan_buys, "sells": sells[:3], "regime": regime, "ts": time.time()},
        ensure_ascii=False))
    buys_kr = sum(1 for b in buys if b.get("currency") != "USD")
    logger.info("[plan] 매수 후보 %d(국내 %d·미국 %d, 검증 %d) · 매도 점검 %d "
                "| 국내 흐름: stage1 %d → 전략통과 %d(전체 후보 %d중)",
                len(plan_buys), len(kr_b), len(us_b), len(buys), min(3, len(sells)),
                s1_kr, buys_kr, len(stage1))
    if s1_kr and not buys_kr and kr_diag:         # 국내가 stage1은 통과했는데 전략서 전멸
        logger.info("[plan] 국내 탈락 상세: %s", " · ".join(kr_diag))
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
    fx = await _fx_rate(redis)                        # USD→KRW (주문 한도 원화 환산용)
    max_order = settings.kis_max_order_krw
    exp = await _exposure_frac(redis)                # 국면 비중 오버레이(매수 사이징 곱)
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
        # 주문 한도(원)에 맞춰 수량 축소 — 국내 _auto_buy와 동일 규율. 미장은 고가주가
        # 많아 고정수량이면 1주도 10만원 한도를 넘어 place_gated_order가 전량 거부하던 버그.
        if fx:
            budget_krw = min(risk.get("per_stock_cap") or max_order, max_order) * exp
            qty_cap = int((budget_krw / fx) // order_price)
            if qty_cap < 1:                          # 1주도 한도 초과 → 조용히 스킵(첫 회만 로그)
                if not await _prev_failed(redis, code):
                    logger.info("[auto/kis-us] %s 1주 $%.2f≈%.0f원 > 한도 %.0f원 — 스킵"
                                "(KIS_MAX_ORDER_KRW 상향 필요)", code, order_price,
                                order_price * fx, max_order)
                await redis.hset(ENGINE_AUTO_KEY, code, json.dumps(
                    {"ts": now, "ok": False, "qty": 0, "price": order_price,
                     "broker": "kis-us", "reason": "한도초과"}, ensure_ascii=False))
                continue
            qty = min(qty, qty_cap)                   # 한도 내 최대 수량
        elif order_price > entry:                    # FX 미확보 시 기존 예산 유지 로직
            qty = int(qty * entry // order_price) or qty
        qty = await _cap_by_cash(redis, code, qty, order_price)  # 외화(USD) 예수금 상한
        if qty < 1:                                   # 외화 예수금 부족 → 스킵
            if not await _prev_failed(redis, code):
                logger.info("[auto/kis-us] %s 외화 예수금 부족 — 스킵(미장은 USD 예수금 필요)", code)
            await redis.hset(ENGINE_AUTO_KEY, code, json.dumps(
                {"ts": now, "ok": False, "qty": 0, "price": order_price,
                 "broker": "kis-us", "reason": "외화예수금부족"}, ensure_ascii=False))
            continue
        prev_failed = await _prev_failed(redis, code)
        ok, msg = await place_gated_order(redis, side="BUY", code=code,
                                          qty=qty, price=order_price, broker="kis",
                                          kis=kis, toss=None)
        await redis.hset(ENGINE_AUTO_KEY, code, json.dumps(
            {"ts": now, "ok": ok, "qty": qty, "price": order_price, "broker": "kis-us"},
            ensure_ascii=False))
        logger.info("[auto/kis-us] %s BUY x%s @%.2f → %s | %s", code, qty,
                    order_price, ok, msg)            # 거부 사유(msg)까지 남겨 진단 가능
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
            else:                                          # 장외에도 생존 하트비트(30분 1회)
                nt = time.time()
                if nt - _DAY_HB.get(tag + ":closed", 0) >= 1800:
                    _DAY_HB[tag + ":closed"] = nt
                    logger.info("[%s] 장 마감/장외 — 대기 중(국장 평일 09:05~15:15 KST에 진입)",
                                tag)
        except Exception as exc:
            logger.warning("[DATA_ERROR] %s 루프 실패: %s", tag, exc)
        await asyncio.sleep(interval)


async def _day_cycle(redis: aioredis.Redis, kis, toss: TossClient,
                     sender: TelegramSender, state: str, scalp: bool,
                     tag: str) -> None:
    """분봉 갱신 + (진입가능 구간) 신호 진입 + 보유 데이포지션 익절/손절/장마감 청산.

    스캔 유니버스 = 데이포지션(청산 필수) → 급등주(전 시장 랭킹) → 관심종목(국내 6자리,
    상한 41). 정적 관심종목만 보던 걸 넘어 '오늘 시장에서 움직이는' 종목까지 평가한다.
    웹소켓 등록 대상과 동일 집합이라 급등주도 실시간 1분봉을 받는다.
    """
    watch = await effective_watchlist(redis)
    names = {w["code"]: w.get("name", "") for w in watch}
    pos = await _json_get(redis, DAY_POS_KEY)
    rk = await _json_get(redis, MARKET_RANKINGS_KEY)     # 전 시장 급등·거래대금 상위
    movers = kr_movers(rk)
    for key in ("kr_gainers", "kr_amount"):              # 급등주 이름 매핑(로그·알림용)
        for r in rk.get(key, []) or []:
            if r.get("symbol"):
                names.setdefault(r["symbol"], r.get("name", ""))
    priority = [c for c in pos if is_kr_code(c)]         # 데이포지션 최우선(청산 감시)
    codes = pick_subs([w["code"] for w in watch], priority, movers)
    broker = settings.auto_trade_broker
    max_order = settings.kis_max_order_krw if broker == "kis" else settings.toss_max_order_krw
    # 사이징 상한을 게이트(단일 종목 한도=자산 5%)와 일치시킨다. 안 맞추면 KIS_MAX_ORDER_KRW
    # 로 잡은 수량의 est가 per_stock_cap을 넘겨 order_allowed가 전량 '한도 초과' 거부한다.
    risk = await _json_get(redis, ENGINE_RISK_KEY)
    budget = min(risk.get("per_stock_cap") or max_order, max_order)
    # 신규진입 정지 조건: ①초단타 강등(intraday_entry_enabled=false, 기본) ②위험회피장
    # 스탠드다운. 어느 쪽이든 새 매수는 막고 보유 청산은 계속한다.
    regime = await _json_get(redis, ENGINE_REGIME_KEY)
    demoted = not settings.intraday_entry_enabled
    stand_down = demoted or (settings.day_skip_risk_off
                             and regime.get("regime") == "risk_off")
    icon = "⚡초단타" if scalp else "📈데이"
    changed = False
    n_live = n_ready = n_buy = n_fill = 0                   # 진단 카운터(신호·체결 분리)
    miss = ""                                              # 대표 '미진입 사유'(신호 없음)
    order_miss = ""                                        # 대표 '미체결 사유'(신호는 났는데 주문 거부)

    # ── 방어 1) 보유 데이포지션 청산 — 스캔 유니버스·실시간 신선도와 '무관하게' 매 사이클
    # 전량 점검한다. 실시간가가 끊겨도(웹소켓 이탈) 저장 시세→진입가로 폴백해 익절/손절/
    # 장마감 정리가 항상 평가되게 한다(예전엔 실시간가 없으면 관리 자체가 스킵돼 묶였음).
    for code, p in list(pos.items()):
        px = (await _live_price(redis, code) or await _quote_price(redis, code)
              or p.get("entry"))
        if not px or px <= 0:
            continue
        entry = p.get("entry") or px
        ret = (px / entry - 1) * 100
        reason = None
        if state == "flatten":
            reason = "장마감 정리"
        elif ret >= settings.day_trade_take_pct:
            reason = f"익절 +{ret:.1f}%"
        elif ret <= -settings.day_trade_stop_pct:
            reason = f"손절 {ret:.1f}%"
        if not reason:
            continue
        ok, msg = await place_gated_order(
            redis, side="SELL", code=code, qty=p.get("qty") or 1,
            price=px, broker=broker, kis=kis, toss=toss)
        nm = names.get(code, code)
        if ok:
            pos.pop(code, None)
            changed = True
            await sender.send(f"{icon} 청산 {nm}({code}) {p.get('qty')}주 "
                              f"@{px:,.0f}원 · {reason}\n{msg}")
        elif any(k in (msg or "") for k in                 # 방어 2a) 계좌에 없는 유령 포지션
                 ("잔고내역이 없", "잔고가 없", "보유하고 있지 않", "매도가능수량")):
            # KIS가 '보유 없음'으로 거부 → 이미 팔렸거나 미체결/리셋. 추적만 제거(스팸 중단).
            pos.pop(code, None)
            changed = True
            logger.warning("[%s] %s(%s) 모의잔고 없음 — 유령 포지션 정리(추적 제거): %s",
                           tag, nm, code, msg)
            hbk = f"{tag}:phantom:{code}"
            if time.time() - _DAY_HB.get(hbk, 0) >= 86400:  # 종목당 하루 1회만 안내
                _DAY_HB[hbk] = time.time()
                await sender.send(f"🧹 {icon} 추적 정리 {nm}({code}) — 모의계좌에 잔고가 "
                                  f"없어 포지션 추적만 제거했습니다(이미 매도/미체결). 재시도 중단.")
        else:                                              # 방어 2b) 그 외 실패 알림(쓰로틀)
            logger.warning("[%s] 매도 실패 %s(%s) %s: %s — 재시도 예정",
                           tag, nm, code, reason, msg)
            hbk = f"{tag}:sellfail:{code}"
            if time.time() - _DAY_HB.get(hbk, 0) >= 600:
                _DAY_HB[hbk] = time.time()
                await sender.send(f"⚠️ {icon} 매도 실패 {nm}({code}) · {reason}\n{msg}\n"
                                  f"포지션이 묶였습니다 — 재시도 중이나 수동 확인 권장.")

    for code in codes:
        live = await _live_price(redis, code)
        if not live or live <= 0:
            continue
        n_live += 1                                        # 실시간가 확보 종목
        key = stock_intraday_key(code)
        raw = await redis.get(key)
        try:
            bars = json.loads(raw) if raw else []
        except (json.JSONDecodeError, TypeError):
            bars = []
        bars = add_tick(bars, live, time.time(), settings.intraday_bar_sec)
        await redis.set(key, json.dumps(bars), ex=3600)
        if len([b for b in bars if b.get("c")]) > 20:      # 신호 평가 가능(분봉 21+)
            n_ready += 1

        if code in pos:                                    # 이미 보유 → 청산은 위 방어 패스가 담당
            continue
        if state == "entry" and not stand_down and len(pos) < settings.day_max_positions:
            if is_derivative_etf(names.get(code, "")):     # 레버리지·인버스 신규진입 금지
                order_miss = order_miss or f"{names.get(code, code)}: 파생ETF 제외"
                continue
            if live < settings.day_min_price_krw:          # 동전주/저가 펌핑주 제외
                order_miss = order_miss or f"{names.get(code, code)}: 저가주 제외(<{settings.day_min_price_krw:,.0f})"
                continue
            sig = intraday_signal(bars, require_vsurge=settings.day_require_vsurge)
            if sig.get("action") != "buy":
                miss = miss or sig.get("reason")           # 대표 사유(첫 종목)
                continue
            n_buy += 1                                     # 매수 신호 발생
            qty = int(budget // live)
            if qty < 1:                                    # 예산<주가 → 1주도 못 삼
                order_miss = order_miss or (
                    f"{names.get(code, code)} 주가 {live:,.0f}원>예산 {budget:,.0f}원")
                continue
            ok, msg = await place_gated_order(
                redis, side="BUY", code=code, qty=qty, price=live,
                broker=broker, kis=kis, toss=toss)
            if not ok:
                order_miss = order_miss or f"{names.get(code, code)}: {msg}"
                continue
            n_fill += 1
            pos[code] = {"entry": live, "qty": qty, "ts": time.time()}
            changed = True
            await sender.send(f"{icon} 진입 {names.get(code, code)}({code}) "
                              f"{qty}주 @{live:,.0f}원 · {sig.get('reason')}\n{msg}")
    if changed:
        await redis.set(DAY_POS_KEY, json.dumps(pos, ensure_ascii=False))
    # 진단 하트비트(≈5분 1회) — 왜 안 사는지 관찰 가능하게. 신호/체결을 분리해서,
    # '신호는 났는데 왜 주문이 안 됐나'(예산·한도·리스크실드)를 미체결 사유로 노출한다.
    now_t = time.time()
    if now_t - _DAY_HB.get(tag, 0) >= 300:
        _DAY_HB[tag] = now_t
        if demoted:                             # 초단타 강등 — 신규진입 OFF(청산만)
            tail = " · 강등(초단타 신규진입 OFF · INTRADAY_ENTRY_ENABLED=true로 옵트인)"
        elif stand_down:                        # 위험회피장 → 신규진입 정지(청산만)
            tail = " · 스탠드다운(위험회피장 — 신규진입 정지, 청산만)"
        elif n_buy and not n_fill:              # 신호는 났는데 한 건도 체결 안 됨 → 이유
            tail = " · 미체결:" + (order_miss or "사유불명")
        elif not n_buy and miss:                # 신호 자체가 없음 → 조건 미충족 이유
            tail = " · 미진입:" + miss
        else:
            tail = ""
        logger.info("[%s] %s 하트비트 — 국내 %d종목 · 실시간가 %d · 분봉충분 %d · "
                    "매수신호 %d · 체결 %d · 보유 %d%s", tag, state, len(codes), n_live,
                    n_ready, n_buy, n_fill, len(pos), tail)


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


# ── 클로드 스윙 결정 에이전트(완전 위임 · 게이트는 집행 안전벽) ──────────────
async def _agent_buy(redis: aioredis.Redis, kis, row: dict, risk: dict,
                     fx: float | None, now: float) -> tuple:
    """에이전트 매수 1건 — 하이브리드 진입가 + 한도 내 수량으로 게이트 주문.

    수량·가격·한도는 엔진이 산정(안전). 반환 (ok|None, msg, note). None=스킵(쿨다운·과확장·수량0).
    """
    code, entry = row["code"], row.get("entry")
    if not entry:
        return None, "", ""
    if await _auto_cooldown(redis, code, now):
        return None, "쿨다운", ""
    live = await _live_price(redis, code) or row.get("price")
    dec = entry_decision(entry, live, settings.entry_chase_band_pct)
    if dec is None:
        logger.info("[agent] %s 과확장(현재 %s>추천 %.2f) — 눌림목 대기", code, live, entry)
        return None, "과확장", ""
    order_price, note = dec
    max_order = settings.kis_max_order_krw
    exp = await _exposure_frac(redis)                    # 국면 비중 오버레이(매수 사이징 곱)
    budget = min(risk.get("per_stock_cap") or max_order, max_order) * exp
    if code.isdigit():                                   # 국내(원)
        qty = int(budget // order_price)
    elif fx:                                             # 미국(USD, 한도 원화 환산)
        qty = int((budget / fx) // order_price)
    else:
        return None, "환율 미확보", ""
    qty = await _cap_by_cash(redis, code, qty, order_price)   # 통화별 예수금(원화/외화) 상한
    if qty < 1:
        return None, ("외화 예수금 부족" if not code.isdigit() else "예수금·한도 부족"), ""
    ok, msg = await place_gated_order(redis, side="BUY", code=code, qty=qty,
                                      price=order_price, broker="kis", kis=kis, toss=None)
    await redis.hset(ENGINE_AUTO_KEY, code, json.dumps(
        {"ts": now, "ok": ok, "qty": qty, "price": order_price, "broker": "agent"},
        ensure_ascii=False))
    cur = "$" if not code.isdigit() else ""
    return ok, msg, f"{qty:g}주 @{cur}{order_price:g}({note})"


async def _agent_sell(redis: aioredis.Redis, kis, code: str, hold: dict,
                      want_qty) -> tuple:
    """에이전트 매도 1건 — 보유 수량 내에서 실시간가 지정가 게이트 주문.

    모의(KIS_PAPER)에선 보유 스냅샷이 토스 실계좌 기준이라, 모의계좌에 없는 종목은
    KIS가 rt_cd로 거부(무해). 반환 (ok|None, msg, note).
    """
    held_qty = hold.get("quantity") or 0
    if held_qty < 1:
        return None, "미보유", ""
    qty = min(int(want_qty), int(held_qty)) if want_qty else int(held_qty)
    if qty < 1:
        return None, "수량0", ""
    live = await _live_price(redis, code) or hold.get("last_price") or hold.get("price")
    if not live or live <= 0:
        return None, "시세 없음", ""
    ok, msg = await place_gated_order(redis, side="SELL", code=code, qty=qty,
                                      price=live, broker="kis", kis=kis, toss=None)
    cur = "$" if not code.isdigit() else ""
    return ok, msg, f"{qty:g}주 @{cur}{live:g}"


async def _agent_run(redis: aioredis.Redis, kis,
                     sender: TelegramSender, slot: str, now_hm: str) -> None:
    """스윙 결정 1회 — 플랜(후보/매도점검)+보유+리스크를 클로드에게 넘겨 최종 판정·집행.

    안전: 매수는 후보 목록 안 종목만, 매도는 KIS 계좌 보유만(환각·임의주문 차단).
    보유는 **KIS 계좌**(토스 실계좌 미사용 — 절대 건들지 않음). 실계좌 주문은
    agent_live_enabled=true라야 — 기본 모의 전용. 열린 시장만 매매(국장/미장 분리).
    """
    if not settings.kis_paper and not settings.agent_live_enabled:
        logger.warning("[agent] 실계좌 모드 — agent_live_enabled=false라 스킵(모의 전용)")
        return
    markets = tradeable_markets(now_hm)              # 지금 열린 시장(KR/US)만 매매
    if not markets:
        logger.info("[agent/%s] 국장·미장 모두 장외 — 스킵", slot)
        return
    plan = await _json_get(redis, ENGINE_PLAN_KEY)
    risk = await _json_get(redis, ENGINE_RISK_KEY)
    # 보유 = KIS 계좌(종목별) — 토스 실계좌는 조회조차 하지 않는다(사용자 요구).
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            positions = await kis.fetch_positions(c)
    except Exception as exc:
        logger.warning("[agent] KIS 보유 조회 실패: %s", exc)
        positions = []
    # 열린 시장으로 후보·보유 좁히기 → 클로드가 그 시장만 판단(국장 슬롯=국장만)
    buys = [b for b in (plan.get("buys") or []) if market_of(b.get("code", "")) in markets]
    sells = [s for s in (plan.get("sells") or []) if market_of(s.get("code", "")) in markets]
    holdings = [p for p in positions if market_of(p.get("symbol", "")) in markets]
    if not (buys or holdings):
        logger.info("[agent/%s] %s 시장 후보·보유 없음 — 스킵", slot, markets)
        return
    plan = {**plan, "buys": buys, "sells": sells}
    asset, _cash = await _trade_assets(redis, kis)
    ctx = build_context(plan, holdings, risk, asset, risk.get("cash_pct"),
                        plan.get("style") or "", regime=plan.get("regime"))
    from research.analyst import Analyst
    result = await Analyst().decide(ctx)
    decisions = result.get("decisions", [])
    mv = result.get("market_view", "")
    logger.info("[agent/%s] 결정 %d건 · 시장뷰: %s", slot, len(decisions), mv[:80])

    buy_rows = {b["code"]: b for b in (plan.get("buys") or []) if b.get("code")}
    held = {h.get("symbol"): h for h in holdings if h.get("symbol")}
    fx = await _fx_rate(redis)
    now = time.time()
    acted: list[str] = []
    touched: list[tuple] = []                            # (code, name) — 종목 상세 버튼용
    buys_done = 0
    for d in decisions:
        act, code = d["action"], d["code"]
        reason = (d.get("reason") or "")[:70]
        if act == "BUY":
            if buys_done >= settings.agent_max_buys:
                continue
            row = buy_rows.get(code)
            if not row:                                  # 후보 밖 매수 = 환각 → 차단
                logger.info("[agent] BUY %s 후보 목록 밖 — 무시", code)
                continue
            if code in held:
                continue
            if risk.get("buy_lock") and not _paper_auto():
                continue
            ok, msg, note = await _agent_buy(redis, kis, row, risk, fx, now)
            if ok is None:
                continue
            acted.append(f"🟢 매수 {'✅' if ok else '🚫'} {row.get('name')}({code}) "
                         f"{note} — {reason}\n   {msg}")
            touched.append((code, row.get("name")))
            if ok:
                buys_done += 1
        elif act == "SELL":
            h = held.get(code)
            if not h:
                logger.info("[agent] SELL %s 미보유 — 무시", code)
                continue
            ok, msg, note = await _agent_sell(redis, kis, code, h, d.get("qty"))
            if ok is None:
                continue
            acted.append(f"🔴 매도 {'✅' if ok else '🚫'} {h.get('name') or code}({code}) "
                         f"{note} — {reason}\n   {msg}")
            touched.append((code, h.get("name") or code))
        # HOLD → 행동 없음

    await redis.set(AGENT_LAST_KEY, json.dumps(
        {"slot": slot, "ts": now, "market_view": mv, "n": len(decisions),
         "acted": len(acted), "paper": settings.kis_paper}, ensure_ascii=False))
    tag = "·모의" if settings.kis_paper else "·실계좌"
    if acted:
        body = "\n".join(acted)
    elif decisions:                                  # 검토는 했고 전부 HOLD
        body = (f"관망 — 후보 {len(buys)}·보유 {len(holdings)} 검토, "
                f"{len(decisions)}건 모두 HOLD(확신 부족/현금 보유)")
    else:                                            # 결정 0건 — 파싱 실패 가능
        body = (f"관망 — 후보 {len(buys)}·보유 {len(holdings)} 검토, 신규 매매 없음"
                + ("" if mv else " · ⚠️ 판정 파싱 실패(엔진 로그 확인)"))
    from notifier.telegram import stock_buttons
    btns = (stock_buttons(touched) or []) + (dashboard_buttons() or [])  # 매매 종목 상세 + 대시보드
    await sender.send(f"🤖 스윙 에이전트 판정({slot}{tag}) — 완전위임\n"
                      f"📊 {mv or '시장뷰 없음'}\n\n{body}",
                      buttons=btns or None)


async def _agent_loop(redis: aioredis.Redis, kis,
                      sender: TelegramSender) -> None:
    """하루 N회(agent_times, KST) 클로드 스윙 결정 실행. 기본 OFF(agent_enabled).

    슬롯은 국장(예 09:40)·미장(예 23:40) 시간에 두면 각 슬롯이 열린 시장만 매매.
    토스 실계좌는 사용하지 않는다(보유는 KIS 계좌).
    """
    if not settings.agent_enabled:
        return
    slots = parse_slots(settings.agent_times)
    if not slots:
        logger.warning("[agent] agent_times 파싱 실패(%s) — 비활성", settings.agent_times)
        return
    logger.info("[agent] 스윙 결정 에이전트 시작 — 하루 %d회 %s(KST)%s",
                len(slots), slots, " ·모의" if settings.kis_paper else " ·실계좌")
    while True:
        try:
            now = datetime.utcnow() + timedelta(hours=9)          # KST
            today, now_hm = now.strftime("%Y-%m-%d"), now.strftime("%H:%M")
            fired = await redis.hgetall(AGENT_DONE_KEY)   # 해시 {slot: 날짜}(GET 아님)
            for slot in due_slots(now_hm, slots, fired, today):
                await redis.hset(AGENT_DONE_KEY, slot, today)      # 먼저 마킹(중복 방지)
                await _agent_run(redis, kis, sender, slot, now_hm)
        except Exception as exc:
            logger.warning("[DATA_ERROR] agent 루프 실패: %s", exc)
        await asyncio.sleep(settings.agent_check_interval_sec)


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
            _agent_loop(redis, kis, sender),              # 클로드 스윙 결정(하루 N회·옵트인)
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
