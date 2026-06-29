"""봇 설정 오버라이드 — 저장값이 봇 동작에 실시간 반영되는지."""
from __future__ import annotations

import asyncio
import time

import fakeredis.aioredis

from bots.coin.margin import MarginPaperBot
from shared.bot_settings import load_bot_settings, save_bot_settings
from shared.redis_keys import ticker_key
from shared.schemas import TickerSnapshot


def _seed(redis, ex, coin, price, margin=None):
    s = TickerSnapshot(coin=coin, price=price, quote="USDT", ts=time.time(), margin=margin)
    return redis.hset(ticker_key(ex), coin, s.model_dump_json())


def test_load_save_merge():
    async def run():
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        s = await load_bot_settings(redis, "margin")
        assert s["entry_gap"] == 1.0 and s["exchanges"] == []   # 기본값
        await save_bot_settings(redis, "margin", {"entry_gap": 5.0, "bogus": 1})
        s = await load_bot_settings(redis, "margin")
        assert s["entry_gap"] == 5.0 and "bogus" not in s       # 유효 키만
        await redis.aclose()

    asyncio.run(run())


def test_margin_bot_respects_override_entry_gap():
    async def run():
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        bot = MarginPaperBot(redis)                 # 기본 entry_gap=1.0
        await _seed(redis, "binance", "BTC", 100_000, margin=True)
        await _seed(redis, "bybit", "BTC", 102_000, margin=True)   # net≈1.7
        # 진입 임계치를 3.0으로 올리면 net 1.7 < 3.0 → 진입 안 함
        await save_bot_settings(redis, "margin", {"entry_gap": 3.0})
        await bot.step()
        assert await bot.gw.position_coins() == set()
        # 0.5로 낮추면 진입
        await save_bot_settings(redis, "margin", {"entry_gap": 0.5})
        await bot.step()
        assert await bot.gw.position_coins() == {"BTC"}
        await redis.aclose()

    asyncio.run(run())


def test_margin_bot_respects_exchange_filter():
    async def run():
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        bot = MarginPaperBot(redis)
        await _seed(redis, "binance", "BTC", 100_000, margin=True)
        await _seed(redis, "bybit", "BTC", 102_000, margin=True)
        # okx/gate만 허용 → binance/bybit 기회 제외
        await save_bot_settings(redis, "margin", {"entry_gap": 0.5, "exchanges": ["okx", "gate"]})
        await bot.step()
        assert await bot.gw.position_coins() == set()
        await redis.aclose()

    asyncio.run(run())
