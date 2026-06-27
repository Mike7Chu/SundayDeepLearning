"""봇 컨트롤 API — 텔레그램·대시보드 공용(단일 진실원: Redis 플래그)."""
from __future__ import annotations

import json

from fastapi import APIRouter

from api.redis_client import get_redis
from shared.redis_keys import (
    BOT_KILLSWITCH_KEY,
    bot_enabled_key,
    bot_state_key,
    paper_fills_key,
    paper_positions_key,
)

router = APIRouter()

_BOTS = ["hyeonseon"]   # 등록된 봇


async def _bot_summary(redis, name: str) -> dict:
    enabled = (await redis.get(bot_enabled_key(name))) == "1"
    raw_state = await redis.get(bot_state_key(name))
    state = json.loads(raw_state) if raw_state else {"state": "stopped"}
    positions = await redis.hlen(paper_positions_key(name))
    return {"name": name, "enabled": enabled, "state": state.get("state"),
            "positions": positions}


@router.get("/bots")
async def list_bots() -> dict:
    redis = get_redis()
    killed = (await redis.get(BOT_KILLSWITCH_KEY)) == "1"
    return {"killswitch": killed,
            "bots": [await _bot_summary(redis, n) for n in _BOTS]}


@router.post("/bots/{name}/enable")
async def enable_bot(name: str) -> dict:
    await get_redis().set(bot_enabled_key(name), "1")
    return {"name": name, "enabled": True}


@router.post("/bots/{name}/disable")
async def disable_bot(name: str) -> dict:
    await get_redis().set(bot_enabled_key(name), "0")
    return {"name": name, "enabled": False}


@router.post("/bots/killswitch/{on}")
async def killswitch(on: int) -> dict:
    await get_redis().set(BOT_KILLSWITCH_KEY, "1" if on else "0")
    return {"killswitch": bool(on)}


@router.get("/bots/{name}/fills")
async def bot_fills(name: str, limit: int = 50) -> dict:
    raw = await get_redis().lrange(paper_fills_key(name), 0, limit - 1)
    return {"name": name, "fills": [json.loads(v) for v in raw]}
