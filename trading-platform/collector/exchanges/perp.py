"""무기한선물(perp) 어댑터 — 해외 거래소.

perp 최신가 + 펀딩비(rate)를 수집하되, **정산주기(interval)와 다음 정산시각**도 함께
파싱한다(거래소·코인별로 8H/4H/1H 등 상이). USDT 선형 perp만 대상.
"""
from __future__ import annotations

import logging
import re
import time

from shared.schemas import ExchangeConfig, TickerSnapshot
from shared.settings import settings
from shared.symbols import is_leveraged_token

logger = logging.getLogger(__name__)

_INTERVAL_RE = re.compile(r"(\d+(?:\.\d+)?)\s*h", re.IGNORECASE)
# 거래소 raw info에 들어오는 정산주기 키들(시간 단위로 환산)
_HOUR_KEYS = ("fundingIntervalHours", "funding_interval_hours", "fundingRateInterval",
              "fundingPeriod", "interval_hours")
_MINUTE_KEYS = ("fundingInterval", "fundingIntervalMinutes", "collectCycle",
                "funding_interval_minutes")


def _last_price(ticker: dict | None) -> float | None:
    if not ticker:
        return None
    price = ticker.get("last") or ticker.get("close") or ticker.get("markPrice")
    return float(price) if price else None


def _num(v) -> float | None:
    try:
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def _interval_hours(info: dict) -> float | None:
    """ccxt funding 응답에서 정산주기(시간)를 추정.

    우선순위: 통합필드 interval('8h') → raw info의 시간키 → raw info의 분키(/60).
    거래소별 필드가 제각각이라 가능한 소스를 폭넓게 확인한다(미상이면 None).
    """
    iv = info.get("interval")
    if isinstance(iv, str):
        m = _INTERVAL_RE.search(iv)
        if m:
            return float(m.group(1))
    if isinstance(iv, (int, float)) and iv > 0:   # 일부는 시간 숫자로 옴
        return float(iv)
    raw = info.get("info") if isinstance(info.get("info"), dict) else {}
    for k in _HOUR_KEYS:
        val = raw.get(k)
        if val is None:
            continue
        if isinstance(val, str):
            m = _INTERVAL_RE.search(val)   # '8h' 형태
            if m:
                return float(m.group(1))
        h = _num(val)
        if h:
            return h
    for k in _MINUTE_KEYS:
        mins = _num(raw.get(k))
        if mins:
            return round(mins / 60.0, 4)
    return None


def _next_ts(info: dict) -> int | None:
    for k in ("nextFundingTimestamp", "fundingTimestamp"):
        v = info.get(k)
        if v:
            return int(v)
    return None


def _is_usdt_perp(market: dict | None) -> bool:
    """거래중(active) 선형 USDT 무기한선물 마켓만."""
    if not market or market.get("active") is False:
        return False
    return bool(market.get("swap") and market.get("linear")
                and market.get("settle") == "USDT")


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
        self._markets_at = 0.0
        self._funding_at = 0.0

    def _accept(self, coin: str) -> bool:
        return coin.upper() not in self.exclude and not is_leveraged_token(coin)

    async def _reload_markets_if_due(self) -> None:
        """마켓 메타 주기적 새로고침 — 상폐 perp가 캐시에 남는 것 방지."""
        now = time.time()
        if now - self._markets_at >= settings.markets_reload_sec:
            try:
                await self.client.load_markets(reload=bool(self._markets_at))
                self._markets_at = now
            except Exception as exc:
                logger.warning("[%s perp] load_markets 실패: %s", self.cfg.name, exc)

    async def fetch_tickers(self) -> dict[str, TickerSnapshot]:
        out: dict[str, TickerSnapshot] = {}
        now = time.time()
        try:
            await self._reload_markets_if_due()
            tickers = await self.client.fetch_tickers()
            markets = self.client.markets or {}
            for symbol, t in tickers.items():
                if not _is_usdt_perp(markets.get(symbol)):
                    continue
                coin = (markets[symbol].get("base") or "").upper()
                if not coin or not self._accept(coin):
                    continue
                price = _last_price(t)
                if price is not None:
                    qv = t.get("quoteVolume") or (
                        float(t["baseVolume"]) * price if t.get("baseVolume") else None)
                    out[coin] = TickerSnapshot(
                        coin=coin, price=price, quote="USDT", ts=now,
                        quote_volume=float(qv) if qv else None)
        except Exception as exc:
            logger.warning("[%s perp] tickers failed: %s", self.cfg.name, exc)
        return out

    def _due_funding(self) -> bool:
        now = time.time()
        if now - self._funding_at < settings.funding_interval_sec:
            return False
        self._funding_at = now
        return True

    async def fetch_funding(self) -> dict[str, dict]:
        """coin -> {rate, interval_h, next_ts}.

        펀비는 시간 단위로 변하므로 funding_interval_sec 주기로만 갱신(시세보다 느리게).
        bulk(fetchFundingRates) 우선, 미지원(예: MEXC)이면 단건(fetchFundingRate) 폴백.
        """
        if not self._due_funding():
            return {}   # 아직 주기 전 → 빈값(수집기는 기존값 유지)
        out: dict[str, dict] = {}
        try:
            if self.client.has.get("fetchFundingRates"):
                rates = await self.client.fetch_funding_rates()
                out = self._parse_rates(rates)
            if not out and self.client.has.get("fetchFundingRate"):
                out = await self._fetch_funding_single()   # MEXC 등 폴백
        except Exception as exc:
            logger.warning("[%s perp] funding failed: %s", self.cfg.name, exc)
        return out

    def _parse_rates(self, rates: dict) -> dict[str, dict]:
        markets = self.client.markets or {}
        out: dict[str, dict] = {}
        for symbol, info in rates.items():
            if not _is_usdt_perp(markets.get(symbol)):
                continue
            coin = (markets[symbol].get("base") or "").upper()
            rate = info.get("fundingRate")
            if not coin or not self._accept(coin) or rate is None:
                continue
            out[coin] = {
                "rate": float(rate),
                "interval_h": _interval_hours(info),
                "next_ts": _next_ts(info),
            }
        return out

    async def _fetch_funding_single(self) -> dict[str, dict]:
        """bulk 미지원 거래소용 단건 폴백(부하 방지로 상한·동시성 제한)."""
        import asyncio

        markets = self.client.markets or {}
        symbols = [s for s, m in markets.items()
                   if _is_usdt_perp(m) and self._accept((m.get("base") or "").upper())]
        symbols = symbols[: settings.funding_single_cap]
        sem = asyncio.Semaphore(8)
        out: dict[str, dict] = {}

        async def one(symbol: str) -> None:
            async with sem:
                try:
                    info = await self.client.fetch_funding_rate(symbol)
                except Exception:
                    return
            coin = (markets[symbol].get("base") or "").upper()
            rate = info.get("fundingRate")
            if coin and rate is not None:
                out[coin] = {"rate": float(rate),
                             "interval_h": _interval_hours(info),
                             "next_ts": _next_ts(info)}

        await asyncio.gather(*(one(s) for s in symbols))
        logger.info("[%s perp] 단건 펀비 폴백 %d/%d", self.cfg.name, len(out), len(symbols))
        return out

    async def close(self) -> None:
        await self.client.close()
