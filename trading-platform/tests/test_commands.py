"""텔레그램 명령 처리 테스트 (fakeredis, 순수 디스패치)."""
from __future__ import annotations

import asyncio

import fakeredis.aioredis

from notifier.commands import handle, parse_command
from shared.alert_settings import load_settings
from shared.redis_keys import BOT_KILLSWITCH_KEY, bot_enabled_key


def test_parse_command():
    assert parse_command("/bot start hyeonseon") == ("bot", ["start", "hyeonseon"])
    assert parse_command("/status@MyBot") == ("status", [])
    assert parse_command("") == ("", [])


def test_bot_start_stop_and_killswitch():
    async def run():
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        r = await handle(redis, "/bot start hyeonseon")
        assert "시작" in r and await redis.get(bot_enabled_key("hyeonseon")) == "1"
        await handle(redis, "/bot stop hyeonseon")
        assert await redis.get(bot_enabled_key("hyeonseon")) == "0"
        assert "알 수 없는 봇" in await handle(redis, "/bot start nope")

        await handle(redis, "/killswitch on")
        assert await redis.get(BOT_KILLSWITCH_KEY) == "1"
        await handle(redis, "/killswitch off")
        assert await redis.get(BOT_KILLSWITCH_KEY) == "0"
        await redis.aclose()

    asyncio.run(run())


def test_mute_unmute_updates_settings():
    async def run():
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await handle(redis, "/mute")
        assert (await load_settings(redis)).enabled is False
        await handle(redis, "/unmute")
        assert (await load_settings(redis)).enabled is True
        await redis.aclose()

    asyncio.run(run())


def test_status_and_help_and_brief():
    async def run():
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        assert "명령어" in await handle(redis, "/help")
        assert "상태" in await handle(redis, "/status")
        assert "알림" in await handle(redis, "/alerts")

        called = {"n": 0}
        async def fake_brief():
            called["n"] += 1
            return True
        assert "발송" in await handle(redis, "/brief", brief_fn=fake_brief)
        assert called["n"] == 1
        assert "미연결" in await handle(redis, "/brief")     # 콜백 없음
        assert "알 수 없는 명령" in await handle(redis, "/foo")
        await redis.aclose()

    asyncio.run(run())
