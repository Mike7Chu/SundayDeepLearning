"""코인 상세 집계 테스트."""
from __future__ import annotations

import asyncio
import json
import time

import fakeredis.aioredis

from api.services.coin_detail import compute_coin_detail
from shared.redis_keys import (
    FX_USDKRW_KEY,
    funding_key,
    perp_ticker_key,
    ticker_key,
    wallet_key,
)
from shared.schemas import TickerSnapshot


def _snap(redis, key, coin, price, quote, vol=None):
    s = TickerSnapshot(coin=coin, price=price, quote=quote, ts=time.time(), quote_volume=vol)
    return redis.hset(key, coin, s.model_dump_json())


def test_coin_detail_aggregates_legs_gap_wallet():
    async def run():
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await redis.set(FX_USDKRW_KEY, 1400)
        # 국내 현물(KRW), 해외 현물(USDT), 해외 선물 + 펀비 + 지갑
        await _snap(redis, ticker_key("upbit"), "BTC", 140_000_000, "KRW")   # = $100,000
        await _snap(redis, ticker_key("binance"), "BTC", 100_000, "USDT", vol=5e8)
        await _snap(redis, perp_ticker_key("bybit"), "BTC", 101_000, "USDT")
        await redis.hset(funding_key("bybit"), "BTC",
                         json.dumps({"rate": 0.0001, "interval_h": 8, "next_ts": None}))
        await redis.hset(wallet_key("binance"), "BTC",
                         json.dumps({"deposit": True, "withdraw": False}))

        d = await compute_coin_detail(redis, "btc")   # 소문자도 허용
        assert d["coin"] == "BTC" and d["forex"] == 1400
        legs = {(l["exchange"], l["market"]): l for l in d["legs"]}
        # upbit 현물 KRW가 USD로 환산돼 binance와 같은 ~$100k
        assert abs(legs[("upbit", "spot")]["price_usd"] - 100_000) < 1
        # 최저가(=100k) 기준 갭: bybit perp 101k → +1%
        assert abs(legs[("bybit", "perp")]["gap_pct"] - 1.0) < 1e-6
        assert legs[("bybit", "perp")]["funding"]["rate_pct"] == 0.01
        # 지갑
        w = {x["exchange"]: x for x in d["wallets"]}
        assert w["binance"]["withdraw"] is False
        await redis.aclose()

    asyncio.run(run())
