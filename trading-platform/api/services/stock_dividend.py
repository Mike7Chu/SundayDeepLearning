"""배당주 — 배당수익률 + 배당 캘린더 + 정기 적립(DRIP) 제안.

stock:dividend(KIS 배당 일정) + stock:quote(현재가)로 연배당/수익률을 산출.
배당 데이터가 없으면 수익률은 None(키 입력 후 채워짐).
"""
from __future__ import annotations

import json
from datetime import date

import redis.asyncio as aioredis

from api.services.stock_value import load_quotes
from shared.redis_keys import STOCK_DIVIDEND_KEY


def _today_str() -> str:
    return date.today().strftime("%Y%m%d")


def compute_dividend(quote: dict, items: list[dict]) -> dict:
    """현재가 + 배당항목 → 연도별 이력 + 연배당/수익률/다음 기준일(순수 함수).

    items는 최대 ~3년치. 연도별로 주당배당금을 합산해 history를 만들고,
    가장 최근 연도 합을 연배당(annual)으로 본다.
    """
    price = quote.get("price")
    today = _today_str()
    by_year: dict[str, float] = {}
    for i in items:
        d = i.get("date") or ""
        ps = i.get("per_share")
        if len(d) >= 4 and ps is not None:
            by_year[d[:4]] = round(by_year.get(d[:4], 0.0) + ps, 2)
    history = [{"year": y, "per_share": v,
               "yield_pct": round(v / price * 100, 2) if price else None}
              for y, v in sorted(by_year.items(), reverse=True)]
    annual = history[0]["per_share"] if history else None
    yield_pct = round(annual / price * 100, 2) if (annual and price) else None
    upcoming = [i for i in items if (i.get("date") or "") >= today]
    next_ex = upcoming[0]["date"] if upcoming else None
    return {
        "code": quote.get("code"), "name": quote.get("name"), "price": price,
        "annual_per_share": annual, "yield_pct": yield_pct,
        "next_ex_date": next_ex, "count": len(items), "history": history,
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
    # 전체시장(stock:market) ∪ 관심(stock:quote) 병합 → 배당 데이터 있는 종목 전부 랭킹.
    quotes = {q["code"]: q for q in await load_quotes(redis) if q.get("code")}
    div_raw = await redis.hgetall(STOCK_DIVIDEND_KEY)
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
