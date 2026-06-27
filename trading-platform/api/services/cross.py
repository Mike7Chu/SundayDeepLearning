"""해외 거래소 간 가격차(아비트라지) + 펀딩비(정산주기 포함) 비교.

- 현물(spot)/선물(perp) 각각 해외 거래소들의 USDT 가격 → 최저-최고 스프레드.
- 펀딩비는 거래소별 비율(%) + 정산주기(interval_h) + 다음정산시각(next_ts) + APY.
(마진은 현물 오더북 공유 → 가격은 현물과 동일.)
"""
from __future__ import annotations

import json

import redis.asyncio as aioredis

from shared.redis_keys import funding_key, perp_ticker_key, ticker_key
from shared.universe import load_universe

_DEFAULT_INTERVAL_H = 8.0


def _apy(rate: float, interval_h: float | None) -> float:
    """펀딩비를 연이율(%)로 정규화. 정산주기 다른 거래소 비교용."""
    h = interval_h or _DEFAULT_INTERVAL_H
    periods_per_year = (24.0 / h) * 365.0
    return round(rate * periods_per_year * 100, 4)


async def _union_coins(redis: aioredis.Redis, key_fn, exchanges: list[str]) -> list[str]:
    coins: set[str] = set()
    for ex in exchanges:
        coins.update(await redis.hkeys(key_fn(ex)))
    return sorted(coins)


async def all_coins(redis: aioredis.Redis) -> list[str]:
    """해외 현물에 존재하는 전 코인(검색 자동완성용)."""
    universe = load_universe()
    return await _union_coins(redis, ticker_key, universe.overseas)


async def compute_cross(redis: aioredis.Redis, coin: str, market: str = "spot") -> dict:
    """해외 거래소들의 coin 가격(USDT) 비교. market=spot|perp."""
    universe = load_universe()
    key_fn = perp_ticker_key if market == "perp" else ticker_key

    rows: list[dict] = []
    for ex in universe.overseas:
        raw = await redis.hget(key_fn(ex), coin)
        if not raw:
            continue
        price = json.loads(raw).get("price")
        if price and price > 0:
            rows.append({"exchange": ex, "price": float(price)})

    rows.sort(key=lambda r: r["price"])
    result = {"coin": coin, "market": market, "rows": rows,
              "spread_pct": None, "cheapest": None, "priciest": None}
    if len(rows) >= 2:
        lo, hi = rows[0], rows[-1]
        result["cheapest"] = lo["exchange"]
        result["priciest"] = hi["exchange"]
        result["spread_pct"] = round((hi["price"] / lo["price"] - 1) * 100, 4)
    return result


def _funding_cell(raw: str) -> dict:
    d = json.loads(raw)
    rate = float(d["rate"])
    interval_h = d.get("interval_h")
    return {
        "rate_pct": round(rate * 100, 4),
        "interval_h": interval_h,
        "next_ts": d.get("next_ts"),
        "apy": _apy(rate, interval_h),
    }


async def compute_funding(redis: aioredis.Redis, coin: str) -> dict:
    """단일 코인의 거래소별 펀딩비 비교(정산주기·APY 포함)."""
    universe = load_universe()
    rows: list[dict] = []
    for ex in universe.overseas:
        raw = await redis.hget(funding_key(ex), coin)
        if raw is None:
            continue
        rows.append({"exchange": ex, **_funding_cell(raw)})

    rows.sort(key=lambda r: r["rate_pct"], reverse=True)
    result = {"coin": coin, "rows": rows, "spread_pct": None,
              "highest": None, "lowest": None}
    if len(rows) >= 2:
        hi, lo = rows[0], rows[-1]
        result["highest"] = hi["exchange"]
        result["lowest"] = lo["exchange"]
        result["spread_pct"] = round(hi["rate_pct"] - lo["rate_pct"], 4)
    return result


async def compute_funding_matrix(redis: aioredis.Redis) -> dict:
    """코인 × 거래소 펀딩비 매트릭스(더따리 실시간 펀비 화면)."""
    universe = load_universe()
    exchanges = universe.overseas
    coins = await _union_coins(redis, funding_key, exchanges)

    rows: list[dict] = []
    for coin in coins:
        by_ex: dict[str, dict] = {}
        for ex in exchanges:
            raw = await redis.hget(funding_key(ex), coin)
            if raw is not None:
                by_ex[ex] = _funding_cell(raw)
        if by_ex:
            rows.append({"coin": coin, "by_ex": by_ex})
    return {"exchanges": exchanges, "coins": rows}
