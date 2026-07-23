"""터질 종목 발굴 레이더 — 급등 '전조'를 조합해 전 시장에서 후보를 추린다.

예측이 아니라 '지금 돈·가격·실적·추세가 동시에 깨어나는' 종목을 찾는 스크리너.
지앤씨에너지처럼 하루 급등하는 종목의 공통 전조:
  ① 거래대금 급증 — 돈이 몰린다(가장 강한 선행지표, 30점)
  ② 신고가 돌파/근접 — 위에 저항(매물)이 없는 구간(25점)
  ③ 당일 강한 장대양봉 — 급등 초입(20점)
  ④ 실적·공시 촉매 — 급등의 '명분'(15점)
  ⑤ 추세 전환 — 정배열·골든크로스(10점)
전부 순수 함수. 매수 신호·수익 보장이 아니며, 급등주는 되돌림 위험도 크다(면책).
"""
from __future__ import annotations

from api.services.stock_signal import candle_trading_value, evaluate_signals


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def turnover_surge(candles: list[dict]) -> tuple[float | None, float | None]:
    """(오늘 거래대금 억원, 최근 20일 평균 대비 배수) — 순수 함수.

    급등의 1순위 전조는 '평소보다 돈이 몇 배 몰렸나'다. 절대 규모(억)와
    상대 급증(배)을 함께 반환해 잔챙이(평소 5억→15억 3배)를 걸러낸다.
    """
    vals = [v for v in (candle_trading_value(c) for c in candles) if v is not None]
    if len(vals) < 6:
        return None, None
    today = vals[-1]
    prior = vals[-21:-1] if len(vals) > 21 else vals[:-1]
    avg = sum(prior) / len(prior) if prior else 0.0
    surge = round(today / avg, 1) if avg > 0 else None
    return round(today, 1), surge


def radar_score(quote: dict, candles: list[dict],
                has_flash: bool = False) -> dict | None:
    """전조 종합 점수(0~100) + 발화 신호(순수). 최소 조건 미달이면 None.

    최소 게이트: 오늘 거래대금 30억↑ AND 당일 상승 — '터지는 중'만 후보로.
    """
    closes = [c["close"] for c in candles if isinstance(c, dict) and c.get("close")]
    if len(closes) < 20:
        return None
    price = quote.get("price") or closes[-1]
    chg = quote.get("change_pct")
    today_eok, surge = turnover_surge(candles)
    if not today_eok or today_eok < 30 or (chg is not None and chg <= 0):
        return None                                   # 돈 안 몰렸거나 하락 → 제외
    signals: list[str] = []

    # ① 거래대금 급증 (30) — 1배→0, 3배↑→만점
    s_flow = _clamp(((surge or 1) - 1) / 2) * 30
    if surge and surge >= 2:
        signals.append(f"거래대금 {surge}배 급증({today_eok:.0f}억)")

    # ② 신고가 돌파/근접 (25) — 52주 고가 대비 위치
    hi = quote.get("high_52w") or max((c.get("high") or 0) for c in candles) or 0
    pos = price / hi if hi else 0.0
    s_high = _clamp((pos - 0.85) / 0.15) * 25
    if pos >= 1.0:
        signals.append("52주 신고가 돌파")
    elif pos >= 0.9:
        signals.append(f"신고가 근접({pos * 100:.0f}%)")

    # ③ 당일 강도 (20) — 등락률 15 + 장대양봉(고가 마감) 5
    s_day = _clamp((chg or 0) / 8) * 15
    last = candles[-1]
    o, h, c = last.get("open"), last.get("high"), last.get("close")
    if None not in (o, h, c) and c > o and (h - c) < (c - o) * 0.5:
        s_day += 5
        signals.append("장대양봉(고가 마감)")
    if chg and chg >= 5:
        signals.append(f"당일 +{chg:.1f}%")

    # ④ 실적·공시 촉매 (15) — 잠정실적 YoY 급증 우선, 없으면 최근 공시
    yoy = quote.get("flash_ni_yoy")
    if yoy is None:
        yoy = quote.get("flash_op_yoy")
    s_cat = 0.0
    if yoy is not None:
        s_cat = _clamp((yoy + 10) / 60) * 15
        if yoy >= 30:
            signals.append(f"실적 급증 {yoy:+.0f}%(잠정)")
    elif has_flash:
        s_cat = 6.0
        signals.append("최근 실적·공시")

    # ⑤ 추세 전환 (10)
    sig = evaluate_signals(closes)
    s20, s60 = sig.get("sma20"), sig.get("sma60")
    s_tr = 0.0
    if s20 and s60 and s20 > s60:
        s_tr += 6
        signals.append("정배열")
    if sig.get("sma_cross") == "golden":
        s_tr += 4
        signals.append("골든크로스")

    total = round(s_flow + s_high + s_day + s_cat + s_tr, 1)
    return {
        "code": quote.get("code"), "name": quote.get("name"),
        "price": price, "change_pct": chg, "radar": total,
        "value_eok": today_eok, "surge_x": surge, "pos_52w": round(pos, 3),
        "flash_yoy": yoy, "signals": signals,
    }


