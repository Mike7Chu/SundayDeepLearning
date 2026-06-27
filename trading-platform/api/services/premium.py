"""김프/역프 계산 서비스.

환산 기준(basis)은 **원화 테더가(USDT/KRW, 기준 거래소)** 를 우선 사용한다.
  premium_pct = (국내가_KRW / (해외가_USDT * 테더가_KRW) - 1) * 100
테더가가 없으면 은행 환율(USD/KRW)로 폴백.
  양수 = 김프(국내가 비쌈), 음수 = 역프.
"""
from __future__ import annotations

import json

import redis.asyncio as aioredis

from shared.redis_keys import FX_USDKRW_KEY, tether_key, ticker_key
from shared.schemas import PremiumCell, TickerSnapshot
from shared.settings import settings
from shared.universe import load_universe


async def _load_tickers(redis: aioredis.Redis, exchange: str) -> dict[str, TickerSnapshot]:
    raw = await redis.hgetall(ticker_key(exchange))
    return {coin: TickerSnapshot(**json.loads(v)) for coin, v in raw.items()}


async def _usdkrw(redis: aioredis.Redis) -> float:
    val = await redis.get(FX_USDKRW_KEY)
    return float(val) if val else settings.fx_usdkrw_fallback


async def _conversion_rate(redis: aioredis.Redis, base: str) -> tuple[float, str]:
    """USDT→KRW 환산 레이트와 기준(basis) 결정.

    1순위: 기준(국내) 거래소의 원화 테더가(USDT/KRW). 2순위: 은행 환율.
    """
    tether = await redis.get(tether_key(base))
    if tether and float(tether) > 0:
        return float(tether), "tether"
    return await _usdkrw(redis), "forex"


async def compute_premium(
    redis: aioredis.Redis, base: str, ref: str
) -> list[PremiumCell]:
    """기준 국내 거래소(base) vs 해외 거래소(ref) 코인별 김프 (테더가 기준)."""
    universe = load_universe()
    if base not in universe.exchanges:
        raise ValueError(f"unknown base exchange: {base}")
    if ref not in universe.exchanges:
        raise ValueError(f"unknown ref exchange: {ref}")

    rate, basis = await _conversion_rate(redis, base)
    base_t = await _load_tickers(redis, base)
    ref_t = await _load_tickers(redis, ref)

    cells: list[PremiumCell] = []
    for coin in universe.coins:
        b = base_t.get(coin)
        r = ref_t.get(coin)
        if not b or not r or r.price <= 0:
            continue
        base_krw = b.price if b.quote == "KRW" else b.price * rate
        ref_krw = r.price if r.quote == "KRW" else r.price * rate
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
                rate=round(rate, 4),
                basis=basis,
                ts=min(b.ts, r.ts),
            )
        )
    return cells
