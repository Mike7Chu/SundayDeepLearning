"""AI 가치투자 리서치 API.

GET  /research            저장된 리포트 목록(요약)
GET  /research/{code}     해당 종목 최신 리포트(전문)
POST /research/{code}/run 즉시 분석 실행(키 없으면 비활성 리포트)
"""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, HTTPException

from api.redis_client import get_redis
from collector.stock.kis import effective_watchlist, load_watchlist
from research.analyst import Analyst
from research.data import StockData, gather
from shared.redis_keys import (
    RESEARCH_KEY,
    RESEARCH_REQ_KEY,
    RESEARCH_STORY_KEY,
    RESEARCH_STORY_REQ_KEY,
)

router = APIRouter()


def _name_for(code: str) -> str:
    return next((w.get("name", "") for w in load_watchlist() if w["code"] == code), "")


@router.get("/research")
async def research_list() -> dict:
    raw = await get_redis().hgetall(RESEARCH_KEY)
    rows = []
    for v in raw.values():
        try:
            r = json.loads(v)
        except (json.JSONDecodeError, TypeError):
            continue
        rows.append({
            "code": r.get("code"), "name": r.get("name"),
            "model": r.get("model"), "ts": r.get("ts"),
            "enabled": r.get("enabled", False),
            "summary": (r.get("report") or "").strip().splitlines()[:1],
        })
    rows.sort(key=lambda x: x.get("ts") or 0, reverse=True)
    return {"enabled": Analyst().enabled, "rows": rows}


@router.get("/research/{code}")
async def research_get(code: str) -> dict:
    raw = await get_redis().hget(RESEARCH_KEY, code)
    if not raw:
        raise HTTPException(status_code=404, detail="리포트 없음 — /research/{code}/run 으로 생성")
    return json.loads(raw)


@router.post("/research/{code}/run")
async def research_run(code: str, force: bool = False) -> dict:
    """리포트가 이미 있으면 그걸 보여줌(재실행 안 함). 없거나 force면 분석.

    - force=false(기본): 저장된 리포트가 있으면 즉시 반환(cached). 매번 재실행 방지.
    - force=true(다시 분석): 새로 분석/큐 요청.
    관심종목은 호스트 정기 패스가 매주 갱신하므로 버튼은 최신 저장분을 보여주면 충분.
    """
    redis = get_redis()
    existing_raw = await redis.hget(RESEARCH_KEY, code)
    existing = None
    if existing_raw:
        try:
            existing = json.loads(existing_raw)
        except (json.JSONDecodeError, TypeError):
            existing = None
    if not force and existing and existing.get("enabled") and existing.get("report"):
        return {**existing, "cached": True}
    analyst = Analyst()
    # API 키 모드는 컨테이너에서 즉시 실행. 구독 CLI는 컨테이너에서 못 돌아 → 호스트 큐로.
    if analyst.mode == "api":
        data = await gather(redis, code) or StockData(code=code, name=_name_for(code))
        if not data.name:
            data.name = _name_for(code)
        report = await analyst.analyze(data)
        await redis.hset(RESEARCH_KEY, code, json.dumps(report, ensure_ascii=False))
        return report
    await redis.sadd(RESEARCH_REQ_KEY, code)
    return {"queued": True, "code": code,
            "report": "분석 요청됨 — 호스트 research가 곧 처리합니다. 잠시 후 새로고침.",
            "prev": existing.get("report") if existing else None}


@router.post("/research/{code}/story")
async def research_story(code: str, force: bool = False) -> dict:
    """기업 스토리(다년치 공시 diff) 분석 — company-story 방법론. 웹검색 필요(구독 CLI/API).

    저장분 있으면 반환(force로 재실행). API 모드는 즉시, 구독 CLI는 호스트 큐로.
    """
    redis = get_redis()
    raw = await redis.hget(RESEARCH_STORY_KEY, code)
    prev = json.loads(raw) if raw else None
    if not force and prev and prev.get("report"):
        return {**prev, "cached": True}
    analyst = Analyst()
    if analyst.mode == "api":
        data = await gather(redis, code) or StockData(code=code, name=_name_for(code))
        if not data.name:
            data.name = _name_for(code)
        report = await analyst.analyze_story(data)
        await redis.hset(RESEARCH_STORY_KEY, code, json.dumps(report, ensure_ascii=False))
        return report
    # 구독 CLI는 컨테이너(claude 바이너리 없음 → mode=None)에선 못 돈다 → 호스트 큐로.
    # research_run과 동일 규율: 컨테이너 mode를 신뢰하지 말고 호스트 research가 처리하게 위임.
    # (여기서 mode is None으로 막으면 CLI 사용자는 스토리를 영영 못 씀 — 실제 버그였음.)
    await redis.sadd(RESEARCH_STORY_REQ_KEY, code)
    return {"queued": True, "code": code,
            "report": "스토리 분석 요청됨(다년치 공시 비교 — 시간이 좀 걸립니다). 잠시 후 새로고침.",
            "prev": (prev or {}).get("report")}


@router.get("/research/{code}/story")
async def research_story_get(code: str) -> dict:
    redis = get_redis()
    raw = await redis.hget(RESEARCH_STORY_KEY, code)
    if not raw:
        raise HTTPException(status_code=404, detail="스토리 리포트 없음 — POST .../story로 생성")
    return json.loads(raw)
