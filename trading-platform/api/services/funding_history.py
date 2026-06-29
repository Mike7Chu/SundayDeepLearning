"""펀비 히스토리 — 코인 상세에서 거래소별 과거 펀딩비(시간×거래소) 온디맨드 조회.

저장(시계열 DB) 없이, 코인 상세를 열 때 ccxt fetch_funding_rate_history로 거래소별
과거 펀비를 가져와 시각 기준으로 정렬·병합한다. 비용 큰 호출이라 Redis 5분 캐시.
ccxt 클라이언트는 모듈 레벨로 캐시(거래소당 1개, load_markets 1회).
"""
from __future__ import annotations

import json

import redis.asyncio as aioredis

from collector.exchanges.perp import _is_usdt_perp
from shared.universe import load_universe

_CACHE_TTL = 300
_clients: dict[str, object] = {}


def merge_history(per_ex: dict[str, dict[int, float]], limit: int) -> list[dict]:
    """{ex: {ts_ms: rate_pct}} → 시각 내림차순 행 [{ts, by_ex:{ex:rate_pct}}]."""
    times: set[int] = set()
    for d in per_ex.values():
        times.update(d.keys())
    rows = []
    for ts in sorted(times, reverse=True)[:limit]:
        by_ex = {ex: d[ts] for ex, d in per_ex.items() if ts in d}
        if by_ex:
            rows.append({"ts": ts, "by_ex": by_ex})
    return rows


async def _client(ex: str, ccxt_id: str):
    if ex not in _clients:
        import ccxt.async_support as ccxt
        c = getattr(ccxt, ccxt_id)({"enableRateLimit": True,
                                    "options": {"defaultType": "swap"}})
        await c.load_markets()
        _clients[ex] = c
    return _clients[ex]


def _perp_symbol(client, coin: str) -> str | None:
    for sym, m in (client.markets or {}).items():
        if _is_usdt_perp(m) and (m.get("base") or "").upper() == coin:
            return sym
    return None


async def funding_history(redis: aioredis.Redis, coin: str, limit: int = 60) -> dict:
    coin = coin.upper()
    cache_key = f"fundhist:{coin}"
    cached = await redis.get(cache_key)
    if cached:
        try:
            return json.loads(cached)
        except (json.JSONDecodeError, TypeError):
            pass

    universe = load_universe()
    per_ex: dict[str, dict[int, float]] = {}
    for ex in universe.overseas:
        try:
            client = await _client(ex, universe.exchanges[ex].ccxt_id)
            if not client.has.get("fetchFundingRateHistory"):
                continue
            sym = _perp_symbol(client, coin)
            if not sym:
                continue
            hist = await client.fetch_funding_rate_history(sym, limit=limit)
            d: dict[int, float] = {}
            for h in hist:
                ts, rate = h.get("timestamp"), h.get("fundingRate")
                if ts and rate is not None:
                    d[int(ts)] = round(float(rate) * 100, 4)
            if d:
                per_ex[ex] = d
        except Exception:
            continue   # 거래소별 실패 격리

    out = {"coin": coin, "exchanges": list(per_ex.keys()),
           "rows": merge_history(per_ex, limit)}
    await redis.set(cache_key, json.dumps(out), ex=_CACHE_TTL)
    return out


async def close_clients() -> None:
    for c in _clients.values():
        try:
            await c.close()
        except Exception:
            pass
    _clients.clear()
