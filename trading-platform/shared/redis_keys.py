"""Redis 키 네이밍 (단일 진실원) — 주식 플랫폼."""
from __future__ import annotations

# 주식 시세 해시. field=종목코드, value=json{code,name,price,change_pct,per,pbr,...,ts}
STOCK_QUOTE_KEY = "stock:quote"

# 주식 배당 해시. field=종목코드, value=json{code, items:[{date, per_share, yield_pct}], ts}
STOCK_DIVIDEND_KEY = "stock:dividend"

# AI 리서치 리포트 해시. field=종목코드, value=json{code,name,report,model,ts,...}
RESEARCH_KEY = "research:reports"

# 관심종목 오버라이드(대시보드 편집). value=json[{code,name}]. 없으면 config/stocks.yaml.
WATCHLIST_KEY = "stock:watchlist"


def stock_ohlcv_key(code: str) -> str:
    """종목 일봉 시계열. value=json[{date, close, high, low, volume}] (오래된→최신)."""
    return f"stock:ohlcv:{code}"
