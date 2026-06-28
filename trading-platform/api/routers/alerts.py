"""알림 설정 API (대시보드 ↔ Redis)."""
from __future__ import annotations

from fastapi import APIRouter

from api.redis_client import get_redis
from shared.alert_settings import load_settings, save_settings

router = APIRouter()


@router.get("/alerts/settings")
async def get_settings() -> dict:
    s = await load_settings(get_redis())
    return s.model_dump()


@router.post("/alerts/settings")
async def update_settings(patch: dict) -> dict:
    s = await save_settings(get_redis(), patch)
    return s.model_dump()
