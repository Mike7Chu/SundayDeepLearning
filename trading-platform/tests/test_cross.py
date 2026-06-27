"""해외 거래소간 가격차 + 펀비(정산주기/APY) + 매트릭스 + 아비트라지 테스트."""
from __future__ import annotations

import asyncio
import json
import time

import fakeredis.aioredis

from api.services.arbitrage import compute_arbitrage
from api.services.cross import (
    compute_cross,
    compute_funding,
    compute_funding_matrix,
)
from shared.redis_keys import funding_key, perp_ticker_key, ticker_key, wallet_key
from shared.schemas import TickerSnapshot
from shared.symbols import is_leveraged_token, parse_symbol


def _seed_price(redis, key, coin, price):
    snap = TickerSnapshot(coin=coin, price=price, quote="USDT", ts=time.time())
    return redis.hset(key, coin, snap.model_dump_json())


def _seed_funding(redis, ex, coin, rate, interval_h=8.0, next_ts=None):
    return redis.hset(funding_key(ex), coin,
                      json.dumps({"rate": rate, "interval_h": interval_h, "next_ts": next_ts}))


def test_symbols_helpers():
    assert parse_symbol("BTC/USDT") == ("BTC", "USDT")
    assert parse_symbol("BTC/USDT:USDT") == ("BTC", "USDT")
    assert is_leveraged_token("BTC3L") and not is_leveraged_token("BTC")
    assert not is_leveraged_token("JUP")   # UP 접미사 오탐 없어야


def test_cross_spot_spread():
    async def run():
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await _seed_price(redis, ticker_key("binance"), "BTC", 100_000)
        await _seed_price(redis, ticker_key("bybit"), "BTC", 100_500)
        await _seed_price(redis, ticker_key("okx"), "BTC", 99_800)
        d = await compute_cross(redis, "BTC", "spot")
        assert d["cheapest"] == "okx" and d["priciest"] == "bybit"
        assert abs(d["spread_pct"] - 0.7014) < 1e-2
        await redis.aclose()

    asyncio.run(run())


def test_funding_apy_and_interval():
    async def run():
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        # 0.01%/8h → APY = 0.0001 * (24/8)*365 *100 = 10.95%
        await _seed_funding(redis, "binance", "BTC", 0.0001, 8.0)
        # 0.01%/1h → APY = 0.0001 * 24*365 *100 = 87.6%
        await _seed_funding(redis, "bybit", "BTC", 0.0001, 1.0)
        d = await compute_funding(redis, "BTC")
        by = {r["exchange"]: r for r in d["rows"]}
        assert abs(by["binance"]["apy"] - 10.95) < 1e-2
        assert abs(by["bybit"]["apy"] - 87.6) < 1e-1
        assert by["bybit"]["interval_h"] == 1.0
        await redis.aclose()

    asyncio.run(run())


def test_funding_matrix():
    async def run():
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await _seed_funding(redis, "binance", "BTC", 0.0001)
        await _seed_funding(redis, "bybit", "ETH", -0.0002)
        d = await compute_funding_matrix(redis)
        coins = {r["coin"] for r in d["coins"]}
        assert {"BTC", "ETH"} <= coins
        await redis.aclose()

    asyncio.run(run())


def test_legacy_float_funding_ignored():
    """구 스키마(평문 float) 값이 섞여도 500 없이 무시된다."""
    async def run():
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await redis.hset(funding_key("binance"), "BTC", "0.0001")   # 레거시(dict 아님)
        await _seed_funding(redis, "bybit", "BTC", 0.0002, 8.0)     # 정상
        d = await compute_funding(redis, "BTC")
        exs = {r["exchange"] for r in d["rows"]}
        assert exs == {"bybit"}            # 레거시는 빠지고 정상만
        m = await compute_funding_matrix(redis)
        assert m["coins"][0]["by_ex"].get("binance") is None
        await redis.aclose()

    asyncio.run(run())


def test_arbitrage_strategy():
    async def run():
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        # BTC: 현물 binance 100000, 선물 bybit 102000 → gap 2%
        await _seed_price(redis, ticker_key("binance"), "BTC", 100_000)
        await _seed_price(redis, perp_ticker_key("bybit"), "BTC", 102_000)
        await _seed_funding(redis, "bybit", "BTC", 0.0001, 8.0)
        await redis.hset(wallet_key("binance"), "BTC",
                         json.dumps({"deposit": True, "withdraw": True}))

        d = await compute_arbitrage(redis, min_gap_pct=0.0)
        btc = {x["coin"]: x for x in d["rows"]}["BTC"]
        assert btc["long"]["exchange"] == "binance"      # 싼 쪽
        assert btc["short"]["exchange"] == "bybit"       # 비싼 쪽
        assert abs(btc["gap_pct"] - 2.0) < 1e-6
        assert btc["short"]["funding"]["rate_pct"] == 0.01
        assert btc["long"]["wallet"]["withdraw"] is True
        await redis.aclose()

    asyncio.run(run())
