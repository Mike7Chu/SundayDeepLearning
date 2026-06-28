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


def _seed_price(redis, key, coin, price, vol=None, margin=None):
    snap = TickerSnapshot(coin=coin, price=price, quote="USDT", ts=time.time(),
                          quote_volume=vol, margin=margin)
    return redis.hset(key, coin, snap.model_dump_json())


def test_arbitrage_spot_leg_margin():
    """현물 다리에 마진 가능여부(margin)가 첨부된다."""
    async def run():
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await _seed_price(redis, ticker_key("binance"), "BTC", 100_000, margin=True)
        await _seed_price(redis, ticker_key("gate"), "BTC", 101_000, margin=False)
        d = await compute_arbitrage(redis, min_gap_pct=0.0)
        btc = {x["coin"]: x for x in d["rows"]}["BTC"]
        # 숏(비싼 gate)=현물·마진X, 롱(싼 binance)=현물·마진O
        assert btc["short"]["market"] == "spot" and btc["short"]["margin"] is False
        assert btc["long"]["margin"] is True
        await redis.aclose()

    asyncio.run(run())


def test_arbitrage_volume_filter():
    async def run():
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        # BTC 저거래대금, ETH 고거래대금
        await _seed_price(redis, ticker_key("binance"), "BTC", 100_000, vol=1_000)
        await _seed_price(redis, ticker_key("bybit"), "BTC", 101_000, vol=1_000)
        await _seed_price(redis, ticker_key("binance"), "ETH", 4_000, vol=5_000_000)
        await _seed_price(redis, ticker_key("bybit"), "ETH", 4_040, vol=5_000_000)
        d = await compute_arbitrage(redis, min_volume=1_000_000)
        coins = {x["coin"] for x in d["rows"]}
        assert "ETH" in coins and "BTC" not in coins   # 저거래대금 제외
        await redis.aclose()

    asyncio.run(run())


def test_arbitrage_net_spread():
    """순스프레드 = 총갭 - (롱+숏 taker) - 전송버퍼."""
    async def run():
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        # binance(taker 0.1) 100000, bybit(taker 0.1) 101000 → 총갭 1.0%
        await _seed_price(redis, ticker_key("binance"), "BTC", 100_000)
        await _seed_price(redis, ticker_key("bybit"), "BTC", 101_000)
        d = await compute_arbitrage(redis, min_gap_pct=0.0)
        btc = {x["coin"]: x for x in d["rows"]}["BTC"]
        assert abs(btc["gap_pct"] - 1.0) < 1e-6
        # 비용 = 0.1+0.1+0.1(버퍼) = 0.3 → 순 0.7
        assert abs(btc["cost_pct"] - 0.3) < 1e-6
        assert abs(btc["net_gap_pct"] - 0.7) < 1e-6
        await redis.aclose()

    asyncio.run(run())


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


def test_arbitrage_rejects_outlier_and_zero():
    """0/충돌(dust) 가격점은 제거되고 정상 클러스터로만 갭 산출."""
    async def run():
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        # BTC: binance 100000, bybit 100500 (정상), gate 1.0 (충돌/dust → 제거)
        await _seed_price(redis, ticker_key("binance"), "BTC", 100_000)
        await _seed_price(redis, ticker_key("bybit"), "BTC", 100_500)
        await _seed_price(redis, ticker_key("gate"), "BTC", 1.0)
        d = await compute_arbitrage(redis, min_gap_pct=0.0)
        btc = {x["coin"]: x for x in d["rows"]}["BTC"]
        # gate(1.0) 제거 → 100000~100500, gap ≈ 0.5% (만약 미제거였다면 1000만%)
        assert btc["gap_pct"] < 1.0
        assert {btc["long"]["exchange"], btc["short"]["exchange"]} == {"binance", "bybit"}
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
