"""해외 거래소간 가격차 + 펀비 계산 테스트."""
from __future__ import annotations

import asyncio
import time

import fakeredis.aioredis

from api.services.cross import compute_cross, compute_funding
from shared.redis_keys import funding_key, perp_ticker_key, ticker_key
from shared.schemas import TickerSnapshot


def _seed_price(redis, key, coin, price):
    snap = TickerSnapshot(coin=coin, price=price, quote="USDT", ts=time.time())
    return redis.hset(key, coin, snap.model_dump_json())


def test_cross_spot_spread():
    async def run():
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await _seed_price(redis, ticker_key("binance"), "BTC", 100_000)
        await _seed_price(redis, ticker_key("bybit"), "BTC", 100_500)
        await _seed_price(redis, ticker_key("okx"), "BTC", 99_800)

        d = await compute_cross(redis, "BTC", "spot")
        assert d["cheapest"] == "okx" and d["priciest"] == "bybit"
        # (100500/99800 - 1)*100 ≈ 0.7014%
        assert abs(d["spread_pct"] - 0.7014) < 1e-2
        assert d["rows"][0]["exchange"] == "okx"   # 최저가 먼저
        await redis.aclose()

    asyncio.run(run())


def test_cross_perp_uses_perp_key():
    async def run():
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await _seed_price(redis, perp_ticker_key("binance"), "ETH", 4000)
        await _seed_price(redis, perp_ticker_key("bybit"), "ETH", 4020)
        d = await compute_cross(redis, "ETH", "perp")
        assert len(d["rows"]) == 2
        assert d["priciest"] == "bybit"
        await redis.aclose()

    asyncio.run(run())


def test_funding_spread():
    async def run():
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await redis.hset(funding_key("binance"), "BTC", "0.0001")    # 0.01%
        await redis.hset(funding_key("bybit"), "BTC", "-0.0002")     # -0.02%
        d = await compute_funding(redis, "BTC")
        assert d["highest"] == "binance" and d["lowest"] == "bybit"
        # 0.01 - (-0.02) = 0.03 %p
        assert abs(d["spread_pct"] - 0.03) < 1e-6
        await redis.aclose()

    asyncio.run(run())


def test_cross_empty():
    async def run():
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        d = await compute_cross(redis, "BTC", "spot")
        assert d["rows"] == [] and d["spread_pct"] is None
        await redis.aclose()

    asyncio.run(run())
