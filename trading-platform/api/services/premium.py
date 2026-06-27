"""김프/역프 계산 서비스.

premium_pct = (국내가_KRW / (해외가_USDT * USDKRW) - 1) * 100
  양수 = 김프(국내가 비쌈), 음수 = 역프.
"""
from __future__ import annotations

import json

import redis.asyncio as aioredis

from shared.redis_keys import FX_USDKRW_KEY, ticker_key
from shared.schemas import PremiumCell, TickerSnapshot
from shared.settings import settings
from shared.universe import load_universe


async def _load_tickers(redis: aioredis.Redis, exchange: str) -> dict[str, TickerSnapshot]:
    raw = await redis.hgetall(ticker_key(exchange))
    return {coin: TickerSnapshot(**json.loads(v)) for coin, v in raw.items()}


async def _usdkrw(redis: aioredis.Redis) -> float:
    val = await redis.get(FX_USDKRW_KEY)
    return float(val) if val else settings.fx_usdkrw_fallback


async def compute_premium(
    redis: aioredis.Redis, base: str, ref: str
) -> list[PremiumCell]:
    """기준 국내 거래소(base) vs 해외 거래소(ref) 코인별 김프."""
    universe = load_universe()
    if base not in universe.exchanges:
        raise ValueError(f"unknown base exchange: {base}")
    if ref not in universe.exchanges:
        raise ValueError(f"unknown ref exchange: {ref}")

    usdkrw = await _usdkrw(redis)
    base_t = await _load_tickers(redis, base)
    ref_t = await _load_tickers(redis, ref)

    cells: list[PremiumCell] = []
    for coin in universe.coins:
        b = base_t.get(coin)
        r = ref_t.get(coin)
        if not b or not r or r.price <= 0:
            continue
        base_krw = b.price if b.quote == "KRW" else b.price * usdkrw
        ref_krw = r.price if r.quote == "KRW" else r.price * usdkrw
        if ref_krw <= 0:
            continue
        premium_pct = (base_krw / ref_krw - 1) * 100
        cells.append(
            PremiumCell(
                coin=coin,
                base_exchange=base,
                ref_exchange=ref,
                base_price_krw=round(base_krw, 4),
                ref_price_krw=round(ref_krw, 4),
                premium_pct=round(premium_pct, 4),
                usdkrw=usdkrw,
                ts=min(b.ts, r.ts),
            )
        )
    return cells
