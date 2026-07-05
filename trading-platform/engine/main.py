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

import redis.asyncio as aioredis

from api.services.stock_score import compute_score
from api.services.stock_signal import trade_levels
from api.services.stock_value import load_quotes
from engine.risk import evaluate_risk
from engine.screener import final_score, quant_filter
from notifier.telegram import TelegramSender
from shared.redis_keys import (
    ENGINE_BUYLIST_KEY,
    ENGINE_PEAK_KEY,
    ENGINE_RISK_KEY,
    RESEARCH_INV_KEY,
    RESEARCH_INV_REQ_KEY,
    TOSS_ACCOUNT_KEY,
    TOSS_HOLDINGS_KEY,
    stock_ohlcv_key,
)
from shared.settings import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("engine")

_INV_FRESH_SEC = 86400.0   # 역방향 감점 유효기간(1일) — 지나면 재검증


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


async def _pipeline(redis: aioredis.Redis, sender: TelegramSender,
                    risk: dict) -> None:
    """2단계 필터 실행 → engine:buylist 저장(+신규 진입 알림)."""
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
        fresh = inv and (now - (inv.get("ts") or 0) < _INV_FRESH_SEC)
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
                          + "\n※ 자동 주문 아님 — 최종 결정은 직접")


async def run() -> None:
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    sender = TelegramSender()
    logger.info("engine start (interval=%ss, MDD %s%%, 종목한도 %s%%, 현금바닥 %s%%)",
                settings.engine_interval_sec, settings.mdd_limit_pct,
                settings.max_stock_pct, settings.cash_floor_pct)
    try:
        while True:
            try:
                risk = await _update_risk(redis, sender)
                await _pipeline(redis, sender, risk)
            except Exception as exc:
                # 어떤 오류도 엔진을 죽이지 않는다 — 기록 후 다음 주기.
                logger.warning("[DATA_ERROR] engine 사이클 실패: %s", exc)
            await asyncio.sleep(settings.engine_interval_sec)
    finally:
        await redis.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("engine stopped")
