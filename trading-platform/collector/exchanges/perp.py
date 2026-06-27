"""무기한선물(perp) 어댑터 — 해외 거래소.

perp 최신가 + 펀딩비(rate)를 수집하되, **정산주기(interval)와 다음 정산시각**도 함께
파싱한다(거래소·코인별로 8H/4H/1H 등 상이). USDT 선형 perp만 대상.
"""
from __future__ import annotations

import logging
import re
import time

from shared.schemas import ExchangeConfig, TickerSnapshot
from shared.symbols import is_leveraged_token, parse_symbol

logger = logging.getLogger(__name__)

_INTERVAL_RE = re.compile(r"(\d+(?:\.\d+)?)\s*h", re.IGNORECASE)


def _last_price(ticker: dict | None) -> float | None:
    if not ticker:
        return None
    price = ticker.get("last") or ticker.get("close") or ticker.get("markPrice")
    return float(price) if price else None


def _interval_hours(info: dict) -> float | None:
    """ccxt funding 응답에서 정산주기(시간)를 추정. 예: 'interval'='8h' -> 8.0."""
    iv = info.get("interval")
    if isinstance(iv, str):
        m = _INTERVAL_RE.search(iv)
        if m:
            return float(m.group(1))
    return None


def _next_ts(info: dict) -> int | None:
    for k in ("nextFundingTimestamp", "fundingTimestamp"):
        v = info.get(k)
        if v:
            return int(v)
    return None


def _is_usdt_perp(symbol: str) -> bool:
    # 선형 USDT 무기한: 'BTC/USDT:USDT'
    return symbol.endswith(":USDT") and "/USDT:" in symbol


class PerpAdapter:
    def __init__(self, cfg: ExchangeConfig, exclude: set[str] | None = None):
        import ccxt.async_support as ccxt  # 지연 임포트

        self.cfg = cfg
        self.exclude = exclude or set()
        klass = getattr(ccxt, cfg.ccxt_id)
        self.client = klass({
            "enableRateLimit": True,
            "options": {"defaultType": "swap"},
        })

    def _accept(self, coin: str) -> bool:
        return coin.upper() not in self.exclude and not is_leveraged_token(coin)

    async def fetch_tickers(self) -> dict[str, TickerSnapshot]:
        out: dict[str, TickerSnapshot] = {}
        now = time.time()
        try:
            tickers = await self.client.fetch_tickers()
            for symbol, t in tickers.items():
                if not _is_usdt_perp(symbol):
                    continue
                coin, _ = parse_symbol(symbol)
                if not self._accept(coin):
                    continue
                price = _last_price(t)
                if price is not None:
                    out[coin] = TickerSnapshot(
                        coin=coin, price=price, quote="USDT", ts=now)
        except Exception as exc:
            logger.warning("[%s perp] tickers failed: %s", self.cfg.name, exc)
        return out

    async def fetch_funding(self) -> dict[str, dict]:
        """coin -> {rate, interval_h, next_ts}."""
        out: dict[str, dict] = {}
        try:
            if not self.client.has.get("fetchFundingRates"):
                return out
            rates = await self.client.fetch_funding_rates()
            for symbol, info in rates.items():
                if not _is_usdt_perp(symbol):
                    continue
                coin, _ = parse_symbol(symbol)
                rate = info.get("fundingRate")
                if not self._accept(coin) or rate is None:
                    continue
                out[coin] = {
                    "rate": float(rate),
                    "interval_h": _interval_hours(info),
                    "next_ts": _next_ts(info),
                }
        except Exception as exc:
            logger.warning("[%s perp] funding failed: %s", self.cfg.name, exc)
        return out

    async def close(self) -> None:
        await self.client.close()
