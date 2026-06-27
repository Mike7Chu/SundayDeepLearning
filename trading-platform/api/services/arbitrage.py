"""해외 거래소 아비트라지 전략 산출 (더따리 arbitrage 화면).

코인별로 (거래소, 마켓=현물/선물) 가격점을 모아 최저(롱)·최고(숏) 다리를 잡고
gap% = (max/min - 1) * 100 을 계산. 각 다리에 펀딩비·입출금 상태를 첨부.

성능: 거래소별 해시(ticker/perp/funding/wallet)를 hgetall로 한 번씩만 로드(N+1 제거).
"""
from __future__ import annotations

import json
from statistics import median

import redis.asyncio as aioredis

from shared.redis_keys import funding_key, perp_ticker_key, ticker_key, wallet_key
from shared.settings import settings
from shared.universe import load_universe


def _reject_outliers(pts: list[tuple[str, str, float]], factor: float) -> list[tuple[str, str, float]]:
    """가격점 중앙값 대비 [median/factor, median*factor] 밖(충돌/dust/stale)을 제거."""
    if len(pts) < 2:
        return pts
    med = median(p[2] for p in pts)
    if med <= 0:
        return []
    lo, hi = med / factor, med * factor
    return [p for p in pts if lo <= p[2] <= hi]


def _loads(raw: str):
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


async def _hash(redis: aioredis.Redis, key: str) -> dict[str, dict]:
    raw = await redis.hgetall(key)
    out: dict[str, dict] = {}
    for field, val in raw.items():
        d = _loads(val)
        if isinstance(d, dict):
            out[field] = d
    return out


def _funding_leg(d: dict | None) -> dict | None:
    if not isinstance(d, dict) or d.get("rate") is None:
        return None
    return {
        "rate_pct": round(float(d["rate"]) * 100, 4),
        "interval_h": d.get("interval_h"),
        "next_ts": d.get("next_ts"),
    }


async def compute_arbitrage(
    redis: aioredis.Redis, min_gap_pct: float = 0.0, limit: int = 100
) -> dict:
    universe = load_universe()
    overseas = universe.overseas

    # 거래소별 해시를 한 번씩만 로드
    spot = {ex: await _hash(redis, ticker_key(ex)) for ex in overseas}
    perp = {ex: await _hash(redis, perp_ticker_key(ex)) for ex in overseas}
    funding = {ex: await _hash(redis, funding_key(ex)) for ex in overseas}
    wallet = {ex: await _hash(redis, wallet_key(ex)) for ex in overseas}

    coins: set[str] = set()
    for ex in overseas:
        coins.update(spot[ex].keys())
        coins.update(perp[ex].keys())

    def leg(coin: str, ex: str, market: str, price: float) -> dict:
        d: dict = {"exchange": ex, "market": market, "price": price}
        if market == "perp":
            f = _funding_leg(funding[ex].get(coin))
            if f:
                d["funding"] = f
        w = wallet[ex].get(coin)
        if isinstance(w, dict):
            d["wallet"] = {"deposit": w.get("deposit"), "withdraw": w.get("withdraw")}
        return d

    items: list[dict] = []
    for coin in coins:
        pts: list[tuple[str, str, float]] = []  # (exchange, market, price)
        for ex in overseas:
            sd = spot[ex].get(coin)
            if isinstance(sd, dict) and sd.get("price", 0) > 0:
                pts.append((ex, "spot", float(sd["price"])))
            pd = perp[ex].get(coin)
            if isinstance(pd, dict) and pd.get("price", 0) > 0:
                pts.append((ex, "perp", float(pd["price"])))
        # 충돌/dust/stale 가격점 제거 후 갭 산출
        pts = _reject_outliers(pts, settings.arb_outlier_factor)
        if len(pts) < 2:
            continue
        lo = min(pts, key=lambda p: p[2])
        hi = max(pts, key=lambda p: p[2])
        gap = (hi[2] / lo[2] - 1) * 100
        if gap < min_gap_pct:
            continue
        items.append({
            "coin": coin,
            "gap_pct": round(gap, 4),
            "long": leg(coin, *lo),   # 싸게 매수
            "short": leg(coin, *hi),  # 비싸게 매도/숏
        })

    items.sort(key=lambda x: x["gap_pct"], reverse=True)
    return {"rows": items[:limit]}
