"""리서치 대상 종목 데이터 수집.

1차 소스는 collector가 적재한 Redis `stock:quote`(현재가 + per/pbr/eps/bps 등).
키가 없거나 비어 있으면 가용 데이터만으로 진행(키 없이도 idle-safe).
"""
from __future__ import annotations

import json

import redis.asyncio as aioredis
from pydantic import BaseModel

from shared.redis_keys import STOCK_QUOTE_KEY


class StockData(BaseModel):
    code: str
    name: str = ""
    price: float | None = None
    change_pct: float | None = None
    per: float | None = None
    pbr: float | None = None
    eps: float | None = None
    bps: float | None = None
    market_cap: float | None = None   # 억원
    high_52w: float | None = None
    low_52w: float | None = None
    news: list[str] = []

    def has_fundamentals(self) -> bool:
        return any(v is not None for v in (self.per, self.pbr, self.eps, self.bps))


def from_quote(quote: dict, news: list[str] | None = None) -> StockData:
    """Redis stock:quote 한 종목 dict → StockData (순수 함수)."""
    return StockData(
        code=str(quote.get("code", "")),
        name=quote.get("name", ""),
        price=quote.get("price"),
        change_pct=quote.get("change_pct"),
        per=quote.get("per"),
        pbr=quote.get("pbr"),
        eps=quote.get("eps"),
        bps=quote.get("bps"),
        market_cap=quote.get("market_cap"),
        high_52w=quote.get("high_52w"),
        low_52w=quote.get("low_52w"),
        news=news or [],
    )


async def gather(redis: aioredis.Redis, code: str) -> StockData | None:
    """Redis에서 해당 종목 시세/밸류에이션을 모아 StockData 생성.

    종목이 stock:quote에 아직 없으면 None(수집 대기/미설정).
    """
    raw = await redis.hget(STOCK_QUOTE_KEY, code)
    if not raw:
        return None
    try:
        quote = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return from_quote(quote)


def _fmt(label: str, v, suffix: str = "") -> str:
    return f"- {label}: {'미상' if v is None else f'{v}{suffix}'}"


def format_for_prompt(d: StockData) -> str:
    """StockData → Claude 프롬프트에 넣을 한국어 데이터 블록."""
    lines = [
        f"종목: {d.name or '?'} ({d.code})",
        _fmt("현재가", d.price, "원"),
        _fmt("전일대비", d.change_pct, "%"),
        _fmt("PER", d.per),
        _fmt("PBR", d.pbr),
        _fmt("EPS", d.eps, "원"),
        _fmt("BPS", d.bps, "원"),
        _fmt("시가총액", d.market_cap, "억원"),
        _fmt("52주최고", d.high_52w, "원"),
        _fmt("52주최저", d.low_52w, "원"),
    ]
    if d.news:
        lines.append("최근 뉴스:")
        lines += [f"  · {n}" for n in d.news]
    return "\n".join(lines)
