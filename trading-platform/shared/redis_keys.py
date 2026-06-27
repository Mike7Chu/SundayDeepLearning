"""Redis 키 네이밍 (단일 진실원)."""
from __future__ import annotations


def ticker_key(exchange: str) -> str:
    """거래소별 최신 시세 해시. field=coin, value=json(TickerSnapshot)."""
    return f"ticker:{exchange}"


def tether_key(exchange: str) -> str:
    """국내 거래소의 USDT/KRW(원화 테더가). value=float(KRW). 김프 환산 기준."""
    return f"tether:{exchange}"


# 은행 환율 키(폴백용). value=float(USD/KRW)
FX_USDKRW_KEY = "fx:USDKRW"

# 김프 실시간 스트림 pub/sub 채널
PREMIUM_CHANNEL = "stream:premium"
