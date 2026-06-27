"""해외 거래소 간 가격차(아비트라지) + 펀딩비(정산주기 포함) 비교.

성능: 거래소별 해시를 hgetall로 한 번씩만 로드해 메모리에서 계산(N+1 제거).
안정성: 값이 기대 스키마(dict)가 아니면(레거시 데이터 등) 해당 셀만 건너뛴다.
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


def _loads(raw: str):
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


async def _hash(redis: aioredis.Redis, key: str) -> dict[str, dict]:
    """해시 전체를 한 번에 로드 → {field: parsed_dict}. 불량 값은 제외."""
    raw = await redis.hgetall(key)
    out: dict[str, dict] = {}
    for field, val in raw.items():
        d = _loads(val)
        if isinstance(d, dict):
            out[field] = d
    return out


async def all_coins(redis: aioredis.Redis) -> list[str]:
    """해외 현물에 존재하는 전 코인(검색 자동완성용)."""
    universe = load_universe()
    coins: set[str] = set()
    for ex in universe.overseas:
        coins.update(await redis.hkeys(ticker_key(ex)))
    return sorted(coins)


async def compute_cross(redis: aioredis.Redis, coin: str, market: str = "spot") -> dict:
    """해외 거래소들의 coin 가격(USDT) 비교. market=spot|perp."""
    universe = load_universe()
    key_fn = perp_ticker_key if market == "perp" else ticker_key

    rows: list[dict] = []
    for ex in universe.overseas:
        d = _loads(await redis.hget(key_fn(ex), coin) or "")
        price = d.get("price") if isinstance(d, dict) else None
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


def funding_cell(d: dict) -> dict | None:
    """파싱된 펀비 dict → 표시용 셀. rate 없으면 None."""
    rate = d.get("rate")
    if rate is None:
        return None
    interval_h = d.get("interval_h")
    return {
        "rate_pct": round(float(rate) * 100, 4),
        "interval_h": interval_h,
        "next_ts": d.get("next_ts"),
        "apy": _apy(float(rate), interval_h),
    }


async def compute_funding(redis: aioredis.Redis, coin: str) -> dict:
    """단일 코인의 거래소별 펀딩비 비교(정산주기·APY 포함)."""
    universe = load_universe()
    rows: list[dict] = []
    for ex in universe.overseas:
        d = _loads(await redis.hget(funding_key(ex), coin) or "")
        cell = funding_cell(d) if isinstance(d, dict) else None
        if cell:
            rows.append({"exchange": ex, **cell})

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
    """코인 × 거래소 펀딩비 매트릭스(더따리 실시간 펀비 화면).

    거래소별 funding 해시를 한 번씩만 로드(N+1 제거).
    """
    universe = load_universe()
    exchanges = universe.overseas

    # ex -> {coin: parsed} 한 번에
    per_ex = {ex: await _hash(redis, funding_key(ex)) for ex in exchanges}
    coins: set[str] = set()
    for d in per_ex.values():
        coins.update(d.keys())

    rows: list[dict] = []
    for coin in sorted(coins):
        by_ex: dict[str, dict] = {}
        for ex in exchanges:
            d = per_ex[ex].get(coin)
            cell = funding_cell(d) if d else None
            if cell:
                by_ex[ex] = cell
        if by_ex:
            rows.append({"coin": coin, "by_ex": by_ex})
    return {"exchanges": exchanges, "coins": rows}
