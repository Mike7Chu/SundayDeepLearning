"""김프/시세 라우터 (REST + WebSocket)."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from api.redis_client import get_redis
from api.services.premium import _load_tickers, compute_premium
from shared.settings import settings
from shared.universe import load_universe

router = APIRouter()


@router.get("/exchanges")
async def list_exchanges() -> dict:
    u = load_universe()
    return {
        "domestic": u.domestic,
        "overseas": u.overseas,
        "coins": u.coins,
    }


@router.get("/tickers/{exchange}")
async def tickers(exchange: str) -> dict:
    snaps = await _load_tickers(get_redis(), exchange)
    return {coin: s.model_dump() for coin, s in snaps.items()}


@router.get("/premium")
async def premium(
    base: str = Query("upbit", description="기준 국내 거래소"),
    ref: str = Query("binance", description="비교 해외 거래소"),
) -> dict:
    cells = await compute_premium(get_redis(), base, ref)
    cells.sort(key=lambda c: c.premium_pct, reverse=True)
    return {"base": base, "ref": ref, "rows": [c.model_dump() for c in cells]}


@router.websocket("/ws/premium")
async def ws_premium(ws: WebSocket) -> None:
    """기준/비교 거래소를 쿼리로 받아 주기적으로 김프를 push."""
    await ws.accept()
    base = ws.query_params.get("base", "upbit")
    ref = ws.query_params.get("ref", "binance")
    try:
        while True:
            cells = await compute_premium(get_redis(), base, ref)
            cells.sort(key=lambda c: c.premium_pct, reverse=True)
            await ws.send_text(json.dumps({
                "base": base, "ref": ref,
                "rows": [c.model_dump() for c in cells],
            }))
            await asyncio.sleep(settings.collect_interval_sec)
    except WebSocketDisconnect:
        return
