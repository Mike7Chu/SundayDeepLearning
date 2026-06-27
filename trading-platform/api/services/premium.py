"""김프/역프 계산 서비스.

두 기준을 함께 산출한다:
  - 테더 기준(알림용):   premium_pct      = (국내KRW / (해외USDT * 테더가KRW)) - 1
  - 코인/환율 기준(화면): premium_coin_pct = (국내KRW / (해외USDT * 환율KRW))   - 1
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


async def _tether_rate(redis: aioredis.Redis, base: str, forex: float) -> float:
    """기준(국내) 거래소의 원화 테더가(USDT/KRW). 없으면 환율로 폴백."""
    val = await redis.get(tether_key(base))
    return float(val) if val and float(val) > 0 else forex


def _to_krw(snap: TickerSnapshot, rate: float) -> float:
    """티커 가격을 KRW로 환산. KRW 마켓이면 그대로, USDT면 rate 곱."""
    return snap.price if snap.quote == "KRW" else snap.price * rate


async def compute_premium(
    redis: aioredis.Redis, base: str, ref: str
) -> list[PremiumCell]:
    """기준 국내 거래소(base) vs 해외 거래소(ref) 코인별 김프(테더·코인 기준 동시)."""
    universe = load_universe()
    if base not in universe.exchanges:
        raise ValueError(f"unknown base exchange: {base}")
    if ref not in universe.exchanges:
        raise ValueError(f"unknown ref exchange: {ref}")

    forex = await _usdkrw(redis)
    tether = await _tether_rate(redis, base, forex)
    base_t = await _load_tickers(redis, base)
    ref_t = await _load_tickers(redis, ref)

    cells: list[PremiumCell] = []
    for coin in universe.coins:
        b = base_t.get(coin)
        r = ref_t.get(coin)
        if not b or not r or r.price <= 0:
            continue
        base_krw = _to_krw(b, forex)         # 국내는 KRW라 레이트 무관
        ref_krw_coin = _to_krw(r, forex)     # 코인/환율 기준
        ref_krw_tether = _to_krw(r, tether)  # 테더 기준
        if ref_krw_coin <= 0 or ref_krw_tether <= 0:
            continue
        cells.append(
            PremiumCell(
                coin=coin,
                base_exchange=base,
                ref_exchange=ref,
                base_price_krw=round(base_krw, 4),
                ref_price_krw=round(ref_krw_coin, 4),
                premium_pct=round((base_krw / ref_krw_tether - 1) * 100, 4),
                premium_coin_pct=round((base_krw / ref_krw_coin - 1) * 100, 4),
                tether_rate=round(tether, 4),
                forex_rate=round(forex, 4),
                ts=min(b.ts, r.ts),
            )
        )
    return cells
