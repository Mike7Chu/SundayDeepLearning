"""주식(KIS) 시세 API."""
from __future__ import annotations

import json

from fastapi import APIRouter

from api.redis_client import get_redis
from shared.redis_keys import STOCK_QUOTE_KEY

router = APIRouter()


@router.get("/stocks")
async def stocks() -> dict:
    raw = await get_redis().hgetall(STOCK_QUOTE_KEY)
    rows = [json.loads(v) for v in raw.values()]
    rows.sort(key=lambda r: r.get("change_pct", 0), reverse=True)
    return {"rows": rows}
