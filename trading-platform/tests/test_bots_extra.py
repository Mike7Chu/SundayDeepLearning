"""마진봇·론봇·매도봇 페이퍼 진입/청산 테스트."""
from __future__ import annotations

import asyncio
import time

import fakeredis.aioredis

from bots.coin.loan import LoanPaperBot
from bots.coin.margin import MarginPaperBot
from bots.coin.sell import SellPaperBot
from shared.redis_keys import ticker_key
from shared.schemas import TickerSnapshot


def _seed(redis, ex, coin, price, quote="USDT", margin=None):
    s = TickerSnapshot(coin=coin, price=price, quote=quote, ts=time.time(), margin=margin)
    return redis.hset(ticker_key(ex), coin, s.model_dump_json())


def test_margin_bot_enter_requires_shortable_then_exit():
    async def run():
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        bot = MarginPaperBot(redis, entry_gap=1.0, exit_gap=0.2)
        # 싼 binance 100000, 비싼 bybit 102000(현물·마진 가능) → 숏 가능, net≈1.7
        await _seed(redis, "binance", "BTC", 100_000, margin=True)
        await _seed(redis, "bybit", "BTC", 102_000, margin=True)
        await bot.step()
        assert await bot.gw.position_coins() == {"BTC"}
        # 갭 수렴 → 청산
        await _seed(redis, "bybit", "BTC", 100_150)
        await bot.step()
        assert await bot.gw.position_coins() == set()
        exits = [f for f in await bot.gw.fills() if f["action"] == "exit"]
        assert exits and exits[0]["pnl_pct"] > 0
        await redis.aclose()

    asyncio.run(run())


def test_margin_bot_skips_non_marginable_spot_short():
    async def run():
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        bot = MarginPaperBot(redis, entry_gap=1.0)
        # 비싼 쪽(bybit 현물)이 마진 불가 → 숏 불가 → 진입 안 함
        await _seed(redis, "binance", "BTC", 100_000, margin=True)
        await _seed(redis, "bybit", "BTC", 102_000, margin=False)
        await bot.step()
        assert await bot.gw.position_coins() == set()
        await redis.aclose()

    asyncio.run(run())


def test_loan_bot_enter_exit_with_borrow_cost():
    async def run():
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        bot = LoanPaperBot(redis, entry_gap=1.5, exit_gap=0.3, borrow_cost_pct=0.1)
        await _seed(redis, "binance", "BTC", 100_000)
        await _seed(redis, "bybit", "BTC", 102_000)   # net≈1.7 ≥ 1.5
        await bot.step()
        assert await bot.gw.position_coins() == {"BTC"}
        await _seed(redis, "bybit", "BTC", 100_150)    # 수렴 → 청산
        await bot.step()
        assert await bot.gw.position_coins() == set()
        await redis.aclose()

    asyncio.run(run())


def test_sell_bot_buy_low_premium_sell_high():
    async def run():
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await redis.set("tether:upbit", 1400.0)
        bot = SellPaperBot(redis, base="upbit", ref="binance", buy_pct=0.0, sell_pct=3.0)
        # 김프 0% → 매집(진입)
        await _seed(redis, "upbit", "BTC", 140_000_000, quote="KRW")
        await _seed(redis, "binance", "BTC", 100_000)
        await bot.step()
        assert await bot.gw.position_coins() == {"BTC"}
        # 김프 +3% → 익절(청산)
        await _seed(redis, "upbit", "BTC", 144_200_000, quote="KRW")
        await bot.step()
        assert await bot.gw.position_coins() == set()
        exits = [f for f in await bot.gw.fills() if f["action"] == "exit"]
        assert exits and exits[0]["pnl_pct"] > 2.5
        await redis.aclose()

    asyncio.run(run())
