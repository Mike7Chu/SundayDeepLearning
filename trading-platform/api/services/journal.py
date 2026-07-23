"""매매 일지 + AI 복기 — 매매 시점의 판단을 박제해 나중에 결과와 대조.

'그때 왜 샀지'를 감이 아니라 기록으로 복기 → 감정 매매를 줄이고 규칙을 개선한다.
- judgment_snapshot / review: 순수 함수(점수 스냅샷·결과 대조).
- record_trade: 매매 순간 판단을 스냅샷해 일지에 적재(자동 주문·수동 공용).
"""
from __future__ import annotations

import json
import time
import uuid

from api.services.stock_score import compute_score
from shared.redis_keys import (
    JOURNAL_KEY,
    STOCK_MARKET_KEY,
    STOCK_QUOTE_KEY,
    stock_ohlcv_key,
)

_MAX = 500


def judgment_snapshot(score: dict | None, supply: dict | None) -> dict:
    """매매 시점의 AI 판단 스냅샷(순수 함수) — 점수·판정·축·수급."""
    score = score or {}
    supply = supply or {}
    return {
        "score": score.get("score"),
        "verdict": score.get("verdict"),
        "confidence": score.get("confidence"),
        "value": score.get("value"), "quality": score.get("quality"),
        "growth": score.get("growth"), "momentum": score.get("momentum"),
        "timing": score.get("timing"),
        "supply_net_eok": supply.get("net_eok"),
    }


def review(entry: dict, cur_price: float | None) -> dict:
    """일지 항목 + 현재가 → 결과·복기(순수 함수).

    - ret_pct: 매수는 진입가 대비 상승이 이익, 매도는 하락이 이익(잘 팔았나).
    - verdict_ok: 당시 AI 판정과 실제 결과의 부합 여부(사후, 참고용).
    반환 entry에 {ret_pct, outcome, judged_ok} 덧붙인 dict.
    """
    out = dict(entry)
    price = entry.get("price")
    side = (entry.get("side") or "BUY").upper()
    if not price or not cur_price:
        out["ret_pct"] = None
        out["outcome"] = "진행 중"
        out["judged_ok"] = None
        return out
    raw = (cur_price / price - 1) * 100
    ret = raw if side == "BUY" else -raw          # 매도는 이후 하락이 '잘 판 것'
    out["ret_pct"] = round(ret, 2)
    out["outcome"] = "이익" if ret > 0 else ("손실" if ret < 0 else "보합")
    j = entry.get("judgment") or {}
    v = j.get("score")
    if v is None:
        out["judged_ok"] = None                   # 당시 판단 기록 없음
    elif side == "BUY":
        # 높은 점수에 샀는데 올랐다 / 낮은 점수에 샀는데 내렸다 → 판단 부합
        bullish = v >= 60
        out["judged_ok"] = (bullish and ret > 0) or (not bullish and ret <= 0)
    else:
        out["judged_ok"] = None                   # 매도 판단은 별도 기준(생략)
    return out


async def _quote(redis, code: str) -> dict:
    q: dict = {"code": code}
    for key in (STOCK_MARKET_KEY, STOCK_QUOTE_KEY):
        raw = await redis.hget(key, code)
        if raw:
            try:
                q.update({k: v for k, v in json.loads(raw).items() if v is not None})
            except (json.JSONDecodeError, TypeError):
                pass
    return q


async def _closes(redis, code: str) -> list[float]:
    raw = await redis.get(stock_ohlcv_key(code))
    if not raw:
        return []
    try:
        return [c["close"] for c in json.loads(raw)
                if isinstance(c, dict) and c.get("close")]
    except (json.JSONDecodeError, TypeError):
        return []


async def record_trade(redis, *, code: str, name: str, side: str,
                       qty: float, price: float, note: str = "",
                       source: str = "manual") -> dict:
    """매매 시점 판단(점수·수급)을 스냅샷해 일지에 기록. 자동 주문·수동 공용."""
    quote = await _quote(redis, code)
    sc = compute_score(quote, await _closes(redis, code))
    supply = None
    sd_raw = await redis.get(f"sd_last:{code}")           # 최근 조회된 수급(있으면)
    if sd_raw:
        try:
            supply = json.loads(sd_raw)
        except (json.JSONDecodeError, TypeError):
            supply = None
    entry = {
        "id": uuid.uuid4().hex[:12], "ts": time.time(),
        "code": code, "name": name or quote.get("name") or code,
        "side": (side or "BUY").upper(), "qty": qty, "price": price,
        "note": note, "source": source,
        "judgment": judgment_snapshot(sc, supply),
    }
    await redis.rpush(JOURNAL_KEY, json.dumps(entry, ensure_ascii=False))
    await redis.ltrim(JOURNAL_KEY, -_MAX, -1)
    return entry
