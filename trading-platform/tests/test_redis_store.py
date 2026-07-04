"""replace_hash: 사라진 코인 필드가 다음 주기에 제거되는지 검증."""
from __future__ import annotations

import asyncio

import fakeredis.aioredis

from shared.redis_store import replace_hash


def test_replace_removes_vanished_fields():
    async def run():
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await replace_hash(redis, "ticker:binance", {"AERGO": "1", "BTC": "2"})
        assert set(await redis.hkeys("ticker:binance")) == {"AERGO", "BTC"}
        # 다음 주기에 AERGO 상폐(사라짐) → BTC만
        await replace_hash(redis, "ticker:binance", {"BTC": "3"})
        assert set(await redis.hkeys("ticker:binance")) == {"BTC"}
        assert await redis.hget("ticker:binance", "BTC") == "3"
        await redis.aclose()

    asyncio.run(run())


def test_empty_mapping_preserves_existing():
    """거래소 일시 실패(빈 결과)면 기존값 보존(공백 방지)."""
    async def run():
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await replace_hash(redis, "ticker:upbit", {"BTC": "1"})
        await replace_hash(redis, "ticker:upbit", {})   # no-op
        assert set(await redis.hkeys("ticker:upbit")) == {"BTC"}
        await redis.aclose()

    asyncio.run(run())
