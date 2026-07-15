"""투자 매력도 스코어 — 전문가 멀티팩터(가치·품질·모멘텀·타이밍)를 0~100으로 통합.

'지금 사야 하나?'에 답하는 단일 점수 + 한 줄 판정. 근거는 4축으로 분해.
설계 근거(웹서치): 기관/퀀트는 단일 지표가 아니라 가치+품질+모멘텀을 조합하고,
가치투자는 내재가치 대비 안전마진에서 매수. 여기선 그레이엄 넘버(√(22.5·EPS·BPS))를
내재가치 프록시로 써 안전마진(%)을 계산한다. 모두 순수 함수 — 테스트 용이.

투자 판단 보조일 뿐이며 매매 신호·수익 보장이 아니다(면책).
"""
from __future__ import annotations

import math

from api.services.stock_signal import evaluate_signals


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def graham_number(eps: float | None, bps: float | None) -> float | None:
    """벤저민 그레이엄 내재가치 프록시 = √(22.5 × EPS × BPS). 적자·음수면 None."""
    if eps is None or bps is None or eps <= 0 or bps <= 0:
        return None
    return round(math.sqrt(22.5 * eps * bps), 1)


def margin_of_safety(price: float | None, eps: float | None, bps: float | None) -> float | None:
    """안전마진 % = (그레이엄넘버 − 현재가)/그레이엄넘버 × 100. 양수=저평가."""
    g = graham_number(eps, bps)
    if not g or not price:
        return None
    return round((g - price) / g * 100, 1)


def _value_axis(q: dict) -> tuple[float, list[str]]:
    """가치 30점: 이익수익률·ROE·PBR·안전마진 (트레일링 기준 — 성장 축이 보완)."""
    price, per, pbr = q.get("price"), q.get("per"), q.get("pbr")
    eps, bps = q.get("eps"), q.get("bps")
    ey = (eps / price * 100) if (eps and price) else (100 / per if (per and per > 0) else None)
    roe = (eps / bps * 100) if (eps and bps) else None
    mos = margin_of_safety(price, eps, bps)
    s_ey = _clamp(ey / 12) if ey is not None else 0.0          # EY 12%+ 만점
    s_roe = _clamp(roe / 15) if roe is not None else 0.0       # ROE 15%+ 만점
    s_pbr = _clamp((3 - pbr) / 2.2) if (pbr and pbr > 0) else 0.0  # PBR 0.8↓ 만점, 3↑ 0
    s_mos = _clamp(mos / 30) if mos is not None else 0.0       # 안전마진 30%+ 만점
    score = 30 * (0.35 * s_ey + 0.25 * s_roe + 0.20 * s_pbr + 0.20 * s_mos)
    reasons = []
    if ey is not None and ey >= 8:
        reasons.append(f"이익수익률 {ey:.1f}%")
    if mos is not None and mos > 0:
        reasons.append(f"안전마진 {mos:.0f}%")
    if pbr and pbr < 1:
        reasons.append(f"PBR {pbr:.2f}")
    return round(score, 1), reasons


def _quality_axis(q: dict) -> tuple[float, list[str]]:
    """품질 20점: 흑자·ROE·PBR·PER·안전마진 체크리스트."""
    price, per, pbr = q.get("price"), q.get("per"), q.get("pbr")
    eps, bps = q.get("eps"), q.get("bps")
    roe = (eps / bps * 100) if (eps and bps) else None
    mos = margin_of_safety(price, eps, bps)
    checks = [
        (eps is not None and eps > 0, "흑자"),
        (roe is not None and roe >= 10, "ROE 10%+"),
        (pbr is not None and 0 < pbr < 1.5, "저PBR"),
        (per is not None and 0 < per < 15, "저PER"),
        (mos is not None and mos > 0, "그레이엄 저평가"),
    ]
    passed = [label for ok, label in checks if ok]
    return round(20 * len(passed) / len(checks), 1), passed


def _growth_axis(q: dict) -> tuple[float, list[str]]:
    """성장 15점: 순이익 YoY(DART 공식) — 트레일링 PER 함정 보정.

    이익이 급증하는 변곡점(예: AI·HBM 사이클)에서는 트레일링 PER이 높아도
    시장이 미래 이익을 반영 중일 수 있다. 데이터 없으면 중립(5점).
    우선순위: 잠정실적(발표 당일 공시 원문에서 추출, 가장 최신) → 분기보고서 → 연간.
    """
    gf = q.get("flash_ni_yoy")
    if gf is None:
        gf = q.get("flash_op_yoy")              # 순이익 없으면 영업이익 YoY로
    gq = q.get("ni_growth_q_pct")               # 최근 분기(전년 동기 대비)
    if gf is not None:
        g, label = gf, q.get("flash_label") or "잠정"
    elif gq is not None:
        g, label = gq, q.get("ni_growth_q_label")
    else:
        g, label = q.get("ni_growth_pct"), "연간"
    if g is None:
        return 5.0, []                          # 미수집 → 중립(가점·감점 없음)
    s = _clamp((g + 10) / 60)                    # -10%↓=0점, +50%↑=만점
    reasons = []
    if g >= 15:
        reasons.append(f"순이익 {g:+.0f}%({label})")
    elif g < 0:
        reasons.append(f"이익 감소 {g:.0f}%({label})")
    return round(15 * s, 1), reasons


