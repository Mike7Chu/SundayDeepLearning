"""관심종목 편집 API — 대시보드에서 동적으로 추가/삭제.

Redis(stock:watchlist)에 [{code,name}] 저장. 없으면 config/stocks.yaml이 기본값.
collector/research 루프는 매 주기 이 목록을 다시 읽어 반영한다.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.redis_client import get_redis
from collector.stock.kis import effective_watchlist, normalize_watch_item
from shared.redis_keys import STOCK_QUOTE_KEY, WATCHLIST_KEY

router = APIRouter()


class WatchItem(BaseModel):
    code: str
    name: str = ""


async def _save(redis, items: list[dict]) -> None:
    await redis.set(WATCHLIST_KEY, json.dumps(items, ensure_ascii=False))


@router.get("/watchlist")
async def get_watchlist() -> dict:
    return {"rows": await effective_watchlist(get_redis())}


@router.post("/watchlist")
async def add_watch(item: WatchItem) -> dict:
    redis = get_redis()
    norm = normalize_watch_item(item.code, item.name)
    if not norm:
        raise HTTPException(400, "종목코드는 6자리 숫자")
    # 이름 미지정이면 수집된 시세에서 보완
    if not norm["name"]:
        raw = await redis.hget(STOCK_QUOTE_KEY, norm["code"])
        if raw:
            try:
                norm["name"] = json.loads(raw).get("name", "")
            except (ValueError, TypeError):
                pass
    items = await effective_watchlist(redis)
    if any(w.get("code") == norm["code"] for w in items):
        raise HTTPException(409, "이미 관심종목")
    items.append(norm)
    await _save(redis, items)
    return {"rows": items}


@router.delete("/watchlist/{code}")
async def remove_watch(code: str) -> dict:
    redis = get_redis()
    items = [w for w in await effective_watchlist(redis) if w.get("code") != code]
    await _save(redis, items)
    return {"rows": items}
