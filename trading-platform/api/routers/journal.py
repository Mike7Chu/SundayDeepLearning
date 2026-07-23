"""매매 일지 API — 기록(자동/수동) + AI 복기(당시 판단 vs 현재 결과)."""
from __future__ import annotations

import json

from fastapi import APIRouter
from pydantic import BaseModel

from api.redis_client import get_redis
from api.services.journal import _quote, record_trade, review
from shared.redis_keys import JOURNAL_KEY

router = APIRouter()


class JournalIn(BaseModel):
    code: str
    name: str = ""
    side: str = "BUY"
    qty: float = 0
    price: float
    note: str = ""


@router.post("/journal")
async def add_journal(body: JournalIn) -> dict:
    """수동 일지 기록(토스 앱에서 직접 매매한 것도 여기 남겨 복기)."""
    redis = get_redis()
    code = body.code if body.code.isdigit() else body.code.upper()
    entry = await record_trade(redis, code=code, name=body.name, side=body.side,
                               qty=body.qty, price=body.price, note=body.note,
                               source="manual")
    return {"ok": True, "entry": entry}


@router.get("/journal")
async def list_journal() -> dict:
    """일지 + 복기 — 당시 판단(점수·수급)과 현재가 대비 결과. 최신순."""
    redis = get_redis()
    rows = []
    for raw in await redis.lrange(JOURNAL_KEY, 0, -1):
        try:
            rows.append(json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            continue
    out = []
    for e in reversed(rows):                              # 최신순
        q = await _quote(redis, e.get("code", ""))
        out.append(review(e, q.get("price")))
    # 요약: 판단 부합률(기록된 것 중)
    judged = [r for r in out if r.get("judged_ok") is not None]
    hit = sum(1 for r in judged if r["judged_ok"])
    return {"rows": out, "count": len(out),
            "judged": len(judged),
            "hit_rate": round(100 * hit / len(judged), 0) if judged else None}


@router.delete("/journal/{entry_id}")
async def delete_journal(entry_id: str) -> dict:
    """일지 항목 삭제(오기입 정정)."""
    redis = get_redis()
    kept = []
    for raw in await redis.lrange(JOURNAL_KEY, 0, -1):
        try:
            if json.loads(raw).get("id") == entry_id:
                continue
        except (json.JSONDecodeError, TypeError):
            pass
        kept.append(raw)
    await redis.delete(JOURNAL_KEY)
    if kept:
        await redis.rpush(JOURNAL_KEY, *kept)
    return {"ok": True}
