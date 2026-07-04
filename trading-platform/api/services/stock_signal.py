"""시그널 기반 매매 — 기술적 지표(순수 함수, 일봉 종가 시계열 입력).

closes: 오래된→최신 순 종가 리스트.
지표: SMA 골든/데드크로스, RSI(과매수/과매도), 모멘텀(N일 수익률), 볼린저 위치.
aggregate 'signal'은 단순 룰 합산(매수/매도/중립) — 백테스트 후 가중치 조정 전 출발점.
"""
from __future__ import annotations

import json

import redis.asyncio as aioredis

from shared.redis_keys import stock_ohlcv_key


def sma(values: list[float], n: int) -> float | None:
    if len(values) < n or n <= 0:
        return None
    return round(sum(values[-n:]) / n, 4)


def rsi(values: list[float], n: int = 14) -> float | None:
    if len(values) < n + 1:
        return None
    gains, losses = 0.0, 0.0
    for i in range(-n, 0):
        diff = values[i] - values[i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses -= diff
    if losses == 0:
        return 100.0
    rs = (gains / n) / (losses / n)
    return round(100 - 100 / (1 + rs), 2)


def momentum_pct(values: list[float], n: int) -> float | None:
    if len(values) < n + 1 or values[-n - 1] == 0:
        return None
    return round((values[-1] / values[-n - 1] - 1) * 100, 2)


def bollinger_pos(values: list[float], n: int = 20, k: float = 2.0) -> float | None:
    """현재가의 볼린저밴드 내 위치(0=하단,1=상단). 표준편차 0이면 None."""
    if len(values) < n:
        return None
    window = values[-n:]
    mean = sum(window) / n
    var = sum((x - mean) ** 2 for x in window) / n
    sd = var ** 0.5
    if sd == 0:
        return None
    lower, upper = mean - k * sd, mean + k * sd
    return round((values[-1] - lower) / (upper - lower), 3)


def evaluate_signals(closes: list[float]) -> dict:
    """종가 시계열 → 지표 + 종합 시그널."""
    s20, s60 = sma(closes, 20), sma(closes, 60)
    prev20, prev60 = sma(closes[:-1], 20), sma(closes[:-1], 60)
    cross = None
    if None not in (s20, s60, prev20, prev60):
        if prev20 <= prev60 and s20 > s60:
            cross = "golden"
        elif prev20 >= prev60 and s20 < s60:
            cross = "dead"
    r = rsi(closes)
    mom = momentum_pct(closes, 60)
    boll = bollinger_pos(closes)

    score = 0
    if cross == "golden":
        score += 1
    elif cross == "dead":
        score -= 1
    if r is not None:
        if r < 30:
            score += 1
        elif r > 70:
            score -= 1
    if mom is not None:
        score += 1 if mom > 0 else -1
    signal = "buy" if score >= 2 else "sell" if score <= -2 else "neutral"

    return {
        "sma20": s20, "sma60": s60, "sma_cross": cross,
        "rsi": r, "rsi_state": (None if r is None else
                                "oversold" if r < 30 else "overbought" if r > 70 else "neutral"),
        "momentum_pct": mom, "bollinger_pos": boll,
        "score": score, "signal": signal, "bars": len(closes),
    }


def krx_tick(p: float) -> float:
    """KRX 호가 단위로 반올림(주문 가능한 가격으로)."""
    if p < 2000:
        t = 1
    elif p < 5000:
        t = 5
    elif p < 20000:
        t = 10
    elif p < 50000:
        t = 50
    elif p < 200000:
        t = 100
    elif p < 500000:
        t = 500
    else:
        t = 1000
    return round(p / t) * t


def trade_levels(closes: list[float], live_price: float | None = None) -> dict | None:
    """매매 가격 가이드(순수 함수): 추천 매수가·손절가·목표가.

    - 추천 매수가: 상승추세면 SMA20 눌림목(추격 매수 방지), 아니면 현재가.
    - 손절가: 최근 20거래일 최저가의 3% 아래(지지 붕괴 시 탈출). 진입가 대비
      최소 -3%, 최대 -15%로 클램프(비정상 급등락 방어).
    - 목표가: 손익비 1:2 (기대이익 = 감수위험의 2배).
    판단 보조용 — 매매 신호·수익 보장이 아님.
    """
    if len(closes) < 20:
        return None
    price = live_price or closes[-1]
    if not price or price <= 0:
        return None
    s20, s60 = sma(closes, 20), sma(closes, 60)
    entry = s20 if (s20 and price > s20) else price
    basis = "SMA20 눌림목" if (s20 and price > s20) else "현재가"
    stop = min(closes[-20:]) * 0.97
    stop = max(entry * 0.85, min(stop, entry * 0.97))   # 진입 대비 -3%~-15%
    target = entry + 2 * (entry - stop)
    trend_ok = bool(s60 and price > s60)
    return {
        "entry": krx_tick(entry), "stop": krx_tick(stop), "target": krx_tick(target),
        "entry_basis": basis,
        "stop_pct": round((stop / entry - 1) * 100, 1),
        "target_pct": round((target / entry - 1) * 100, 1),
        "rr": 2.0, "trend_ok": trend_ok,
    }


async def signals_for(redis: aioredis.Redis, code: str, name: str = "") -> dict | None:
    raw = await redis.get(stock_ohlcv_key(code))
    if not raw:
        return None
    try:
        candles = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    closes = [c["close"] for c in candles if isinstance(c, dict) and c.get("close")]
    if len(closes) < 20:
        return None
    return {"code": code, "name": name, **evaluate_signals(closes)}
