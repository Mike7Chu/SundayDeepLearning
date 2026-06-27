"""봇 페이퍼: 게이트웨이 가상 체결 + 현선봇 진입/청산 PnL."""
from __future__ import annotations

import asyncio
import json
import time

import fakeredis.aioredis

from bots.coin.hyeonseon import HyeonseonPaperBot
from bots.execution_gateway import ExecutionGateway
from shared.redis_keys import BOT_KILLSWITCH_KEY, bot_enabled_key, ticker_key
from shared.schemas import TickerSnapshot


def test_gateway_open_close():
    async def run():
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        gw = ExecutionGateway(redis, "t")
        await gw.open_paper("BTC", {"entry_perp_pct": -2.0})
        assert await gw.position_coins() == {"BTC"}
        await gw.close_paper("BTC", pnl_pct=2.0)
        assert await gw.position_coins() == set()
        fills = await gw.fills()
        assert {f["action"] for f in fills} == {"enter", "exit"}
        await redis.aclose()

    asyncio.run(run())


def test_gateway_killswitch_blocks_open():
    async def run():
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await redis.set(BOT_KILLSWITCH_KEY, "1")
        gw = ExecutionGateway(redis, "t")
        await gw.open_paper("BTC", {"entry_perp_pct": -2.0})
        assert await gw.position_coins() == set()   # 킬스위치로 진입 차단
        await redis.aclose()

    asyncio.run(run())


def _seed(redis, ex, coin, price, quote):
    snap = TickerSnapshot(coin=coin, price=price, quote=quote, ts=time.time())
    return redis.hset(ticker_key(ex), coin, snap.model_dump_json())


def _seed_perp(redis, ex, coin, price):
    from shared.redis_keys import perp_ticker_key
    snap = TickerSnapshot(coin=coin, price=price, quote="USDT", ts=time.time())
    return redis.hset(perp_ticker_key(ex), coin, snap.model_dump_json())


def test_hyeonseon_enter_then_exit():
    async def run():
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await redis.set(bot_enabled_key("hyeonseon"), "1")
        await redis.set("tether:upbit", 1400.0)
        bot = HyeonseonPaperBot(redis, entry_pct=-1.0, exit_pct=0.0)

        # 진입: 국내 137.2M, 해외현물·선물 100,000 → 선물김프 137.2/140-1 ≈ -2%
        await _seed(redis, "upbit", "BTC", 137_200_000, "KRW")
        await _seed(redis, "binance", "BTC", 100_000, "USDT")   # 현물(교집합 필요)
        await _seed_perp(redis, "binance", "BTC", 100_000)
        await bot.step()
        assert await bot.gw.position_coins() == {"BTC"}

        # 청산: 선물김프가 0%로 회복 (국내 140M) → PnL ≈ +2%
        await _seed(redis, "upbit", "BTC", 140_000_000, "KRW")
        await bot.step()
        assert await bot.gw.position_coins() == set()
        exits = [f for f in await bot.gw.fills() if f["action"] == "exit"]
        assert exits and exits[0]["pnl_pct"] > 1.5
        await redis.aclose()

    asyncio.run(run())
