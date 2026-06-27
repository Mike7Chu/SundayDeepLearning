"""해외 거래소 아비트라지 전략 산출 (더따리 arbitrage 화면).

코인별로 (거래소, 마켓=현물/선물) 가격점을 모아 최저(롱)·최고(숏) 다리를 잡고
gap% = (max/min - 1) * 100 을 계산. 각 다리에 펀딩비(정산주기 포함)와 입출금 상태를 첨부.
전 코인을 gap 내림차순으로 반환.
"""
from __future__ import annotations

import json

import redis.asyncio as aioredis

from shared.redis_keys import (
    funding_key,
    perp_ticker_key,
    ticker_key,
    wallet_key,
)
from shared.universe import load_universe

_MARKETS = (("spot", ticker_key), ("perp", perp_ticker_key))


async def _price_points(redis: aioredis.Redis, coin: str, overseas: list[str]) -> list[dict]:
    pts: list[dict] = []
    for ex in overseas:
        for market, key_fn in _MARKETS:
            raw = await redis.hget(key_fn(ex), coin)
            if not raw:
                continue
            price = json.loads(raw).get("price")
            if price and price > 0:
                pts.append({"exchange": ex, "market": market, "price": float(price)})
    return pts


async def _decorate_leg(redis: aioredis.Redis, pt: dict, coin: str) -> dict:
    leg = dict(pt)
    if pt["market"] == "perp":
        fraw = await redis.hget(funding_key(pt["exchange"]), coin)
        if fraw is not None:
            d = json.loads(fraw)
            leg["funding"] = {
                "rate_pct": round(float(d["rate"]) * 100, 4),
                "interval_h": d.get("interval_h"),
                "next_ts": d.get("next_ts"),
            }
    wraw = await redis.hget(wallet_key(pt["exchange"]), coin)
    if wraw is not None:
        leg["wallet"] = json.loads(wraw)
    return leg


async def compute_arbitrage(
    redis: aioredis.Redis, min_gap_pct: float = 0.0, limit: int = 200
) -> dict:
    universe = load_universe()
    overseas = universe.overseas

    coins: set[str] = set()
    for ex in overseas:
        coins.update(await redis.hkeys(ticker_key(ex)))
        coins.update(await redis.hkeys(perp_ticker_key(ex)))

    items: list[dict] = []
    for coin in coins:
        pts = await _price_points(redis, coin, overseas)
        if len(pts) < 2:
            continue
        lo = min(pts, key=lambda p: p["price"])
        hi = max(pts, key=lambda p: p["price"])
        gap = (hi["price"] / lo["price"] - 1) * 100
        if gap < min_gap_pct:
            continue
        items.append({
            "coin": coin,
            "gap_pct": round(gap, 4),
            "long": await _decorate_leg(redis, lo, coin),   # 싸게 매수
            "short": await _decorate_leg(redis, hi, coin),  # 비싸게 매도/숏
        })

    items.sort(key=lambda x: x["gap_pct"], reverse=True)
    return {"rows": items[:limit]}
