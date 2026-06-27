"""해외 거래소 간 가격차(아비트라지) + 펀딩비 비교.

- 현물(spot)/선물(perp) 각각 해외 거래소들의 USDT 가격을 모아 최저-최고 스프레드 산출.
- 펀딩비는 거래소별 비율(%)과 최대-최소 차.
(마진은 현물 오더북을 공유하므로 가격상 현물과 동일 → 별도 제공 안 함.)
"""
from __future__ import annotations

import json

import redis.asyncio as aioredis

from shared.redis_keys import funding_key, perp_ticker_key, ticker_key
from shared.universe import load_universe


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


async def compute_funding(redis: aioredis.Redis, coin: str) -> dict:
    """해외 거래소들의 coin 무기한선물 펀딩비(%) 비교."""
    universe = load_universe()
    rows: list[dict] = []
    for ex in universe.overseas:
        raw = await redis.hget(funding_key(ex), coin)
        if raw is None:
            continue
        rows.append({"exchange": ex, "funding_pct": round(float(raw) * 100, 4)})

    rows.sort(key=lambda r: r["funding_pct"], reverse=True)
    result = {"coin": coin, "rows": rows, "spread_pct": None,
              "highest": None, "lowest": None}
    if len(rows) >= 2:
        hi, lo = rows[0], rows[-1]
        result["highest"] = hi["exchange"]
        result["lowest"] = lo["exchange"]
        # 펀비 차익: 높은 곳 숏 + 낮은 곳 롱 → 절대 펀비 차(%)
        result["spread_pct"] = round(hi["funding_pct"] - lo["funding_pct"], 4)
    return result
