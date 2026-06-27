"""ccxt 기반 거래소 어댑터.

각 거래소에서 유니버스 코인들의 현물 최신가를 조회한다.
가능하면 fetch_tickers(일괄)로, 미지원이면 코인별 fetch_ticker로 폴백.
"""
from __future__ import annotations

import asyncio
import logging
import time

import ccxt.async_support as ccxt

from shared.schemas import ExchangeConfig, TickerSnapshot

logger = logging.getLogger(__name__)


class ExchangeAdapter:
    def __init__(self, cfg: ExchangeConfig, coins: list[str]):
        self.cfg = cfg
        self.coins = coins
        klass = getattr(ccxt, cfg.ccxt_id)
        # 공개 시세만 사용(키 불필요). rate limit 준수.
        self.client = klass({"enableRateLimit": True})

    def symbol(self, coin: str) -> str:
        return f"{coin}/{self.cfg.quote}"

    async def fetch(self) -> dict[str, TickerSnapshot]:
        """coin -> TickerSnapshot. 실패한 코인은 생략."""
        symbols = [self.symbol(c) for c in self.coins]
        out: dict[str, TickerSnapshot] = {}
        now = time.time()
        try:
            if self.client.has.get("fetchTickers"):
                tickers = await self.client.fetch_tickers(symbols)
                for coin in self.coins:
                    t = tickers.get(self.symbol(coin))
                    price = _last_price(t)
                    if price is not None:
                        out[coin] = TickerSnapshot(
                            coin=coin, price=price, quote=self.cfg.quote, ts=now
                        )
            else:
                results = await asyncio.gather(
                    *[self._fetch_one(c) for c in self.coins],
                    return_exceptions=True,
                )
                for coin, res in zip(self.coins, results):
                    if isinstance(res, TickerSnapshot):
                        out[coin] = res
        except Exception as exc:  # 거래소 전체 실패는 로깅 후 빈 결과
            logger.warning("[%s] fetch failed: %s", self.cfg.name, exc)
        return out

    async def fetch_price(self, symbol: str) -> float | None:
        """단일 심볼 최신가(예: USDT/KRW). 실패 시 None."""
        try:
            t = await self.client.fetch_ticker(symbol)
            return _last_price(t)
        except Exception as exc:
            logger.warning("[%s] %s fetch failed: %s", self.cfg.name, symbol, exc)
            return None

    async def _fetch_one(self, coin: str) -> TickerSnapshot | None:
        t = await self.client.fetch_ticker(self.symbol(coin))
        price = _last_price(t)
        if price is None:
            return None
        return TickerSnapshot(
            coin=coin, price=price, quote=self.cfg.quote, ts=time.time()
        )

    async def close(self) -> None:
        await self.client.close()


def _last_price(ticker: dict | None) -> float | None:
    if not ticker:
        return None
    price = ticker.get("last") or ticker.get("close")
    return float(price) if price else None
