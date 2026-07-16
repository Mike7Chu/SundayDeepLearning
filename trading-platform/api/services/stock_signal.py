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


def ema_series(values: list[float], n: int) -> list[float] | None:
    """지수이동평균 시계열(values[n-1:]과 정렬). 데이터 부족 시 None."""
    if len(values) < n or n <= 0:
        return None
    k = 2 / (n + 1)
    e = sum(values[:n]) / n
    out = [e]
    for v in values[n:]:
        e = v * k + e * (1 - k)
        out.append(e)
    return out


def macd(closes: list[float], fast: int = 12, slow: int = 26,
         signal_n: int = 9) -> dict | None:
    """MACD(12·26·9) — 추세의 방향과 전환(순수 함수). RSI보다 추세장에 강건.

    hist>0 = 상승 힘 우세. state: 직전 봉 대비 히스토그램 부호 전환(golden/dead).
    recent_golden: 최근 3봉 내 상승 전환(스윙 진입 타이밍).
    """
    if len(closes) < slow + signal_n:
        return None
    ef, es = ema_series(closes, fast), ema_series(closes, slow)
    line = [f - s for f, s in zip(ef[-len(es):], es)]
    sig = ema_series(line, signal_n)
    if not sig:
        return None
    hist = [ln - s for ln, s in zip(line[-len(sig):], sig)]
    state = None
    if len(hist) >= 2:
        if hist[-1] > 0 >= hist[-2]:
            state = "golden"
        elif hist[-1] < 0 <= hist[-2]:
            state = "dead"
    recent_golden = (state == "golden")
    if len(hist) >= 4:
        recent_golden = any(h2 > 0 >= h1
                            for h1, h2 in zip(hist[-4:-1], hist[-3:]))
    return {"hist": round(hist[-1], 4), "line": round(line[-1], 4),
            "signal": round(sig[-1], 4), "state": state,
            "rising": len(hist) >= 2 and hist[-1] > hist[-2],
            "recent_golden": recent_golden}


def adx(candles: list[dict], n: int = 14) -> float | None:
    """ADX(14) — 추세 '강도'(순수 함수). 25↑ 강한 추세, 20↓ 횡보(방향 무관).

    스윙에서 '진짜 추세 vs 횡보'를 거르는 용도. 고가/저가 없는 데이터면 None.
    """
    rows = []
    for c in candles or []:
        h, low, cl = c.get("high"), c.get("low"), c.get("close")
        if None in (h, low, cl):
            return None
        rows.append((h, low, cl))
    if len(rows) < 2 * n + 1:
        return None
    trs, pdms, ndms = [], [], []
    for (ph, pl, pc), (h, low, _c) in zip(rows, rows[1:]):
        trs.append(max(h - low, abs(h - pc), abs(low - pc)))
        up, dn = h - ph, pl - low
        pdms.append(up if (up > dn and up > 0) else 0.0)
        ndms.append(dn if (dn > up and dn > 0) else 0.0)

    def wilder(xs: list[float]) -> list[float]:
        s = sum(xs[:n])
        out = [s]
        for x in xs[n:]:
            s = s - s / n + x
            out.append(s)
        return out

    dxs = []
    for a, p, q in zip(wilder(trs), wilder(pdms), wilder(ndms)):
        if a <= 0:
            continue
        pdi, ndi = 100 * p / a, 100 * q / a
        tot = pdi + ndi
        if tot:
            dxs.append(100 * abs(pdi - ndi) / tot)
    if len(dxs) < n:
        return None
    return round(sum(dxs[-n:]) / n, 1)


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
    m = macd(closes)

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
        "macd_hist": m["hist"] if m else None,
        "macd_state": m["state"] if m else None,
        "macd_up": (m["hist"] > 0) if m else None,
        "score": score, "signal": signal, "bars": len(closes),
    }


def pillar_precheck(open_: float | None, high: float | None,
                    close: float | None, value_eok: float | None) -> bool:
    """빛의기둥 1차 조건(당일 실시간 데이터로 판정, 순수 함수).

    거래대금 20억↑ · 양봉 · 몸통 > 윗꼬리×1.2. (수급 3배 급증은 직전 2일
    이력이 필요해 후속 확정 단계에서 검사 — 전 종목 장중 스캔용 프리필터)
    """
    if None in (open_, high, close) or not value_eok:
        return False
    return (value_eok >= 20 and open_ < close
            and (close - open_) > (high - close) * 1.2)


