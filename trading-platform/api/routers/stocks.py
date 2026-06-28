"""주식(KIS) 시세 + 전략 API (시그널·가치·배당)."""
from __future__ import annotations

import json

from fastapi import APIRouter

from api.redis_client import get_redis
from api.services.stock_dividend import dividend_view
from api.services.stock_signal import signals_for
from api.services.stock_value import value_screener
from collector.stock.kis import load_watchlist
from shared.redis_keys import STOCK_QUOTE_KEY

router = APIRouter()


@router.get("/stocks")
async def stocks() -> dict:
    raw = await get_redis().hgetall(STOCK_QUOTE_KEY)
    rows = [json.loads(v) for v in raw.values()]
    rows.sort(key=lambda r: r.get("change_pct", 0), reverse=True)
    return {"rows": rows}


@router.get("/stocks/value")
async def stocks_value() -> dict:
    """가치투자 스크리너(마법공식 랭킹)."""
    return await value_screener(get_redis())


@router.get("/stocks/signals")
async def stocks_signals() -> dict:
    """관심종목 기술적 시그널(일봉 시계열 수집분 기준)."""
    redis = get_redis()
    rows = []
    for w in load_watchlist():
        s = await signals_for(redis, w["code"], w.get("name", ""))
        if s:
            rows.append(s)
    order = {"buy": 0, "neutral": 1, "sell": 2}
    rows.sort(key=lambda r: (order.get(r["signal"], 1), -(r.get("score") or 0)))
    return {"rows": rows}


@router.get("/stocks/dividend")
async def stocks_dividend(monthly_budget: float = 0.0) -> dict:
    """배당수익률 랭킹 + (예산 지정 시) 정기 적립(DRIP) 제안."""
    return await dividend_view(get_redis(), monthly_budget)
