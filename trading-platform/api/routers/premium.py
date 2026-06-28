"""김프/시세 라우터 (REST + WebSocket)."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from api.redis_client import get_redis
from api.services.arbitrage import compute_arbitrage
from api.services.cross import (
    all_coins,
    compute_cross,
    compute_funding,
    compute_funding_matrix,
)
from api.services.premium import _load_tickers, compute_premium
from shared.settings import settings
from shared.universe import load_universe

router = APIRouter()


@router.get("/exchanges")
async def list_exchanges() -> dict:
    u = load_universe()
    return {"domestic": u.domestic, "overseas": u.overseas}


@router.get("/coins")
async def coins() -> dict:
    """해외 현물에 존재하는 전 코인(검색 자동완성용)."""
    return {"coins": await all_coins(get_redis())}


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
    cells.sort(key=lambda c: c.premium_coin_pct, reverse=True)
    return {"base": base, "ref": ref, "rows": [c.model_dump() for c in cells]}


@router.get("/cross")
async def cross(
    coin: str = Query("BTC", description="코인 심볼"),
    market: str = Query("spot", description="spot | perp"),
) -> dict:
    """해외 거래소 간 가격차(현물/선물)."""
    return await compute_cross(get_redis(), coin.upper(), market)


@router.get("/funding")
async def funding(coin: str = Query("BTC", description="코인 심볼")) -> dict:
    """해외 거래소 무기한선물 펀딩비 비교(단일 코인)."""
    return await compute_funding(get_redis(), coin.upper())


@router.get("/funding/matrix")
async def funding_matrix() -> dict:
    """코인 × 거래소 펀딩비 매트릭스(정산주기·APY 포함)."""
    return await compute_funding_matrix(get_redis())


@router.get("/arbitrage")
async def arbitrage(
    min_gap: float = Query(0.0, description="최소 갭%"),
    min_volume: float = Query(0.0, description="최소 거래대금(USDT)"),
    limit: int = Query(200, description="최대 전략 수"),
) -> dict:
    """해외 거래소 아비트라지 전략 리스트(갭순)."""
    return await compute_arbitrage(get_redis(), min_gap_pct=min_gap,
                                   min_volume=min_volume, limit=limit)


@router.websocket("/ws/premium")
async def ws_premium(ws: WebSocket) -> None:
    """기준/비교 거래소를 쿼리로 받아 주기적으로 김프를 push."""
    await ws.accept()
    base = ws.query_params.get("base", "upbit")
    ref = ws.query_params.get("ref", "binance")
    try:
        while True:
            cells = await compute_premium(get_redis(), base, ref)
            cells.sort(key=lambda c: c.premium_coin_pct, reverse=True)
            await ws.send_text(json.dumps({
                "base": base, "ref": ref,
                "rows": [c.model_dump() for c in cells],
            }))
            await asyncio.sleep(settings.collect_interval_sec)
    except WebSocketDisconnect:
        return
