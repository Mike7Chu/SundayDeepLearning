"""가치투자 스크리너 — 보유/관심종목을 펀더멘털 지표로 랭킹.

수집된 stock:quote(per/pbr/eps/bps)만으로 계산하는 경량 스크리너:
  - 이익수익률(earnings yield) = EPS/주가 = 1/PER
  - 자본수익률 프록시 ROE = EPS/BPS
  - 마법공식(Greenblatt) 랭크 = (이익수익률 내림차순 순위 + ROE 내림차순 순위)
  - 간이 품질 플래그(흑자·저PBR·고ROE)
모두 순수 함수 — 데이터가 없으면 해당 지표는 None.
"""
from __future__ import annotations

import json

import redis.asyncio as aioredis

from shared.redis_keys import STOCK_QUOTE_KEY


def _roe(eps: float | None, bps: float | None) -> float | None:
    if eps is None or not bps:
        return None
    return round(eps / bps * 100, 2)


def _earnings_yield(eps: float | None, price: float | None, per: float | None) -> float | None:
    if price and eps is not None:
        return round(eps / price * 100, 2)
    if per and per > 0:
        return round(100 / per, 2)
    return None


def _metrics(q: dict) -> dict:
    eps, bps, price, per = q.get("eps"), q.get("bps"), q.get("price"), q.get("per")
    roe = _roe(eps, bps)
    ey = _earnings_yield(eps, price, per)
    quality = sum([
        eps is not None and eps > 0,          # 흑자
        (q.get("pbr") or 99) < 1.5,           # 저PBR
        (roe or 0) >= 10,                     # 고ROE
    ])
    return {
        "code": q.get("code"), "name": q.get("name"), "price": price,
        "per": per, "pbr": q.get("pbr"), "roe": roe, "earnings_yield": ey,
        "quality": quality,
    }


def compute_value(quotes: list[dict]) -> dict:
    """quote 리스트 → 마법공식 랭킹된 가치 스크리너 결과(순수 함수)."""
    rows = [_metrics(q) for q in quotes if q.get("code")]

    def _rank(rows: list[dict], key: str) -> dict[str, int]:
        ranked = sorted([r for r in rows if r[key] is not None],
                        key=lambda r: r[key], reverse=True)
        return {r["code"]: i + 1 for i, r in enumerate(ranked)}

    ey_rank = _rank(rows, "earnings_yield")
    roe_rank = _rank(rows, "roe")
    for r in rows:
        er, rr = ey_rank.get(r["code"]), roe_rank.get(r["code"])
        r["magic_rank"] = (er + rr) if (er and rr) else None

    # 마법공식 랭크 오름차순(작을수록 우량), 없는 건 뒤로
    rows.sort(key=lambda r: (r["magic_rank"] is None, r["magic_rank"] or 0,
                             -(r["quality"] or 0)))
    for i, r in enumerate(rows):
        r["value_rank"] = i + 1
    return {"rows": rows}


async def value_screener(redis: aioredis.Redis) -> dict:
    raw = await redis.hgetall(STOCK_QUOTE_KEY)
    quotes = []
    for v in raw.values():
        try:
            quotes.append(json.loads(v))
        except (json.JSONDecodeError, TypeError):
            continue
    return compute_value(quotes)
