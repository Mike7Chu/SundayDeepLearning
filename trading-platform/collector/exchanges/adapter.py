"""ccxt 기반 현물 거래소 어댑터.

거래소의 모든 티커를 한 번에 받아(quote 필터: KRW/USDT) 전 코인을 수집한다.
"""
from __future__ import annotations

import logging
import time

from shared.schemas import ExchangeConfig, TickerSnapshot
from shared.settings import settings
from shared.symbols import is_leveraged_token

logger = logging.getLogger(__name__)


class ExchangeAdapter:
    def __init__(self, cfg: ExchangeConfig, exclude: set[str] | None = None):
        import ccxt.async_support as ccxt  # 지연 임포트

        self.cfg = cfg
        self.exclude = exclude or set()
        klass = getattr(ccxt, cfg.ccxt_id)
        # defaultType=spot 명시: bybit/okx 등은 fetch_tickers에 카테고리(spot)가 없으면
        # 비현물(linear)을 반환해 현물 필터에 전부 걸려 0개가 됨 → 현물 카테고리 고정.
        self.client = klass({"enableRateLimit": True,
                             "options": {"defaultType": "spot"}})
        self._markets_at = 0.0

    def _accept(self, coin: str) -> bool:
        return coin.upper() not in self.exclude and not is_leveraged_token(coin)

    async def _reload_markets_if_due(self) -> None:
        """마켓 메타를 주기적으로 새로고침 — 상폐 코인이 캐시에 남아 stale 가격을
        내보내는 것을 막는다(수집기는 장기 구동이라 최초 1회 캐시가 굳어짐)."""
        now = time.time()
        if now - self._markets_at >= settings.markets_reload_sec:
            try:
                await self.client.load_markets(reload=bool(self._markets_at))
                self._markets_at = now
            except Exception as exc:
                logger.warning("[%s] load_markets 실패: %s", self.cfg.name, exc)

    async def fetch(self) -> dict[str, TickerSnapshot]:
        """coin -> TickerSnapshot. cfg.quote 의 **거래중(active)·현물** 마켓만.

        bybit 등은 fetch_tickers가 현물 카테고리 없이 호출되면 0개를 반환할 수 있어,
        결과가 비면 params={'category':'spot'}로 1회 재시도한다.
        """
        now = time.time()
        try:
            await self._reload_markets_if_due()
            out = await self._collect(now, None)
            if not out:
                out = await self._collect(now, {"category": "spot"})
            return out
        except Exception as exc:
            logger.warning("[%s] fetch failed: %s", self.cfg.name, exc)
            return {}

    async def _collect(self, now: float, params: dict | None) -> dict[str, TickerSnapshot]:
        out: dict[str, TickerSnapshot] = {}
        tickers = await (self.client.fetch_tickers(params=params) if params
                         else self.client.fetch_tickers())
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
            if price is None:
                continue
            qv = _quote_volume(t, price)
            # 24h 거래대금이 0이면 상폐/거래중지된 데드마켓(active 거짓양성이어도 제거)
            if settings.drop_zero_volume and qv is not None and qv <= 0:
                continue
            out[coin] = TickerSnapshot(
                coin=coin, price=price, quote=self.cfg.quote, ts=now,
                quote_volume=qv, margin=_margin_flag(m))
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


def _margin_flag(market: dict) -> bool | None:
    """현물 마진(차입) 거래 가능 여부. ccxt market['margin'] (없으면 unknown=None)."""
    v = market.get("margin")
    return bool(v) if v is not None else None


def _quote_volume(ticker: dict, price: float) -> float | None:
    """24h 거래대금. quoteVolume 우선, 없으면 baseVolume*price."""
    qv = ticker.get("quoteVolume")
    if qv:
        return float(qv)
    bv = ticker.get("baseVolume")
    return float(bv) * price if bv else None
