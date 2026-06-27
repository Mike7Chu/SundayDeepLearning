"""무기한선물(perp) 어댑터 — 해외 거래소.

perp 최신가와 펀딩비(funding rate)를 수집한다. ccxt 통합 perp 심볼은
'{COIN}/USDT:USDT' 형식이며, defaultType=swap 으로 클라이언트를 띄운다.
"""
from __future__ import annotations

import asyncio
import logging
import time

from shared.schemas import ExchangeConfig, TickerSnapshot

logger = logging.getLogger(__name__)


def perp_symbol(coin: str) -> str:
    return f"{coin}/USDT:USDT"


def _coin_of(symbol: str) -> str:
    return symbol.split("/", 1)[0]


def _last_price(ticker: dict | None) -> float | None:
    if not ticker:
        return None
    price = ticker.get("last") or ticker.get("close") or ticker.get("markPrice")
    return float(price) if price else None


class PerpAdapter:
    def __init__(self, cfg: ExchangeConfig, coins: list[str]):
        import ccxt.async_support as ccxt  # 지연 임포트

        self.cfg = cfg
        self.coins = coins
        klass = getattr(ccxt, cfg.ccxt_id)
        self.client = klass({
            "enableRateLimit": True,
            "options": {"defaultType": "swap"},
        })

    async def fetch_tickers(self) -> dict[str, TickerSnapshot]:
        symbols = [perp_symbol(c) for c in self.coins]
        out: dict[str, TickerSnapshot] = {}
        now = time.time()
        try:
            if self.client.has.get("fetchTickers"):
                tickers = await self.client.fetch_tickers(symbols)
                for coin in self.coins:
                    price = _last_price(tickers.get(perp_symbol(coin)))
                    if price is not None:
                        out[coin] = TickerSnapshot(
                            coin=coin, price=price, quote="USDT", ts=now)
        except Exception as exc:
            logger.warning("[%s perp] tickers failed: %s", self.cfg.name, exc)
        return out

    async def fetch_funding(self) -> dict[str, float]:
        symbols = [perp_symbol(c) for c in self.coins]
        out: dict[str, float] = {}
        try:
            if self.client.has.get("fetchFundingRates"):
                rates = await self.client.fetch_funding_rates(symbols)
                for sym, info in rates.items():
                    rate = info.get("fundingRate")
                    if rate is not None:
                        out[_coin_of(sym)] = float(rate)
            elif self.client.has.get("fetchFundingRate"):
                results = await asyncio.gather(
                    *[self.client.fetch_funding_rate(perp_symbol(c)) for c in self.coins],
                    return_exceptions=True,
                )
                for coin, res in zip(self.coins, results):
                    if isinstance(res, dict) and res.get("fundingRate") is not None:
                        out[coin] = float(res["fundingRate"])
        except Exception as exc:
            logger.warning("[%s perp] funding failed: %s", self.cfg.name, exc)
        return out

    async def close(self) -> None:
        await self.client.close()
