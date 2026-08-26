"""AI 가치투자 리서치 API.

GET  /research            저장된 리포트 목록(요약)
GET  /research/{code}     해당 종목 최신 리포트(전문)
POST /research/{code}/run 즉시 분석 실행(키 없으면 비활성 리포트)
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

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
    TENBAGGER_KEY,
    TENBAGGER_REQ_KEY,
)

router = APIRouter()


def _name_for(code: str) -> str:
    return next((w.get("name", "") for w in load_watchlist() if w["code"] == code), "")


def _quarter_key(ts: float | None) -> tuple[int, int] | None:
    """타임스탬프의 (연, 분기1~4) — KST 기준. 분기 경계로 캐시 신선도 판정."""
    if not ts:
        return None
    dt = datetime.fromtimestamp(ts, tz=timezone(timedelta(hours=9)))
    return (dt.year, (dt.month - 1) // 3 + 1)


def _same_quarter(ts: float | None) -> bool:
    """저장된 리포트가 '지금과 같은 분기'에 만들어졌는가(분기 바뀌면 재분석 신호)."""
    qk = _quarter_key(ts)
    return qk is not None and qk == _quarter_key(time.time())


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
    # 분기 캐시: 같은 분기에 이미 분석했으면 재요청 없이 저장분(토큰 절약).
    # 분기가 바뀌면 자동 재분석(신규 사업보고서·실적 반영). force면 무조건 재분석.
    if not force and prev and prev.get("report") and _same_quarter(prev.get("ts")):
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
            "prev": (prev or {}).get("report"),
            "prev_ts": (prev or {}).get("ts")}   # 폴링이 '이전 분기 저장분'을 새 결과로 오인 않게


@router.get("/research/{code}/story")
async def research_story_get(code: str) -> dict:
    redis = get_redis()
    raw = await redis.hget(RESEARCH_STORY_KEY, code)
    if not raw:
        raise HTTPException(status_code=404, detail="스토리 리포트 없음 — POST .../story로 생성")
    return json.loads(raw)


@router.get("/tenbagger")
async def tenbagger_list() -> dict:
    """TENBAGGER DETECTOR 저장 리포트 목록(최신순). enabled로 활성 여부 표시."""
    raw = await get_redis().hgetall(TENBAGGER_KEY)
    rows = []
    for slot, v in raw.items():
        try:
            r = json.loads(v)
        except (json.JSONDecodeError, TypeError):
            continue
        rows.append({"slot": slot, "ts": r.get("ts"), "command": r.get("command"),
                     "enabled": r.get("enabled", False), "report": r.get("report")})
    rows.sort(key=lambda x: x.get("ts") or 0, reverse=True)
    return {"enabled": Analyst().enabled, "rows": rows}


@router.post("/tenbagger")
async def tenbagger_run(command: str = "탐색", force: bool = False) -> dict:
    """텐베거 발굴 실행. 탐색/신규발굴/딥다이브는 12h 캐시(force로 재실행), 오늘점검/
    가격만은 항상 최신 실행. 웹검색 필수 — API 모드 즉시, 구독 CLI는 호스트 큐로."""
    redis = get_redis()
    command = (command or "탐색").strip()
    slot = command
    raw = await redis.hget(TENBAGGER_KEY, slot)
    prev = json.loads(raw) if raw else None
    cacheable = command in ("탐색", "신규발굴") or command.startswith("딥다이브")
    if (cacheable and not force and prev and prev.get("report")
            and time.time() - (prev.get("ts") or 0) < 43200):     # 12h 캐시
        return {**prev, "cached": True}
    analyst = Analyst()
    if analyst.mode == "api":
        result = await analyst.analyze_tenbagger(command)
        await redis.hset(TENBAGGER_KEY, slot, json.dumps(result, ensure_ascii=False))
        return result
    await redis.sadd(TENBAGGER_REQ_KEY, command)
    return {"queued": True, "command": command, "slot": slot,
            "report": "🚀 텐베거 발굴 요청됨(전 시장 심층 리서치 — 수 분 소요). 잠시 후 새로고침.",
            "prev": (prev or {}).get("report"), "prev_ts": (prev or {}).get("ts")}