def radar_pool(quotes: list[dict], ranking_codes: list[str],
               flash_codes: list[str], held: set[str], cap: int = 30) -> list[str]:
    """레이더 후보군 선정(순수) — 온디맨드 캔들 조회 비용을 cap으로 제한.

    우선순위: ①토스 랭킹(이미 움직임 — 급등·거래대금 상위) ②실적·공시 촉매
    ③신고가 근접+당일 상승. 보유·동전주(<1,000원)·미국 티커 제외.
    """
    pool: list[str] = []
    seen = set(held)

    def add(code: str) -> None:
        if code and code.isdigit() and len(code) == 6 and code not in seen:
            seen.add(code)
            pool.append(code)

    for c in ranking_codes:
        add(c)
    for c in flash_codes:
        add(c)
    ranked: list[tuple[float, str]] = []
    for q in quotes:
        code, price = q.get("code"), q.get("price")
        if not code or not price or price < 1000:
            continue
        hi = q.get("high_52w")
        pos = price / hi if hi else 0.0
        chg = q.get("change_pct") or 0.0
        if pos >= 0.9 and chg > 1:
            ranked.append((pos + chg / 100, code))
    ranked.sort(reverse=True)
    for _, code in ranked:
        add(code)
    return pool[:cap]


def market_regime(indicators: dict | None) -> dict:
    """지수·수급으로 '레이더 신뢰 환경' 한 줄(순수 함수).

    돌파 전략은 위험선호장(코스닥 강세·외국인 순매수)에서 성공률이 높고,
    위험회피장에선 가짜 돌파(되돌림)가 잦다 — 후보 신뢰도의 배경으로 노출.
    """
    if not isinstance(indicators, dict):
        return {"tone": "unknown",
                "note": "시장 지표 없음 — 개별 종목 전조만 참고하세요"}
    kq = (indicators.get("kosdaq") or {}).get("change_pct")
    ks = (indicators.get("kospi") or {}).get("change_pct")
    inv = ((indicators.get("investor") or {}).get("kosdaq") or {})
    foreign = inv.get("foreigner")
    idx = f"코스피 {ks:+.1f}%·코스닥 {kq:+.1f}%" if (ks is not None and kq is not None) else ""
    ftxt = (f" · 외국인 코스닥 {foreign:+,.0f}억" if foreign is not None else "")
    risk_on = (kq or 0) > 0.3 or (foreign or 0) > 0
    risk_off = (kq or 0) < -0.7 or (foreign or 0) < -1000
    if risk_on and not risk_off:
        return {"tone": "risk_on",
                "note": f"위험선호장({idx}{ftxt}) — 돌파 후보 신뢰 편이나 분할·손절은 필수"}
    if risk_off:
        return {"tone": "risk_off",
                "note": f"위험회피장({idx}{ftxt}) — 가짜 돌파(되돌림) 잦음, 소액·관찰 권장"}
    return {"tone": "neutral",
            "note": f"중립장({idx}{ftxt}) — 종목별 전조로 선별, 무리한 추격 금지"}
