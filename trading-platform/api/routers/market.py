"""시장 지표·랭킹 API (토스 v1.2.2).

GET /market — 코스피/코스닥 지수·투자자별 수급 + 국내/미국 급등·거래대금 랭킹.
collector(indicators_loop/rankings_loop)가 10분마다 채운 Redis 스냅샷을 반환.
"""
from __future__ import annotations

import json

from fastapi import APIRouter

from api.redis_client import get_redis
from shared.redis_keys import MARKET_INDICATORS_KEY, MARKET_RANKINGS_KEY

router = APIRouter()


async def _load(key: str) -> dict | None:
    raw = await get_redis().get(key)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


@router.get("/market")
async def market() -> dict:
    return {"indicators": await _load(MARKET_INDICATORS_KEY),
            "rankings": await _load(MARKET_RANKINGS_KEY)}
