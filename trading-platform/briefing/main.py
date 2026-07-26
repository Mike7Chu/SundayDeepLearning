"""주식 일일 브리핑 엔트리포인트 — 스케줄 발송.

실행: python -m briefing.main
시세/시그널/가치/배당을 모아 텔레그램으로 1일 1회 요약 발송(키 없으면 로그만).
"""
from __future__ import annotations

import asyncio
import json
import logging

import redis.asyncio as aioredis

from api.services.stats import summarize
from api.services.stock_dividend import dividend_view
from api.services.stock_signal import signals_for
from api.services.stock_value import value_screener
from briefing.compose import compose_brief, has_content
from collector.stock.kis import load_watchlist
from notifier.telegram import TelegramSender
from shared.redis_keys import (
    AGENT_LAST_KEY,
    ENGINE_PLAN_KEY,
    ENGINE_RISK_KEY,
    JOURNAL_KEY,
    MARKET_INDICATORS_KEY,
    STOCK_QUOTE_KEY,
)
from shared.settings import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
logger = logging.getLogger("briefing")


async def gather(redis: aioredis.Redis) -> tuple[list, list, list, list, list]:
    raw = await redis.hgetall(STOCK_QUOTE_KEY)
    quotes = []
    for v in raw.values():
        try:
            quotes.append(json.loads(v))
        except (json.JSONDecodeError, TypeError):
            continue
    value_rows = (await value_screener(redis)).get("rows", [])
    signal_rows = []
    for w in load_watchlist():
        s = await signals_for(redis, w["code"], w.get("name", ""))
        if s:
            signal_rows.append(s)
    div = await dividend_view(redis, settings.briefing_drip_budget)
    return quotes, value_rows, signal_rows, div.get("rows", []), div.get("drip", [])


async def _jget(redis: aioredis.Redis, key: str) -> dict:
    raw = await redis.get(key)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


async def _extras(redis: aioredis.Redis) -> dict:
    """행동에 가까운 섹션(시장·플랜·자동매매·성적)을 엔진/시장 스냅샷에서 모은다."""
    journal = []
    for raw in await redis.lrange(JOURNAL_KEY, 0, -1):
        try:
            journal.append(json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            continue
    entries = [{"code": j.get("code"), "side": j.get("side"), "price": j.get("price"),
                "qty": j.get("qty"), "ts": j.get("ts")} for j in journal]
    return {
        "market": await _jget(redis, MARKET_INDICATORS_KEY),
        "plan": await _jget(redis, ENGINE_PLAN_KEY),
        "risk": await _jget(redis, ENGINE_RISK_KEY),
        "agent": await _jget(redis, AGENT_LAST_KEY),
        "stats": summarize(entries) if entries else {},
    }


async def run_once(redis: aioredis.Redis, sender: TelegramSender) -> bool:
    quotes, value_rows, signal_rows, div_rows, drip = await gather(redis)
    ex = await _extras(redis)
    has_plan = bool((ex["plan"].get("buys") or ex["plan"].get("sells")))
    if not (has_content(quotes, value_rows, signal_rows, div_rows) or has_plan):
        logger.info("브리핑 생략 — 데이터 없음(KIS 키/수집 대기)")
        return False
    msg = compose_brief(quotes, value_rows, signal_rows, div_rows, drip,
                        market=ex["market"], plan=ex["plan"], risk=ex["risk"],
                        agent=ex["agent"], stats=ex["stats"])
    await sender.send(msg)
    logger.info("브리핑 발송(telegram=%s, %d종목)", sender.enabled, len(quotes))
    return True


async def run() -> None:
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    sender = TelegramSender()
    logger.info("briefing start (interval=%ss, telegram=%s)",
                settings.briefing_interval_sec, sender.enabled)
    try:
        while True:
            try:
                await run_once(redis, sender)
            except Exception as exc:
                logger.warning("브리핑 실패: %s", exc)
            await asyncio.sleep(settings.briefing_interval_sec)
    finally:
        await redis.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("briefing stopped")
