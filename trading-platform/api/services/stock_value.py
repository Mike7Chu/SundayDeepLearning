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

from shared.redis_keys import STOCK_MARKET_KEY, STOCK_QUOTE_KEY


async def load_quotes(redis: aioredis.Redis) -> list[dict]:
    """전체 시장(stock:market) ∪ 관심종목(stock:quote) 시세를 병합(코드 중복은 market 우선).

    3,600+종목 HGETALL+파싱은 Pi에서 수백 ms~수 초 — 여러 엔드포인트(전체종목·
    살까말까·저평가·배당)가 각자 스캔하지 않게 12초 공유 캐시(시세 스윕 주기보다 짧음).
    """
    from api.services.cache import get_or_compute

    async def _scan() -> list[dict]:
        merged: dict[str, dict] = {}
        for key in (STOCK_QUOTE_KEY, STOCK_MARKET_KEY):
            raw = await redis.hgetall(key)
            for v in raw.values():
                try:
                    q = json.loads(v)
                except (json.JSONDecodeError, TypeError):
                    continue
                if q.get("code"):
                    merged[q["code"]] = q
        return list(merged.values())
    return await get_or_compute("load_quotes", 12, _scan)


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
        # 최신 분기 실적(정기보고서 기준) — 화면에 '어느 분기 수치'인지 함께 표시
        "ni_growth_q_pct": q.get("ni_growth_q_pct"),
        "ni_growth_q_label": q.get("ni_growth_q_label"),
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


async def value_screener(redis: aioredis.Redis, limit: int = 0) -> dict:
    """전체 시장 마법공식 스크리너. limit>0이면 상위 N만."""
    quotes = await load_quotes(redis)
    out = compute_value(quotes)
    out["total"] = len(out["rows"])
    if limit > 0:
        out["rows"] = out["rows"][:limit]
    return out
