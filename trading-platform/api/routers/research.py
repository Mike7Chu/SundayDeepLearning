"""AI 가치투자 리서치 API.

GET  /research            저장된 리포트 목록(요약)
GET  /research/{code}     해당 종목 최신 리포트(전문)
POST /research/{code}/run 즉시 분석 실행(키 없으면 비활성 리포트)
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from api.redis_client import get_redis
from collector.stock.kis import load_watchlist
from research.analyst import Analyst
from research.data import StockData, gather
from shared.redis_keys import RESEARCH_KEY

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
async def research_run(code: str) -> dict:
    redis = get_redis()
    analyst = Analyst()
    data = await gather(redis, code) or StockData(code=code, name=_name_for(code))
    if not data.name:
        data.name = _name_for(code)
    report = await analyst.analyze(data)
    await redis.hset(RESEARCH_KEY, code, json.dumps(report, ensure_ascii=False))
    return report
