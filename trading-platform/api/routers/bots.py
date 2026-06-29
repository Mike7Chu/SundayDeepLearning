"""봇 컨트롤 API — 텔레그램·대시보드 공용(단일 진실원: Redis 플래그)."""
from __future__ import annotations

import json

from fastapi import APIRouter

from fastapi import Request

from api.redis_client import get_redis
from bots.registry import REGISTERED_BOTS
from shared.bot_settings import FIELDS, load_bot_settings, save_bot_settings
from shared.redis_keys import (
    BOT_KILLSWITCH_KEY,
    bot_enabled_key,
    bot_state_key,
    paper_fills_key,
    paper_positions_key,
)

router = APIRouter()

_BOTS = REGISTERED_BOTS   # 등록된 봇(단일 진실원)


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


@router.get("/bots/{name}/settings")
async def get_bot_settings(name: str) -> dict:
    """봇 effective 설정 + 폼 메타(필드 목록)."""
    return {"name": name, "settings": await load_bot_settings(get_redis(), name),
            "fields": FIELDS.get(name, [])}


@router.post("/bots/{name}/settings")
async def post_bot_settings(name: str, request: Request) -> dict:
    """봇 설정 부분 저장(대시보드/텔레그램 공용)."""
    patch = await request.json()
    return {"name": name, "settings": await save_bot_settings(get_redis(), name, patch)}


@router.post("/bots/killswitch/{on}")
async def killswitch(on: int) -> dict:
    await get_redis().set(BOT_KILLSWITCH_KEY, "1" if on else "0")
    return {"killswitch": bool(on)}


@router.get("/bots/{name}/fills")
async def bot_fills(name: str, limit: int = 50) -> dict:
    raw = await get_redis().lrange(paper_fills_key(name), 0, limit - 1)
    return {"name": name, "fills": [json.loads(v) for v in raw]}
