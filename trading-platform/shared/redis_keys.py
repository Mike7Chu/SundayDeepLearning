"""Redis 키 네이밍 (단일 진실원)."""
from __future__ import annotations


def ticker_key(exchange: str) -> str:
    """거래소별 최신 시세 해시. field=coin, value=json(TickerSnapshot)."""
    return f"ticker:{exchange}"


# 환율 키. value=float(USD/KRW)
FX_USDKRW_KEY = "fx:USDKRW"

# 김프 실시간 스트림 pub/sub 채널
PREMIUM_CHANNEL = "stream:premium"
