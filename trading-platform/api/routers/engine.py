"""매매 엔진 상태 API — 리스크 실드 + 2단계 필터 매수 리스트(읽기 전용)."""
from __future__ import annotations

import json

from fastapi import APIRouter

from api.redis_client import get_redis
from shared.redis_keys import ENGINE_BUYLIST_KEY, ENGINE_PLAN_KEY, ENGINE_RISK_KEY
from shared.settings import settings

router = APIRouter()


@router.get("/plan")
async def trade_plan() -> dict:
    """오늘의 매매 플랜(실적+추세 스윙, 설문 맞춤) — 엔진이 10분마다 갱신."""
    return await _jget(ENGINE_PLAN_KEY) or {"buys": [], "sells": [], "style": None}


async def _jget(key: str) -> dict:
    raw = await get_redis().get(key)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


@router.get("/engine")
async def engine_state() -> dict:
    """리스크 실드(MDD·현금·한도)와 매수 리스트. 엔진 미가동이면 빈 값."""
    risk = await _jget(ENGINE_RISK_KEY)
    buylist = await _jget(ENGINE_BUYLIST_KEY)
    return {
        "risk": risk,
        "buylist": buylist.get("rows", []),
        "buylist_ts": buylist.get("ts"),
        "rules": {
            "mdd_limit_pct": settings.mdd_limit_pct,
            "max_stock_pct": settings.max_stock_pct,
            "cash_floor_pct": settings.cash_floor_pct,
            "buy_score_min": settings.buy_score_min,
        },
        "enabled": bool(risk),
    }
