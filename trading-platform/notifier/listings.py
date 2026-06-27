"""신규상장 감지.

거래소의 거래가능 마켓 심볼 집합을 주기적으로 비교해, 새로 등장한 심볼을
'신규상장'으로 간주한다. 최초 1회는 조용히 시드(기존 상장 폭탄 알림 방지).
"""
from __future__ import annotations

import logging

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)


def parse_symbol(symbol: str) -> tuple[str, str]:
    """'BTC/KRW' -> ('BTC', 'KRW'). 형식이 다르면 (symbol, '')."""
    if "/" in symbol:
        base, quote = symbol.split("/", 1)
        return base, quote
    return symbol, ""


class MarketLister:
    """ccxt로 거래소의 현재 거래가능 심볼 집합을 조회."""

    def __init__(self, exchange_name: str, ccxt_id: str):
        import ccxt.async_support as ccxt  # 지연 임포트(순수 로직 테스트는 ccxt 불필요)

        self.name = exchange_name
        klass = getattr(ccxt, ccxt_id)
        self.client = klass({"enableRateLimit": True})

    async def current_symbols(self) -> set[str]:
        markets = await self.client.load_markets(reload=True)
        return set(markets.keys())

    async def close(self) -> None:
        await self.client.close()


async def detect_new_listings(
    redis: aioredis.Redis, exchange: str, symbols: set[str]
) -> list[str]:
    """저장된 심볼 집합과 비교해 새 심볼 목록 반환.

    최초 호출(미시드 상태)이면 전체를 시드하고 빈 목록 반환(조용히).
    이후 호출부터 신규 심볼을 반환한다.
    """
    if not symbols:
        return []
    key = f"listing:markets:{exchange}"
    primed_key = f"listing:primed:{exchange}"

    is_primed = bool(await redis.exists(primed_key))
    stored = set(await redis.smembers(key))
    new = sorted(symbols - stored)

    # 최신 심볼을 항상 저장에 반영
    await redis.sadd(key, *symbols)

    if not is_primed:
        await redis.set(primed_key, "1")
        logger.info("[%s] primed with %d markets (silent)", exchange, len(symbols))
        return []
    return new


def format_listing(exchange: str, symbol: str) -> str:
    base, quote = parse_symbol(symbol)
    pair = f"{base}/{quote}" if quote else base
    return f"🆕 신규상장 감지\n{pair} — {exchange}"
