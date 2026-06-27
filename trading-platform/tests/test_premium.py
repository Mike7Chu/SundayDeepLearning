"""김프 계산 + 유니버스 로딩 스모크 테스트 (redis는 fakeredis 사용)."""
from __future__ import annotations

import asyncio
import time

import fakeredis.aioredis

from api.services.premium import compute_premium
from shared.redis_keys import FX_USDKRW_KEY, ticker_key
from shared.schemas import TickerSnapshot
from shared.universe import load_universe


def test_universe_loads():
    u = load_universe()
    assert "upbit" in u.domestic
    assert "binance" in u.overseas
    assert len(u.exchanges) == 9
    assert u.symbol_for("upbit", "BTC") == "BTC/KRW"
    assert u.symbol_for("binance", "BTC") == "BTC/USDT"


def _seed(redis, exchange, coin, price, quote):
    snap = TickerSnapshot(coin=coin, price=price, quote=quote, ts=time.time())
    return redis.hset(ticker_key(exchange), coin, snap.model_dump_json())


def test_premium_math():
    async def run():
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await redis.set(FX_USDKRW_KEY, 1380.0)
        # 업비트 BTC 138,000,000 KRW / 바이낸스 BTC 100,000 USDT
        # 해외가 KRW = 100000 * 1380 = 138,000,000 -> 김프 0%
        await _seed(redis, "upbit", "BTC", 138_000_000, "KRW")
        await _seed(redis, "binance", "BTC", 100_000, "USDT")
        # ETH: 업비트 5,520,000 / 바이낸스 4000*1380=5,520,000 -> 0% (control)
        await _seed(redis, "upbit", "ETH", 5_700_000, "KRW")
        await _seed(redis, "binance", "ETH", 4_000, "USDT")

        cells = await compute_premium(redis, "upbit", "binance")
        by_coin = {c.coin: c for c in cells}
        assert abs(by_coin["BTC"].premium_pct) < 1e-6
        # ETH 김프 = (5,700,000 / 5,520,000 - 1)*100 ≈ 3.26%
        assert abs(by_coin["ETH"].premium_pct - 3.2608695) < 1e-3
        await redis.aclose()

    asyncio.run(run())


def test_api_health():
    from fastapi.testclient import TestClient
    from api.main import app

    client = TestClient(app)
    assert client.get("/health").json() == {"status": "ok"}
    ex = client.get("/exchanges").json()
    assert len(ex["overseas"]) == 7
