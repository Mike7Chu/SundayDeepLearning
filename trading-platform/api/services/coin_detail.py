"""코인 상세 — 한 코인의 전 거래소 가격/갭/펀비/지갑 집계(더따리 coin 페이지).

거래소별 현물·선물 가격을 USD 환산해 갭% 산출, 펀비(주기/APY)·입출금 상태 첨부.
한 코인만 다루므로 hget 소량(거래소수×키종류) — N+1 부담 없음.
"""
from __future__ import annotations

import json

import redis.asyncio as aioredis

from api.services.cross import _apy
from shared.redis_keys import (
    FX_USDKRW_KEY,
    funding_key,
    perp_ticker_key,
    ticker_key,
    wallet_key,
)
from shared.settings import settings
from shared.universe import load_universe


def _loads(raw):
    try:
        return json.loads(raw) if raw else None
    except (json.JSONDecodeError, TypeError):
        return None


def _funding(d: dict | None) -> dict | None:
    if not isinstance(d, dict) or d.get("rate") is None:
        return None
    rate = float(d["rate"])
    return {"rate_pct": round(rate * 100, 4), "interval_h": d.get("interval_h"),
            "next_ts": d.get("next_ts"), "apy": _apy(rate, d.get("interval_h"))}


async def compute_coin_detail(redis: aioredis.Redis, coin: str) -> dict:
    coin = coin.upper()
    universe = load_universe()
    fx = await redis.get(FX_USDKRW_KEY)
    forex = float(fx) if fx else settings.fx_usdkrw_fallback

    legs: list[dict] = []
    wallets: list[dict] = []
    for ex, cfg in universe.exchanges.items():
        wal = _loads(await redis.hget(wallet_key(ex), coin))
        if isinstance(wal, dict):
            wallets.append({"exchange": ex, "deposit": wal.get("deposit"),
                            "withdraw": wal.get("withdraw")})
        # 현물
        sd = _loads(await redis.hget(ticker_key(ex), coin))
        if isinstance(sd, dict) and (sd.get("price") or 0) > 0:
            price = float(sd["price"])
            usd = price / forex if cfg.quote == "KRW" else price
            legs.append({"exchange": ex, "market": "spot", "quote": cfg.quote,
                         "price": price, "price_usd": round(usd, 6),
                         "volume": sd.get("quote_volume"), "margin": sd.get("margin"),
                         "funding": None})
        # 선물(해외)
        if cfg.region == "overseas":
            pd = _loads(await redis.hget(perp_ticker_key(ex), coin))
            if isinstance(pd, dict) and (pd.get("price") or 0) > 0:
                legs.append({"exchange": ex, "market": "perp", "quote": "USDT",
                             "price": float(pd["price"]), "price_usd": round(float(pd["price"]), 6),
                             "volume": pd.get("quote_volume"), "margin": None,
                             "funding": _funding(_loads(await redis.hget(funding_key(ex), coin)))})

    usds = [l["price_usd"] for l in legs if l["price_usd"] > 0]
    base = min(usds) if usds else 0
    for l in legs:
        l["gap_pct"] = round((l["price_usd"] / base - 1) * 100, 4) if base > 0 else None
    legs.sort(key=lambda l: l["price_usd"])
    return {"coin": coin, "forex": round(forex, 2), "legs": legs, "wallets": wallets}
