"""수집기 엔트리포인트 (주식 전용).

한국투자증권(KIS) 관심종목의 현재가(+밸류에이션)와 일봉·배당을 주기적으로 Redis에 적재.
키 미설정이면 비활성(idle). 실행: python -m collector.main
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

import httpx
import redis.asyncio as aioredis

from collector.stock.kis import KISClient, load_watchlist
from shared.redis_keys import (
    STOCK_DIVIDEND_KEY,
    STOCK_QUOTE_KEY,
    stock_ohlcv_key,
)
from shared.redis_store import replace_hash
from shared.settings import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
logger = logging.getLogger("collector")


async def stock_loop(redis: aioredis.Redis) -> None:
    """KIS 관심종목 현재가(+PER/PBR/EPS/BPS) 수집. 키 미설정이면 비활성."""
    kis = KISClient()
    if not kis.enabled:
        logger.info("KIS 미설정 → 주식 수집 비활성 (.env KIS_APP_KEY/SECRET)")
        return
    watch = load_watchlist()
    logger.info("stock collector start: %d종목 (paper=%s)", len(watch), settings.kis_paper)
    while True:
        out: dict[str, str] = {}
        async with httpx.AsyncClient(timeout=10) as client:
            for item in watch:
                try:
                    q = await kis.fetch_price(client, item["code"])
                    out[item["code"]] = json.dumps(
                        {"code": item["code"], "name": item["name"],
                         "ts": time.time(), **q})
                except Exception as exc:
                    logger.warning("[stock %s] 실패: %s", item["code"], exc)
        if out:
            await replace_hash(redis, STOCK_QUOTE_KEY, out)
            logger.info("[stock] %d종목 수집", len(out))
        await asyncio.sleep(settings.stock_interval_sec)


async def stock_history_loop(redis: aioredis.Redis) -> None:
    """관심종목 일봉(시그널용) + 배당(배당주용) — 느린 주기. 키 없으면 비활성."""
    kis = KISClient()
    if not kis.enabled:
        return
    watch = load_watchlist()
    while True:
        async with httpx.AsyncClient(timeout=15) as client:
            divs: dict[str, str] = {}
            for item in watch:
                code = item["code"]
                try:
                    candles = await kis.fetch_daily(client, code)
                    if candles:
                        await redis.set(stock_ohlcv_key(code), json.dumps(candles))
                except Exception as exc:
                    logger.warning("[stock daily %s] 실패: %s", code, exc)
                try:
                    dv = await kis.fetch_dividend(client, code)
                    if dv.get("items"):
                        divs[code] = json.dumps({**dv, "ts": time.time()})
                except Exception as exc:
                    logger.warning("[stock div %s] 실패: %s", code, exc)
            if divs:
                await replace_hash(redis, STOCK_DIVIDEND_KEY, divs)
        logger.info("[stock] 일봉/배당 수집 완료(%d종목)", len(watch))
        await asyncio.sleep(settings.stock_history_interval_sec)


async def main() -> None:
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    logger.info("collector start (stock-only)")
    try:
        await asyncio.gather(
            stock_loop(redis),
            stock_history_loop(redis),
        )
    finally:
        await redis.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("collector stopped")
