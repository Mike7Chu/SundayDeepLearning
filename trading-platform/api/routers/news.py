"""뉴스·공시(DART) API."""
from __future__ import annotations

import json

from fastapi import APIRouter

from api.redis_client import get_redis
from shared.redis_keys import DART_RECENT_KEY

router = APIRouter()


@router.get("/news")
async def news(limit: int = 100) -> dict:
    """최근 공시(신규순). code 지정 시 해당 종목만."""
    raw = await get_redis().lrange(DART_RECENT_KEY, 0, limit - 1)
    rows = []
    for v in raw:
        try:
            rows.append(json.loads(v))
        except (json.JSONDecodeError, TypeError):
            continue
    return {"rows": rows}
