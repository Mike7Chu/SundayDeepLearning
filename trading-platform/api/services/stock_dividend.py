"""배당주 — 배당수익률 + 배당 캘린더 + 정기 적립(DRIP) 제안.

stock:dividend(KIS 배당 일정) + stock:quote(현재가)로 연배당/수익률을 산출.
배당 데이터가 없으면 수익률은 None(키 입력 후 채워짐).
"""
from __future__ import annotations

import json
from datetime import date

import redis.asyncio as aioredis

from shared.redis_keys import STOCK_DIVIDEND_KEY, STOCK_QUOTE_KEY


def _today_str() -> str:
    return date.today().strftime("%Y%m%d")


def compute_dividend(quote: dict, items: list[dict]) -> dict:
    """현재가 + 배당항목 → 연배당/수익률/다음 배당기준일(순수 함수)."""
    price = quote.get("price")
    today = _today_str()
    # 최근 12개월 배당 합(연배당 추정)
    annual = round(sum(i.get("per_share") or 0 for i in items), 4) if items else None
    yield_pct = round(annual / price * 100, 2) if (annual and price) else None
    upcoming = [i for i in items if (i.get("date") or "") >= today]
    next_ex = upcoming[0]["date"] if upcoming else None
    return {
        "code": quote.get("code"), "name": quote.get("name"), "price": price,
        "annual_per_share": annual, "yield_pct": yield_pct,
        "next_ex_date": next_ex, "count": len(items),
    }


def drip_plan(rows: list[dict], monthly_budget: float) -> list[dict]:
    """월 적립예산을 수익률 상위 종목에 균등 배분(정기 적립 매수 제안)."""
    ranked = [r for r in rows if r.get("yield_pct")]
    ranked.sort(key=lambda r: r["yield_pct"], reverse=True)
    top = ranked[:5]
    if not top:
        return []
    each = monthly_budget / len(top)
    for r in top:
        r["monthly_alloc"] = round(each, 0)
        r["est_shares"] = int(each // r["price"]) if r.get("price") else None
    return top


async def dividend_view(redis: aioredis.Redis, monthly_budget: float = 0.0) -> dict:
    quotes_raw = await redis.hgetall(STOCK_QUOTE_KEY)
    div_raw = await redis.hgetall(STOCK_DIVIDEND_KEY)
    quotes = {}
    for v in quotes_raw.values():
        try:
            q = json.loads(v)
            quotes[q.get("code")] = q
        except (json.JSONDecodeError, TypeError):
            continue
    rows: list[dict] = []
    for code, q in quotes.items():
        items = []
        if code in div_raw:
            try:
                items = json.loads(div_raw[code]).get("items", [])
            except (json.JSONDecodeError, TypeError):
                items = []
        rows.append(compute_dividend(q, items))
    rows.sort(key=lambda r: (r["yield_pct"] is None, -(r["yield_pct"] or 0)))
    out = {"rows": rows}
    if monthly_budget > 0:
        out["drip"] = drip_plan(rows, monthly_budget)
    return out