def candle_trading_value(c: dict) -> float | None:
    """캔들 1개의 거래대금(억원) = (H+L+O+C)/4 × V ÷ 1억 (순수 함수)."""
    try:
        h, l, o, cl, v = (c.get("high"), c.get("low"), c.get("open"),
                          c.get("close"), c.get("volume"))
        if None in (h, l, o, cl) or not v:
            return None
        return (h + l + o + cl) / 4 * v / 1e8
    except TypeError:
        return None


def light_pillar(candles: list[dict]) -> dict | None:
    """'빛의기둥' 수급 포착(순수 함수) — 마지막 봉 기준.

    수급 = (H+L+O+C)/4 × 거래량 ÷ 1억 = 거래대금(억원).
    조건: ①거래대금 20억↑ ②양봉(o<c) ③몸통 > 윗꼬리×1.2(고가 근처 마감)
          ④거래대금 ≥ 직전 2일 평균의 3배(수급 급증).
    보조 확인(원 전략): 볼밴 하단권·이평 위·테마 동반이면 신뢰↑ — 추격 매수 주의.
    """
    if len(candles) < 3:
        return None
    t, p1, p2 = candles[-1], candles[-2], candles[-3]
    v, v1, v2 = (candle_trading_value(t), candle_trading_value(p1),
                 candle_trading_value(p2))
    if v is None or v1 is None or v2 is None:
        return None
    o, h, c = t.get("open"), t.get("high"), t.get("close")
    if None in (o, h, c):
        return None
    avg2 = (v1 + v2) / 2
    pillar = (v >= 20 and o < c and (c - o) > (h - c) * 1.2
              and avg2 > 0 and v >= avg2 * 3)
    return {"pillar": pillar, "value_eok": round(v, 1),
            "surge_x": round(v / avg2, 1) if avg2 > 0 else None}


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


def trade_levels(closes: list[float], live_price: float | None = None,
                 kr: bool = True) -> dict | None:
    """매매 가격 가이드(순수 함수): 추천 매수가·손절가·목표가.

    - 추천 매수가: 상승추세면 SMA20 눌림목(추격 매수 방지), 아니면 현재가.
    - 손절가: 최근 20거래일 최저가의 3% 아래(지지 붕괴 시 탈출). 진입가 대비
      최소 -3%, 최대 -15%로 클램프(비정상 급등락 방어).
    - 목표가: 손익비 1:2 (기대이익 = 감수위험의 2배).
    - kr=True면 KRX 호가 단위 반올림, False(미국 티커)면 센트(0.01) 반올림.
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
    tick = krx_tick if kr else (lambda p: round(p, 2))
    return {
        "entry": tick(entry), "stop": tick(stop), "target": tick(target),
        "entry_basis": basis,
        "stop_pct": round((stop / entry - 1) * 100, 1),
        "target_pct": round((target / entry - 1) * 100, 1),
        "rr": 2.0, "trend_ok": trend_ok,
    }


def pillar_guide(candles: list[dict], live_price: float | None = None,
                 kr: bool = True) -> str | None:
    """빛의기둥 알림용 '언제 사고 팔까' 문구(순수 함수).

    수급 포착만 알려주면 행동을 못 정한다 → 매매 가격 가이드(trade_levels)로
    매수·손절·목표 + 추세 필터를 알림에 바로 붙인다.
    """
    closes = [c["close"] for c in (candles or [])
              if isinstance(c, dict) and c.get("close")]
    lv = trade_levels(closes, live_price, kr=kr)
    if not lv:
        return None
    f = (lambda v: f"{v:,.0f}원") if kr else (lambda v: f"${v:,.2f}")
    trend = ("상승 추세 ✓ — 눌림 진입 유효" if lv["trend_ok"]
             else "⚠️ 하락 추세 — 수급만 보고 진입 금지, 반등 확인 전 관망")
    return ("── 언제 사고 팔까 ──\n"
            f"· 매수: {f(lv['entry'])} ({lv['entry_basis']} — 추격 대신 눌림 대기)\n"
            f"· 손절: {f(lv['stop'])} ({lv['stop_pct']:+.1f}%) — 이탈 시 기계적 탈출\n"
            f"· 목표: {f(lv['target'])} ({lv['target_pct']:+.1f}%, 손익비 1:2)\n"
            f"· 추세 필터: {trend}")


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
