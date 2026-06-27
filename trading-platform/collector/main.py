"""수집기 엔트리포인트.

- 모든 거래소 시세를 COLLECT_INTERVAL_SEC 주기로 Redis 해시에 적재.
- 환율을 FX_INTERVAL_SEC 주기로 갱신.
실행: python -m collector.main
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

import httpx
import redis.asyncio as aioredis

from collector.exchanges.adapter import ExchangeAdapter
from collector.exchanges.perp import PerpAdapter
from collector.exchanges.wallet import WalletAdapter
from collector.forex import fetch_usdkrw
from collector.stock.kis import KISClient, load_watchlist
from shared.redis_store import replace_hash
from shared.redis_keys import (
    FX_USDKRW_KEY,
    STOCK_QUOTE_KEY,
    funding_key,
    perp_ticker_key,
    tether_key,
    ticker_key,
    wallet_key,
)
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
        # 통째 교체로 상폐/사라진 코인 stale 제거
        await replace_hash(redis, ticker_key(adapter.cfg.name), mapping)

    # 국내(KRW) 거래소는 원화 테더가(USDT/KRW)도 수집 → 김프 환산 기준
    tether = None
    if adapter.cfg.region == "domestic":
        tether = await adapter.fetch_price(f"USDT/{adapter.cfg.quote}")
        if tether:
            await redis.set(tether_key(adapter.cfg.name), tether)

    logger.info("[%s] %d coins%s", adapter.cfg.name, len(snapshots),
                f" tether={tether:.1f}" if tether else "")


async def ticker_loop(redis: aioredis.Redis, adapters: list[ExchangeAdapter]) -> None:
    while True:
        await asyncio.gather(*[collect_exchange(redis, a) for a in adapters])
        await asyncio.sleep(settings.collect_interval_sec)


async def collect_perp(redis: aioredis.Redis, adapter: PerpAdapter) -> None:
    snaps = await adapter.fetch_tickers()
    if snaps:
        mapping = {c: s.model_dump_json() for c, s in snaps.items()}
        await replace_hash(redis, perp_ticker_key(adapter.cfg.name), mapping)
    funding = await adapter.fetch_funding()
    if funding:
        await replace_hash(redis, funding_key(adapter.cfg.name),
                           {c: json.dumps(v) for c, v in funding.items()})
    if snaps or funding:
        logger.info("[%s perp] %d tickers, %d funding",
                    adapter.cfg.name, len(snaps), len(funding))


async def perp_loop(redis: aioredis.Redis, adapters: list[PerpAdapter]) -> None:
    while True:
        await asyncio.gather(*[collect_perp(redis, a) for a in adapters])
        await asyncio.sleep(settings.collect_interval_sec)


async def collect_wallet(redis: aioredis.Redis, adapter: WalletAdapter) -> None:
    """입출금(입금/출금) 가능여부 — 느린 변화라 별도 주기."""
    states = await adapter.fetch()
    if states:
        await replace_hash(redis, wallet_key(adapter.cfg.name),
                           {c: json.dumps(s) for c, s in states.items()})
        logger.info("[%s wallet] %d coins", adapter.cfg.name, len(states))


async def wallet_loop(redis: aioredis.Redis, adapters: list[WalletAdapter]) -> None:
    while True:
        await asyncio.gather(*[collect_wallet(redis, a) for a in adapters])
        await asyncio.sleep(settings.wallet_interval_sec)


async def stock_loop(redis: aioredis.Redis) -> None:
    """KIS 관심종목 현재가 수집. 키 미설정이면 비활성."""
    kis = KISClient()
    if not kis.enabled:
        logger.info("KIS 미설정 → 주식 수집 비활성 (.env KIS_APP_KEY/SECRET)")
        return
    watch = load_watchlist()
    logger.info("stock collector start: %d종목 (paper=%s)", len(watch), settings.kis_app_key and True)
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
            adapters.append(ExchangeAdapter(cfg, universe.exclude))
        except Exception as exc:
            # 거래소 하나가 잘못된 ccxt_id 등으로 실패해도 전체 수집은 계속.
            logger.error("거래소 초기화 실패(건너뜀) %s(ccxt_id=%s): %s",
                         cfg.name, cfg.ccxt_id, exc)
    if not adapters:
        raise RuntimeError("초기화된 거래소가 없습니다. config/symbols.yaml 확인")

    # 해외 거래소는 무기한선물(perp) 가격+펀비, 입출금 상태도 수집
    perp_adapters: list[PerpAdapter] = []
    wallet_adapters: list[WalletAdapter] = []
    for name in universe.overseas:
        cfg = universe.exchanges[name]
        try:
            perp_adapters.append(PerpAdapter(cfg, universe.exclude))
        except Exception as exc:
            logger.error("perp 초기화 실패(건너뜀) %s(ccxt_id=%s): %s",
                         name, cfg.ccxt_id, exc)
        try:
            wallet_adapters.append(WalletAdapter(cfg))
        except Exception as exc:
            logger.error("wallet 초기화 실패(건너뜀) %s(ccxt_id=%s): %s",
                         name, cfg.ccxt_id, exc)

    logger.info("collector start: %d/%d spot, %d perp, %d wallet",
                len(adapters), len(universe.exchanges),
                len(perp_adapters), len(wallet_adapters))
    try:
        await asyncio.gather(
            ticker_loop(redis, adapters),
            perp_loop(redis, perp_adapters),
            wallet_loop(redis, wallet_adapters),
            fx_loop(redis),
            stock_loop(redis),
        )
    finally:
        await asyncio.gather(
            *[a.close() for a in adapters],
            *[a.close() for a in perp_adapters],
            *[a.close() for a in wallet_adapters],
            return_exceptions=True,
        )
        await redis.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("collector stopped")
