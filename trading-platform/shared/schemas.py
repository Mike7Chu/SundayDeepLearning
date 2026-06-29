"""공통 데이터 스키마 (pydantic)."""
from __future__ import annotations

from pydantic import BaseModel


class ExchangeConfig(BaseModel):
    name: str          # 내부 이름 (upbit, gate ...)
    ccxt_id: str       # ccxt 거래소 id (gateio ...)
    quote: str         # KRW | USDT
    region: str        # domestic | overseas
    options: dict = {}  # 거래소별 ccxt options (예: bybit {defaultType: spot})


class Universe(BaseModel):
    exchanges: dict[str, ExchangeConfig]
    exclude: set[str] = set()      # 제외할 base 코인(스테이블 등)

    def quote_of(self, exchange: str) -> str:
        return self.exchanges[exchange].quote

    def is_excluded(self, coin: str) -> bool:
        return coin.upper() in self.exclude

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
    quote_volume: float | None = None   # 24h 거래대금 (KRW 또는 USDT)
    margin: bool | None = None          # 현물 마진(차입) 거래 가능 여부 — 현물 숏 가능성 판단


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
    # 국내현물 vs 해외선물(perp) — 현선(현물매수+선물숏) 기회 판단용. perp 없으면 None.
    premium_perp_pct: float | None = None        # 테더 기준
    premium_perp_coin_pct: float | None = None   # 코인/환율 기준
    ref_perp_price_krw: float | None = None      # 해외 선물 환산가(코인 기준)
    base_volume_krw: float | None = None         # 국내 24h 거래대금(KRW) — 알림 볼륨 필터
