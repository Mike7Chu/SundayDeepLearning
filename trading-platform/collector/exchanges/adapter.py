"""ccxt 기반 현물 거래소 어댑터.

거래소의 모든 티커를 한 번에 받아(quote 필터: KRW/USDT) 전 코인을 수집한다.
"""
from __future__ import annotations

import logging
import time

from shared.schemas import ExchangeConfig, TickerSnapshot
from shared.symbols import is_leveraged_token

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
        """coin -> TickerSnapshot. cfg.quote 의 **거래중(active)·현물** 마켓만."""
        out: dict[str, TickerSnapshot] = {}
        now = time.time()
        try:
            tickers = await self.client.fetch_tickers()
            markets = self.client.markets or {}
            for symbol, t in tickers.items():
                m = markets.get(symbol)
                # 상장폐지/거래중지(active=False) 또는 비현물 마켓은 제외(썩은 가격 차단)
                if not m or m.get("active") is False or not m.get("spot"):
                    continue
                if m.get("quote") != self.cfg.quote:
                    continue
                coin = (m.get("base") or "").upper()
                if not coin or not self._accept(coin):
                    continue
                price = _last_price(t)
                if price is not None:
                    out[coin] = TickerSnapshot(
                        coin=coin, price=price, quote=self.cfg.quote, ts=now,
                        quote_volume=_quote_volume(t, price))
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


def _quote_volume(ticker: dict, price: float) -> float | None:
    """24h 거래대금. quoteVolume 우선, 없으면 baseVolume*price."""
    qv = ticker.get("quoteVolume")
    if qv:
        return float(qv)
    bv = ticker.get("baseVolume")
    return float(bv) * price if bv else None
