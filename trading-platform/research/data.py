"""리서치 대상 종목 데이터 수집.

1차 소스는 collector가 적재한 Redis `stock:quote`(현재가 + per/pbr/eps/bps 등).
키가 없거나 비어 있으면 가용 데이터만으로 진행(키 없이도 idle-safe).
"""
from __future__ import annotations

import json

import redis.asyncio as aioredis
from pydantic import BaseModel

from api.services.stock_score import compute_score
from shared.redis_keys import STOCK_MARKET_KEY, STOCK_QUOTE_KEY, stock_ohlcv_key


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
    ni_growth_pct: float | None = None  # 순이익 YoY %(DART 연간 사업보고서)
    ni_growth_q_pct: float | None = None   # 최근 분기 순이익 YoY %(전년 동기 대비)
    ni_growth_q_label: str | None = None   # 예: "2026.1Q"
    score: float | None = None        # 투자 매력도 0~100
    verdict: str | None = None        # 판정
    margin_pct: float | None = None   # 안전마진 %
    score_reasons: list[str] = []     # 축별 근거
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
        ni_growth_pct=quote.get("ni_growth_pct"),
        ni_growth_q_pct=quote.get("ni_growth_q_pct"),
        ni_growth_q_label=quote.get("ni_growth_q_label"),
        news=news or [],
    )


async def gather(redis: aioredis.Redis, code: str) -> StockData | None:
    """Redis에서 시세/밸류에이션 + 투자 매력도 스코어를 모아 StockData 생성.

    관심종목(stock:quote) 없으면 전체시장(stock:market)에서. 둘 다 없으면 None.
    """
    raw = await redis.hget(STOCK_QUOTE_KEY, code) or await redis.hget(STOCK_MARKET_KEY, code)
    if not raw:
        return None
    try:
        quote = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    closes: list = []
    oraw = await redis.get(stock_ohlcv_key(code))
    if oraw:
        try:
            closes = [c["close"] for c in json.loads(oraw)
                      if isinstance(c, dict) and c.get("close")]
        except (json.JSONDecodeError, TypeError):
            closes = []
    sd = from_quote(quote)
    sc = compute_score(quote, closes)
    sd.score, sd.verdict, sd.margin_pct = sc["score"], sc["verdict"], sc.get("margin_pct")
    sd.score_reasons = sc.get("reasons", [])
    return sd


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
        _fmt("순이익 YoY(연간 사업보고서)", d.ni_growth_pct, "%"),
        _fmt(f"최근 분기 순이익 YoY({d.ni_growth_q_label or '분기'}, 전년 동기 대비)",
             d.ni_growth_q_pct, "%"),
        _fmt("투자매력도(0~100)", d.score),
        _fmt("판정", d.verdict),
        _fmt("안전마진(그레이엄 대비)", d.margin_pct, "%"),
    ]
    if d.price and d.low_52w:
        up = (d.price / d.low_52w - 1) * 100
        lines.append(f"- 최근 1년 저점 대비 등락: {up:+.0f}% (실측 — 시장 대세를 반영한 실제 수치)")
    if d.score_reasons:
        lines.append("정량 근거: " + " · ".join(d.score_reasons))
    if d.news:
        lines.append("최근 뉴스:")
        lines += [f"  · {n}" for n in d.news]
    return "\n".join(lines)
