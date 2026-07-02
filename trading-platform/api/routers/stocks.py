"""주식(KIS) 시세 + 전략 API (시그널·가치·배당)."""
from __future__ import annotations

import json

from fastapi import APIRouter

import json as _json

from api.redis_client import get_redis
from api.services.stock_dividend import dividend_view
from api.services.stock_signal import signals_for
from api.services.stock_value import value_screener
from backtest.engine import STRATEGIES, backtest
from collector.stock.kis import load_watchlist
from fastapi import HTTPException
from shared.redis_keys import STOCK_QUOTE_KEY, stock_ohlcv_key

router = APIRouter()


@router.get("/stocks")
async def stocks() -> dict:
    raw = await get_redis().hgetall(STOCK_QUOTE_KEY)
    rows = [json.loads(v) for v in raw.values()]
    rows.sort(key=lambda r: r.get("change_pct", 0), reverse=True)
    return {"rows": rows}


@router.get("/stocks/value")
async def stocks_value(limit: int = 200) -> dict:
    """가치투자 스크리너(마법공식 랭킹). 전체 시장 수집분(stock:market) 기준, 상위 limit."""
    return await value_screener(get_redis(), limit=limit)


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


@router.get("/stocks/backtest/{code}")
async def stocks_backtest(code: str, strategy: str = "sma") -> dict:
    """저장된 일봉으로 전략 백테스트(sma|rsi|momentum). 룰 검증용(실매매 아님)."""
    if strategy not in STRATEGIES:
        raise HTTPException(400, f"전략은 {', '.join(STRATEGIES)} 중 하나")
    raw = await get_redis().get(stock_ohlcv_key(code))
    if not raw:
        raise HTTPException(404, "일봉 없음 — 수집 대기(KIS 키 필요)")
    candles = _json.loads(raw)
    closes = [c["close"] for c in candles if isinstance(c, dict) and c.get("close")]
    return {"code": code, **backtest(closes, strategy)}
