"""알림 설정 머지/저장 + 디바운스(min_hold) 게이팅."""
from __future__ import annotations

import asyncio
import time

import fakeredis.aioredis

from notifier.alerts import AlertEvent
from notifier.main import _held_long_enough
from shared.alert_settings import load_settings, save_settings
from shared.redis_keys import alert_hold_key


def test_defaults_and_override():
    async def run():
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        s = await load_settings(redis)
        assert s.enabled is True
        assert s.premium_high_pct == 3.0           # yaml 기본
        # 오버라이드 저장
        s2 = await save_settings(redis, {"enabled": False, "premium_high_pct": 7.0,
                                         "exclude_coins": ["pepe"]})
        assert s2.enabled is False and s2.premium_high_pct == 7.0
        assert s2.excluded("PEPE") and not s2.excluded("BTC")
        # 재로드도 반영
        assert (await load_settings(redis)).premium_high_pct == 7.0
        await redis.aclose()

    asyncio.run(run())


def _ev(coin="BTC", side="high", pct=4.0):
    return AlertEvent(pair_key="upbit->binance", coin=coin, side=side,
                      premium_pct=pct, base_exchange="upbit", ref_exchange="binance")


def test_min_hold_debounce():
    async def run():
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        ev = _ev()
        # min_hold=0 → 항상 통과
        assert await _held_long_enough(redis, ev, 0) is True
        # 최초 충족 직후엔 미통과
        ev2 = _ev(coin="ETH")
        assert await _held_long_enough(redis, ev2, 30) is False
        # 30초 전부터 지속된 것처럼 → 통과
        await redis.set(alert_hold_key(_ev(coin="XRP").dedup_key), int(time.time()) - 40)
        assert await _held_long_enough(redis, _ev(coin="XRP"), 30) is True
        await redis.aclose()

    asyncio.run(run())