def _momentum_axis(closes: list[float]) -> tuple[float, list[str], dict]:
    """모멘텀·추세 25점: 정배열·현재가>SMA60·3개월·6개월 모멘텀. 일봉 없으면 0."""
    if len(closes) < 20:
        return 0.0, [], {}
    sig = evaluate_signals(closes)
    s20, s60 = sig.get("sma20"), sig.get("sma60")
    price = closes[-1]
    mom3 = sig.get("momentum_pct")
    mom6 = None
    if len(closes) > 121 and closes[-121]:
        mom6 = round((price / closes[-121] - 1) * 100, 2)
    checks = [
        (s20 is not None and s60 is not None and s20 > s60, "정배열"),
        (s60 is not None and price > s60, "SMA60 상회"),
        (mom3 is not None and mom3 > 0, "3개월 +"),
        (mom6 is not None and mom6 > 0, "6개월 +"),
    ]
    passed = [label for ok, label in checks if ok]
    score = 25 * len(passed) / len(checks)
    if sig.get("sma_cross") == "golden":
        reasons_extra = ["골든크로스"]
    else:
        reasons_extra = []
    return round(score, 1), passed + reasons_extra, sig


def _timing_axis(q: dict, closes: list[float], sig: dict) -> tuple[float, list[str]]:
    """타이밍 10점: RSI 과열 아님(5) + 52주 하단 근접(5). 일봉 없으면 5(중립)."""
    if not sig:
        return 5.0, []
    reasons = []
    rsi = sig.get("rsi")
    rsi_score = 2.5 if rsi is None else (5.0 if rsi <= 70 else 0.0)
    if rsi is not None and rsi <= 70:
        reasons.append(f"RSI {rsi:.0f}")
    price = q.get("price") or (closes[-1] if closes else None)
    hi, lo = q.get("high_52w"), q.get("low_52w")
    entry_score = 5.0
    if price and hi and lo and hi > lo:
        pos = (price - lo) / (hi - lo)                # 0=저점,1=고점
        entry_score = round(5 * _clamp((0.8 - pos) / 0.8), 2)
        if pos <= 0.5:
            reasons.append("52주 하단권")
    return round(rsi_score + entry_score, 1), reasons


def _verdict(score: float) -> str:
    if score >= 75:
        return "적극 매수 검토"
    if score >= 60:
        return "분할매수 구간"
    if score >= 45:
        return "관찰"
    return "관망"


def compute_score(quote: dict, closes: list[float] | None = None) -> dict:
    """merged quote(+일봉 종가) → 투자 매력도 0~100 + 판정 + 축별 근거.

    가치30 + 품질20 + 성장15 + 추세25 + 타이밍10. 성장 축이 트레일링 가치
    지표의 사이클 함정(이익 급증기에 '비싸 보임')을 보정한다.
    """
    closes = closes or []
    v, vr = _value_axis(quote)
    ql, qr = _quality_axis(quote)
    gr, grr = _growth_axis(quote)
    mo, mr, sig = _momentum_axis(closes)
    tm, tr = _timing_axis(quote, closes, sig)
    total = round(v + ql + gr + mo + tm, 1)
    reasons = vr + qr + grr + mr + tr
    return {
        "code": quote.get("code"), "name": quote.get("name"), "price": quote.get("price"),
        "score": total, "verdict": _verdict(total),
        "value": v, "quality": ql, "growth": gr, "momentum": mo, "timing": tm,
        "margin_pct": margin_of_safety(quote.get("price"), quote.get("eps"), quote.get("bps")),
        "graham": graham_number(quote.get("eps"), quote.get("bps")),
        "ni_growth_pct": quote.get("ni_growth_pct"),
        "ni_growth_q_pct": quote.get("ni_growth_q_pct"),
        "ni_growth_q_label": quote.get("ni_growth_q_label"),
        "flash_ni_yoy": quote.get("flash_ni_yoy"),
        "flash_op_yoy": quote.get("flash_op_yoy"),
        "flash_label": quote.get("flash_label"),
        "has_chart": bool(closes),
        "reasons": reasons,
    }
