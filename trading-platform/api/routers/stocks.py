"""주식(KIS) 시세 + 전략 API (시그널·가치·배당)."""
from __future__ import annotations

import datetime as _dt
import json
import time as _time

import httpx
from fastapi import APIRouter

import json as _json

from api.redis_client import get_redis
from collector.news.dart import DartClient
from collector.stock.toss import TossClient, candle_metrics
from api.services.stock_dividend import compute_dividend, dividend_view
from api.services.stock_signal import evaluate_signals, signals_for, trade_levels
from api.services.stock_score import compute_score
from api.services.stock_value import load_quotes, value_screener
from backtest.engine import STRATEGIES, backtest
from collector.stock.kis import effective_watchlist
from fastapi import HTTPException
from shared.redis_keys import (
    DART_CORP_KEY,
    STOCK_DIVIDEND_KEY,
    STOCK_MARKET_KEY,
    STOCK_QUOTE_KEY,
    stock_ohlcv_key,
)

router = APIRouter()


@router.get("/stocks")
async def stocks() -> dict:
    raw = await get_redis().hgetall(STOCK_QUOTE_KEY)
    rows = [json.loads(v) for v in raw.values()]
    rows.sort(key=lambda r: r.get("change_pct", 0), reverse=True)
    return {"rows": rows}


@router.get("/stocks/all")
async def stocks_all() -> dict:
    """전체 시장 시세(수집된 유니버스 stock:market ∪ 관심종목 stock:quote). 등락률 정렬."""
    rows = [r for r in await load_quotes(get_redis()) if r.get("price")]
    rows.sort(key=lambda r: r.get("change_pct") or 0, reverse=True)
    return {"rows": rows, "total": len(rows)}


@router.get("/stocks/value")
async def stocks_value(limit: int = 200) -> dict:
    """가치투자 스크리너(마법공식 랭킹). 전체 시장 수집분(stock:market) 기준, 상위 limit."""
    return await value_screener(get_redis(), limit=limit)


@router.get("/stocks/score")
async def stocks_score(limit: int = 200) -> dict:
    """투자 매력도 랭킹 — 가치·품질·모멘텀·타이밍 통합 0~100 + 판정. 전체시장∪관심."""
    redis = get_redis()
    quotes = [q for q in await load_quotes(redis) if q.get("price") and q.get("code")]
    codes = [q["code"] for q in quotes]
    closes_map: dict[str, list] = {}
    if codes:
        async with redis.pipeline(transaction=False) as pipe:
            for c in codes:
                pipe.get(stock_ohlcv_key(c))
            raws = await pipe.execute()
        for c, raw in zip(codes, raws):
            if not raw:
                continue
            try:
                candles = _json.loads(raw)
                closes_map[c] = [x["close"] for x in candles
                                 if isinstance(x, dict) and x.get("close")]
            except (ValueError, TypeError):
                pass
    rows = [compute_score(q, closes_map.get(q["code"], [])) for q in quotes]
    rows.sort(key=lambda r: r["score"], reverse=True)
    return {"rows": rows[:limit], "total": len(rows)}


@router.get("/stocks/signals")
async def stocks_signals() -> dict:
    """관심종목 기술적 시그널(일봉 시계열 수집분 기준)."""
    redis = get_redis()
    rows = []
    for w in await effective_watchlist(redis):
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


_toss = TossClient()
_dart = DartClient()


async def _ondemand_candles(redis, code: str) -> list[dict]:
    """미수집 종목의 일봉을 토스에서 즉시 수집(6h 캐시) — 관심종목 아니어도 분석 가능."""
    if not _toss.enabled:
        return []
    try:
        async with httpx.AsyncClient(timeout=20) as tc:
            candles = await _toss.fetch_daily_history(tc, code)
        if candles:
            await redis.set(stock_ohlcv_key(code), _json.dumps(candles), ex=21600)
        return candles or []
    except Exception:
        return []


async def _ondemand_dividend(redis, code: str) -> list[dict]:
    """미수집 종목의 배당 3개년을 DART에서 즉시 수집 — 관심종목 아니어도 표시."""
    if not _dart.enabled:
        return []
    try:
        craw = await redis.get(DART_CORP_KEY)
        corp = (_json.loads(craw) if craw else {}).get(code)
        if not corp:
            return []
        year = _dt.date.today().year - 1
        async with httpx.AsyncClient(timeout=15) as dc:
            items = await _dart.fetch_dividend_years(dc, corp, year)
            if not items:
                items = await _dart.fetch_dividend_years(dc, corp, year - 1)
        if items:
            await redis.hset(STOCK_DIVIDEND_KEY, code, _json.dumps(
                {"code": code, "items": items, "src": "dart", "ts": _time.time()},
                ensure_ascii=False))
        return items or []
    except Exception:
        return []


@router.get("/stocks/{code}")
async def stock_detail(code: str) -> dict:
    """단일 종목 상세 — 관심종목이 아니어도 즉시 분석.

    펀더멘털은 stock:quote ∪ stock:market. 차트(기술분석·매매가이드)와 배당이
    미수집이면 토스/DART에서 온디맨드 수집 후 계산(캐시 저장).
    """
    redis = get_redis()
    raw = await redis.hget(STOCK_QUOTE_KEY, code) or await redis.hget(STOCK_MARKET_KEY, code)
    quote = json.loads(raw) if raw else {"code": code}
    # 일봉: 저장분 → 없으면 토스 온디맨드
    candles: list = []
    oraw = await redis.get(stock_ohlcv_key(code))
    if oraw:
        try:
            candles = _json.loads(oraw)
        except (ValueError, TypeError):
            candles = []
    if len(candles) < 20:
        candles = await _ondemand_candles(redis, code)
    closes = [c["close"] for c in candles if isinstance(c, dict) and c.get("close")]
    # 장중 실시간 반영: 오늘 캔들이 아직 없으면 현재가를 오늘 종가로 덧붙이고,
    # 있으면 마지막 종가를 실시간가로 교체 → 시그널·추세·매매가이드가 장중 가격 기준.
    live = quote.get("price")
    if live and closes:
        last_date = str(candles[-1].get("date", ""))[:10]
        if last_date == _dt.date.today().isoformat():
            closes[-1] = live
        else:
            closes = closes + [live]
    # 시세가 아직 없으면 일봉으로 보강(현재가·등락률·52주)
    if not quote.get("price") and closes:
        m = candle_metrics(candles)
        quote.update({"price": m.get("last_close"), "change_pct": m.get("change_pct"),
                      "high_52w": quote.get("high_52w") or m.get("high_52w"),
                      "low_52w": quote.get("low_52w") or m.get("low_52w")})
    sig = ({"code": code, "name": quote.get("name", ""), **evaluate_signals(closes)}
           if len(closes) >= 20 else None)
    score = compute_score(quote, closes)
    levels = trade_levels(closes, quote.get("price"))
    # 배당: 저장분 → 없으면 DART 온디맨드
    div = None
    draw = await redis.hget(STOCK_DIVIDEND_KEY, code)
    items: list = []
    if draw:
        try:
            items = json.loads(draw).get("items", [])
        except (json.JSONDecodeError, TypeError):
            items = []
    if not items:
        items = await _ondemand_dividend(redis, code)
    if items:
        div = compute_dividend(quote, items)
    wl = await effective_watchlist(redis)
    return {"quote": quote, "signal": sig, "dividend": div, "score": score,
            "levels": levels,
            "in_watchlist": any(w.get("code") == code for w in wl)}
