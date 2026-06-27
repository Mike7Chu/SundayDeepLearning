"""공통 데이터 스키마 (pydantic)."""
from __future__ import annotations

from pydantic import BaseModel


class ExchangeConfig(BaseModel):
    name: str          # 내부 이름 (upbit, gate ...)
    ccxt_id: str       # ccxt 거래소 id (gateio ...)
    quote: str         # KRW | USDT
    region: str        # domestic | overseas


class Universe(BaseModel):
    coins: list[str]
    exchanges: dict[str, ExchangeConfig]

    def symbol_for(self, exchange: str, coin: str) -> str:
        """ccxt 심볼 문자열. 예: BTC/KRW, BTC/USDT."""
        return f"{coin}/{self.exchanges[exchange].quote}"

    @property
    def domestic(self) -> list[str]:
        return [n for n, c in self.exchanges.items() if c.region == "domestic"]

    @property
    def overseas(self) -> list[str]:
        return [n for n, c in self.exchanges.items() if c.region == "overseas"]


class TickerSnapshot(BaseModel):
    coin: str
    price: float       # 마켓 통화 기준 (KRW 또는 USDT)
    quote: str         # KRW | USDT
    ts: float          # epoch seconds (수집 시각)


class PremiumCell(BaseModel):
    """기준(국내) 거래소 대비 한 해외 거래소의 김프.

    두 기준을 함께 제공:
      - premium_pct      : 테더(USDT/KRW) 기준 — 알림용(실제 차익 신호)
      - premium_coin_pct : 코인/환율(USD/KRW) 기준 — 화면 표출용(통상의 김프)
    """
    coin: str
    base_exchange: str
    ref_exchange: str
    base_price_krw: float
    ref_price_krw: float        # 코인(환율) 기준 환산 해외가 — 화면 표시용
    premium_pct: float          # 테더 기준 (알림)
    premium_coin_pct: float     # 코인/환율 기준 (화면)
    tether_rate: float          # 환산에 쓴 원화 테더가(USDT/KRW)
    forex_rate: float           # 환산에 쓴 은행 환율(USD/KRW)
    ts: float
