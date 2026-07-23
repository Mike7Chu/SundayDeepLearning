"""매매 엔진 상태 API — 리스크 실드 + 2단계 필터 매수 리스트(읽기 전용)."""
from __future__ import annotations

import json
import time as _time
from datetime import datetime

from fastapi import APIRouter

from api.redis_client import get_redis
from api.services.roadmap import roadmap
from shared.redis_keys import (
    ASSET_HIST_KEY,
    COACH_GOAL_KEY,
    ENGINE_BUYLIST_KEY,
    ENGINE_PLAN_KEY,
    ENGINE_RISK_KEY,
    TOSS_ACCOUNT_KEY,
    TOSS_HOLDINGS_KEY,
)
from shared.settings import settings

router = APIRouter()


@router.get("/plan")
async def trade_plan() -> dict:
    """오늘의 매매 플랜(실적+추세 스윙, 설문 맞춤) — 엔진이 10분마다 갱신."""
    return await _jget(ENGINE_PLAN_KEY) or {"buys": [], "sells": [], "style": None}


@router.get("/roadmap")
async def asset_roadmap() -> dict:
    """100억 로드맵 — 필요 연복리·현재 페이스·도달 예상·궤도 판정.

    현재자산=토스 실평가(+매수여력) 우선, 없으면 수동값은 프론트가 넘긴 게 아니라
    여기선 토스 기준만. 목표=TARGET_ASSET_KRW, 기한=코치 목표 deadline(있으면).
    """
    redis = get_redis()
    hold = await _jget(TOSS_HOLDINGS_KEY)
    acc = await _jget(TOSS_ACCOUNT_KEY)
    ev, cash = hold.get("total_eval"), acc.get("buying_power")
    current = None
    if ev is not None or cash is not None:
        current = (ev or 0.0) + (cash or 0.0)
    goal = await _jget(COACH_GOAL_KEY)
    deadline_ts = None
    if goal.get("deadline"):
        try:
            deadline_ts = datetime.fromisoformat(goal["deadline"]).timestamp()
        except (ValueError, TypeError):
            deadline_ts = None
    history = []
    for raw in await redis.lrange(ASSET_HIST_KEY, 0, -1):
        try:
            history.append(json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            continue
    rm = roadmap(current, settings.target_asset_krw, _time.time(),
                 deadline_ts, history)
    rm["deadline"] = goal.get("deadline")
    rm["history_days"] = len(history)
    return rm


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
        "auto": {                                    # 자동매매 상태(대시보드 표시)
            "enabled": settings.auto_trade_enabled,
            "broker": settings.auto_trade_broker,
            "paper": settings.kis_paper,
            "us_enabled": settings.us_auto_enabled,
            "kis_trading": settings.kis_trading_enabled,
        },
        "enabled": bool(risk),
    }
