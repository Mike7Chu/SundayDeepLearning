"""봇 공통 프레임워크 (상태머신 + 컨트롤).

상태: stopped(비활성) / running(활성). 컨트롤은 Redis 플래그로 단일 진실원 →
텔레그램·대시보드 양쪽이 같은 플래그를 토글한다. 킬스위치는 전 봇 정지.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

import redis.asyncio as aioredis

from shared.bot_settings import bot_settings_key
from shared.redis_keys import (
    BOT_KILLSWITCH_KEY,
    bot_enabled_key,
    bot_state_key,
)

logger = logging.getLogger("bots")


class BotBase:
    name: str = "base"
    interval_sec: float = 5.0

    def __init__(self, redis: aioredis.Redis):
        self.redis = redis

    async def enabled(self) -> bool:
        return (await self.redis.get(bot_enabled_key(self.name))) == "1"

    async def killed(self) -> bool:
        return (await self.redis.get(BOT_KILLSWITCH_KEY)) == "1"

    async def get_settings(self, defaults: dict) -> dict:
        """인스턴스 기본값 위에 Redis 오버라이드(bot:settings:{name}) 머지 → 실시간 설정."""
        base = dict(defaults)
        raw = await self.redis.get(bot_settings_key(self.name))
        if raw:
            try:
                o = json.loads(raw)
                if isinstance(o, dict):
                    base.update({k: v for k, v in o.items() if k in base and v is not None})
            except (json.JSONDecodeError, TypeError):
                pass
        return base

    async def set_state(self, state: str, **extra) -> None:
        await self.redis.set(bot_state_key(self.name),
                             json.dumps({"state": state, "ts": time.time(), **extra}))

    async def step(self) -> None:
        """1 사이클 로직 (서브클래스 구현)."""
        raise NotImplementedError

    async def run_forever(self) -> None:
        logger.info("bot '%s' loop start", self.name)
        while True:
            try:
                if await self.killed() or not await self.enabled():
                    await self.set_state("stopped")
                else:
                    await self.step()
                    await self.set_state("running")
            except Exception as exc:
                logger.warning("[%s] step 실패: %s", self.name, exc)
            await asyncio.sleep(self.interval_sec)
