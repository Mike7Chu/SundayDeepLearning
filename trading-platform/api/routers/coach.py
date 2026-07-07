"""AI 포트폴리오 코치 API — 아침 점검 리포트·목표 설정.

GET  /coach          최신 아침 점검 리포트 + 목표
POST /coach/goal     목표 저장(수익률 %·기한·메모) — 코치가 목표 현실성 점검에 사용
POST /coach/run      지금 점검(온디맨드) — API 키 모드는 즉시, CLI 모드는 호스트 큐
"""
from __future__ import annotations

import json
import time

from fastapi import APIRouter
from pydantic import BaseModel

from api.redis_client import get_redis
from research.analyst import Analyst
from research.coach import gather_coach
from shared.redis_keys import COACH_GOAL_KEY, COACH_KEY, COACH_REQ_KEY
from shared.settings import settings

router = APIRouter()


class Goal(BaseModel):
    target_pct: float | None = None   # 목표 수익률(%). 예: 35
    deadline: str = ""                # 기한(YYYY-MM-DD)
    memo: str = ""                    # 예: 코인 손실 복구


async def _load(key: str) -> dict:
    raw = await get_redis().get(key)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


@router.get("/coach")
async def coach_get() -> dict:
    return {"enabled": Analyst().enabled and settings.coach_enabled,
            "hour_kst": settings.coach_hour_kst,
            "report": await _load(COACH_KEY) or None,
            "goal": await _load(COACH_GOAL_KEY) or None}


@router.post("/coach/goal")
async def coach_goal(goal: Goal) -> dict:
    data = {"target_pct": goal.target_pct, "deadline": goal.deadline.strip(),
            "memo": goal.memo.strip(), "ts": time.time()}
    await get_redis().set(COACH_GOAL_KEY, json.dumps(data, ensure_ascii=False))
    return {"saved": True, "goal": data}


@router.post("/coach/run")
async def coach_run() -> dict:
    redis = get_redis()
    analyst = Analyst()
    if analyst.mode == "api":   # 종량 키 모드는 컨테이너에서 즉시 실행
        block = await gather_coach(redis)
        if block is None:
            return {"queued": False,
                    "detail": "보유 데이터 없음 — 토스 연동(TOSS_CLIENT_ID/SECRET) 필요"}
        result = await analyst.analyze_coach(block)
        await redis.set(COACH_KEY, json.dumps(result, ensure_ascii=False))
        return {"queued": False, "report": result}
    # 구독 CLI 모드: 호스트 research 프로세스가 큐를 소비(15초 내 픽업)
    await redis.sadd(COACH_REQ_KEY, "now")
    return {"queued": True,
            "detail": "점검 요청됨 — 호스트 research가 곧 처리합니다(웹검색 포함 수 분 소요)."}
