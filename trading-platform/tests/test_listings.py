"""신규상장 감지 로직 테스트 (ccxt 없이 fakeredis로 심볼 집합만 검증)."""
from __future__ import annotations

import asyncio

import fakeredis.aioredis

from notifier.config import load_announce_config
from notifier.listings import detect_new_listings, format_listing, parse_symbol


def test_parse_symbol():
    assert parse_symbol("BTC/KRW") == ("BTC", "KRW")
    assert parse_symbol("WEIRD") == ("WEIRD", "")


def test_format_listing():
    msg = format_listing("upbit", "PEPE/KRW")
    assert "PEPE/KRW" in msg and "upbit" in msg and "🆕" in msg


def test_detect_priming_then_new():
    async def run():
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        first = {"BTC/KRW", "ETH/KRW"}
        # 최초: 조용히 시드 → 알림 없음
        assert await detect_new_listings(redis, "upbit", first) == []
        # 동일: 신규 없음
        assert await detect_new_listings(redis, "upbit", first) == []
        # 신규 상장 등장
        second = first | {"PEPE/KRW"}
        assert await detect_new_listings(redis, "upbit", second) == ["PEPE/KRW"]
        # 같은 게 다시 와도 더는 신규 아님
        assert await detect_new_listings(redis, "upbit", second) == []
        await redis.aclose()

    asyncio.run(run())


def test_empty_symbols_noop():
    async def run():
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        assert await detect_new_listings(redis, "bithumb", set()) == []
        await redis.aclose()

    asyncio.run(run())


def test_announce_config_loads():
    cfg = load_announce_config()
    assert "upbit" in cfg.watched_exchanges
    assert cfg.poll_interval_sec > 0
