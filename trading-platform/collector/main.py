"""수집기 엔트리포인트.

- 모든 거래소 시세를 COLLECT_INTERVAL_SEC 주기로 Redis 해시에 적재.
- 환율을 FX_INTERVAL_SEC 주기로 갱신.
실행: python -m collector.main
"""
from __future__ import annotations

import asyncio
import logging

import redis.asyncio as aioredis

from collector.exchanges.adapter import ExchangeAdapter
from collector.forex import fetch_usdkrw
from shared.redis_keys import FX_USDKRW_KEY, tether_key, ticker_key
from shared.settings import settings
from shared.universe import load_universe

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
logger = logging.getLogger("collector")


async def collect_exchange(redis: aioredis.Redis, adapter: ExchangeAdapter) -> None:
    snapshots = await adapter.fetch()
    if snapshots:
        mapping = {coin: snap.model_dump_json() for coin, snap in snapshots.items()}
        await redis.hset(ticker_key(adapter.cfg.name), mapping=mapping)

    # 국내(KRW) 거래소는 원화 테더가(USDT/KRW)도 수집 → 김프 환산 기준
    tether = None
    if adapter.cfg.region == "domestic":
        tether = await adapter.fetch_price(f"USDT/{adapter.cfg.quote}")
        if tether:
            await redis.set(tether_key(adapter.cfg.name), tether)

    logger.info("[%s] %d/%d coins%s", adapter.cfg.name,
                len(snapshots), len(adapter.coins),
                f" tether={tether:.1f}" if tether else "")


async def ticker_loop(redis: aioredis.Redis, adapters: list[ExchangeAdapter]) -> None:
    while True:
        await asyncio.gather(*[collect_exchange(redis, a) for a in adapters])
        await asyncio.sleep(settings.collect_interval_sec)


async def fx_loop(redis: aioredis.Redis) -> None:
    while True:
        rate = await fetch_usdkrw()
        await redis.set(FX_USDKRW_KEY, rate)
        logger.info("USD/KRW = %.2f", rate)
        await asyncio.sleep(settings.fx_interval_sec)


async def main() -> None:
    universe = load_universe()
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    adapters: list[ExchangeAdapter] = []
    for cfg in universe.exchanges.values():
        try:
            adapters.append(ExchangeAdapter(cfg, universe.coins))
        except Exception as exc:
            # 거래소 하나가 잘못된 ccxt_id 등으로 실패해도 전체 수집은 계속.
            logger.error("거래소 초기화 실패(건너뜀) %s(ccxt_id=%s): %s",
                         cfg.name, cfg.ccxt_id, exc)
    if not adapters:
        raise RuntimeError("초기화된 거래소가 없습니다. config/symbols.yaml 확인")
    logger.info("collector start: %d/%d exchanges, %d coins",
                len(adapters), len(universe.exchanges), len(universe.coins))
    try:
        await asyncio.gather(
            ticker_loop(redis, adapters),
            fx_loop(redis),
        )
    finally:
        await asyncio.gather(*[a.close() for a in adapters],
                             return_exceptions=True)
        await redis.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("collector stopped")
