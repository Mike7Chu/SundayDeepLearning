"""주식 일일 브리핑 엔트리포인트 — 스케줄 발송.

실행: python -m briefing.main
시세/시그널/가치/배당을 모아 텔레그램으로 1일 1회 요약 발송(키 없으면 로그만).
"""
from __future__ import annotations

import asyncio
import json
import logging

import redis.asyncio as aioredis

from api.services.stock_dividend import dividend_view
from api.services.stock_signal import signals_for
from api.services.stock_value import value_screener
from briefing.compose import compose_brief, has_content
from collector.stock.kis import load_watchlist
from notifier.telegram import TelegramSender
from shared.redis_keys import STOCK_QUOTE_KEY
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


async def run_once(redis: aioredis.Redis, sender: TelegramSender) -> bool:
    quotes, value_rows, signal_rows, div_rows, drip = await gather(redis)
    if not has_content(quotes, value_rows, signal_rows, div_rows):
        logger.info("브리핑 생략 — 데이터 없음(KIS 키/수집 대기)")
        return False
    msg = compose_brief(quotes, value_rows, signal_rows, div_rows, drip)
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
