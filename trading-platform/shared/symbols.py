"""ccxt 심볼 파싱/필터 공통 유틸."""
from __future__ import annotations

import re

# 레버리지 토큰(예: BTC3L, ETH5S) 정도만 보수적으로 제외.
# UP/DOWN/BULL/BEAR 는 실코인(JUP 등) 오탐 위험이 커서 제외하지 않는다.
_LEVERAGED = re.compile(r"\d+[LS]$")


def parse_symbol(symbol: str) -> tuple[str, str]:
    """'BTC/USDT' 또는 'BTC/USDT:USDT' -> ('BTC', 'USDT'). 형식 이상 시 (symbol, '')."""
    left = symbol.split(":", 1)[0]
    if "/" not in left:
        return left, ""
    base, quote = left.split("/", 1)
    return base, quote


def is_leveraged_token(coin: str) -> bool:
    return bool(_LEVERAGED.search(coin.upper()))
