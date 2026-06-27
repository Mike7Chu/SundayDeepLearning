"""Redis 키 네이밍 (단일 진실원)."""
from __future__ import annotations


def ticker_key(exchange: str) -> str:
    """거래소별 최신 시세 해시. field=coin, value=json(TickerSnapshot)."""
    return f"ticker:{exchange}"


def tether_key(exchange: str) -> str:
    """국내 거래소의 USDT/KRW(원화 테더가). value=float(KRW). 김프 환산 기준."""
    return f"tether:{exchange}"


def perp_ticker_key(exchange: str) -> str:
    """거래소별 무기한선물(perp) 최신 시세 해시. field=coin, value=json(TickerSnapshot)."""
    return f"perp_ticker:{exchange}"


def funding_key(exchange: str) -> str:
    """거래소별 펀딩비 해시. field=coin, value=json{rate, interval_h, next_ts}."""
    return f"funding:{exchange}"


def wallet_key(exchange: str) -> str:
    """거래소별 입출금 상태 해시. field=coin, value=json{deposit, withdraw}."""
    return f"wallet:{exchange}"


# ===== 봇 (페이퍼/실행) =====
BOT_KILLSWITCH_KEY = "bot:killswitch"            # "1"이면 전 봇 정지


def bot_enabled_key(name: str) -> str:
    return f"bot:enabled:{name}"                  # "1"/"0"


def bot_state_key(name: str) -> str:
    return f"bot:state:{name}"                    # json 상태


def paper_positions_key(name: str) -> str:
    return f"paper:positions:{name}"             # hash coin->json position


def paper_fills_key(name: str) -> str:
    return f"paper:fills:{name}"                 # list(json fill)


# 은행 환율 키(폴백용). value=float(USD/KRW)
FX_USDKRW_KEY = "fx:USDKRW"

# 김프 실시간 스트림 pub/sub 채널
PREMIUM_CHANNEL = "stream:premium"
