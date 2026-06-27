"""ccxt 기반 현물 거래소 어댑터.

거래소의 모든 티커를 한 번에 받아(quote 필터: KRW/USDT) 전 코인을 수집한다.
"""
from __future__ import annotations

import logging
import time

from shared.schemas import ExchangeConfig, TickerSnapshot
from shared.symbols import is_leveraged_token, parse_symbol

logger = logging.getLogger(__name__)


class ExchangeAdapter:
    def __init__(self, cfg: ExchangeConfig, exclude: set[str] | None = None):
        import ccxt.async_support as ccxt  # 지연 임포트

        self.cfg = cfg
        self.exclude = exclude or set()
        klass = getattr(ccxt, cfg.ccxt_id)
        self.client = klass({"enableRateLimit": True})

    def _accept(self, coin: str) -> bool:
        return coin.upper() not in self.exclude and not is_leveraged_token(coin)

    async def fetch(self) -> dict[str, TickerSnapshot]:
        """coin -> TickerSnapshot. cfg.quote 마켓의 전 코인."""
        out: dict[str, TickerSnapshot] = {}
        now = time.time()
        try:
            tickers = await self.client.fetch_tickers()
            for symbol, t in tickers.items():
                coin, quote = parse_symbol(symbol)
                if quote != self.cfg.quote or not self._accept(coin):
                    continue
                price = _last_price(t)
                if price is not None:
                    out[coin] = TickerSnapshot(
                        coin=coin, price=price, quote=self.cfg.quote, ts=now)
        except Exception as exc:
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

    async def close(self) -> None:
        await self.client.close()


def _last_price(ticker: dict | None) -> float | None:
    if not ticker:
        return None
    price = ticker.get("last") or ticker.get("close")
    return float(price) if price else None
