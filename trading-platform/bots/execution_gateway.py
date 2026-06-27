"""실행 게이트웨이 — 모든 주문은 여기를 경유한다.

현재는 **dry-run(페이퍼) 전용**: 실제 주문 대신 가상 체결을 Redis에 기록한다.
글로벌 킬스위치(BOT_KILLSWITCH_KEY)가 켜지면 신규 진입을 막는다.
실거래는 추후 dry_run=False + 안전장치(한도/IP/출금권한 제외) 통과 후에만.
"""
from __future__ import annotations

import json
import time

import redis.asyncio as aioredis

from shared.redis_keys import (
    BOT_KILLSWITCH_KEY,
    paper_fills_key,
    paper_positions_key,
)

_FILLS_MAX = 200


class ExecutionGateway:
    def __init__(self, redis: aioredis.Redis, name: str, dry_run: bool = True):
        self.redis = redis
        self.name = name
        self.dry_run = dry_run   # 현재 True 고정(실거래 미오픈)

    async def killed(self) -> bool:
        return (await self.redis.get(BOT_KILLSWITCH_KEY)) == "1"

    async def position_coins(self) -> set[str]:
        return set(await self.redis.hkeys(paper_positions_key(self.name)))

    async def positions(self) -> list[dict]:
        raw = await self.redis.hgetall(paper_positions_key(self.name))
        return [json.loads(v) for v in raw.values()]

    async def fills(self, limit: int = 50) -> list[dict]:
        raw = await self.redis.lrange(paper_fills_key(self.name), 0, limit - 1)
        return [json.loads(v) for v in raw]

    async def _record_fill(self, fill: dict) -> None:
        await self.redis.lpush(paper_fills_key(self.name), json.dumps(fill))
        await self.redis.ltrim(paper_fills_key(self.name), 0, _FILLS_MAX - 1)

    async def open_paper(self, coin: str, meta: dict) -> None:
        """가상 진입. 킬스위치면 무시."""
        if await self.killed():
            return
        pos = {"coin": coin, "ts": time.time(), **meta}
        await self.redis.hset(paper_positions_key(self.name), coin, json.dumps(pos))
        await self._record_fill({"coin": coin, "action": "enter",
                                 "ts": pos["ts"], **meta})

    async def close_paper(self, coin: str, pnl_pct: float, meta: dict | None = None) -> None:
        """가상 청산 + PnL 기록."""
        await self.redis.hdel(paper_positions_key(self.name), coin)
        await self._record_fill({"coin": coin, "action": "exit",
                                 "pnl_pct": round(pnl_pct, 4), "ts": time.time(),
                                 **(meta or {})})
