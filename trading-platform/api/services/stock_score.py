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


def _ttm_vals(q: dict) -> tuple:
    """(eps, bps, per, roe) — TTM(최근 4분기, DART 분기 반영) 우선, 없으면 KIS 연간 폴백.

    KIS eps/bps/per는 직전 사업보고서(작년 말) 고정 → 올해 실적이 반영되도록 TTM 우선.
    """
    eps = q.get("eps_ttm") if q.get("eps_ttm") is not None else q.get("eps")
    bps = q.get("bps_ttm") if q.get("bps_ttm") is not None else q.get("bps")
    per = q.get("per_ttm") if q.get("per_ttm") is not None else q.get("per")
    roe = q.get("roe_ttm") if q.get("roe_ttm") is not None else (
        (eps / bps * 100) if (eps and bps) else None)
    return eps, bps, per, roe


def _value_axis(q: dict) -> tuple[float, list[str]]:
    """가치 30점: 이익수익률·ROE·PBR·안전마진 (TTM 우선, 성장 축이 보완)."""
    price, pbr = q.get("price"), q.get("pbr")
    eps, bps, per, roe = _ttm_vals(q)
    ey = (eps / price * 100) if (eps and price) else (100 / per if (per and per > 0) else None)
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
    """품질 20점: 흑자·ROE·PBR·PER·안전마진 체크리스트 (TTM 우선)."""
    price, pbr = q.get("price"), q.get("pbr")
    eps, bps, per, roe = _ttm_vals(q)
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
    """타이밍 10점: MACD 상승 힘(5) + 52주 하단 근접(5). 일봉 없으면 5(중립).

    (RSI는 강한 추세에서 계속 과열로 읽혀 랠리 주도주를 감점하는 오판이 잦아
    추세 전환·방향에 강건한 MACD로 교체.)
    """
    if not sig:
        return 5.0, []
    reasons = []
    up = sig.get("macd_up")
    rsi_score = 2.5 if up is None else (5.0 if up else 0.0)
    if up:
        reasons.append("MACD 상승")
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
    # '매력도' 라벨 — 회사의 정적 매력(싼가·튼튼·크는가·오르는가)일 뿐, '지금 사라'는
    # 진입 타이밍(눌림목·과확장·지지/저항)과 별개. '매수' 단어가 혼란을 줘 재프레이밍.
    if score >= 75:
        return "매력 최상위"
    if score >= 60:
        return "매력 양호"
    if score >= 45:
        return "관찰"
    return "관망"


def _news_axis(news: dict | None) -> tuple[float, list[str]]:
    """뉴스 가감 ±5점 — 감성 스코어(-1~+1)×5. 뉴스 없으면 0(중립·가감 없음).

    호재/악재 뉴스가 매력도에 소폭 반영되게 한다(HONG STOCK 벤치마크·사용자 요청).
    과도 반영을 막으려 ±5로 제한 — 핵심은 재무·추세, 뉴스는 보조 신호.
    """
    if not news or news.get("n") in (None, 0) or news.get("score") is None:
        return 0.0, []
    s = max(-1.0, min(1.0, news["score"]))
    adj = round(s * 5, 1)
    reasons = []
    if abs(adj) >= 1:
        reasons.append(f"뉴스 {news.get('label', '')} {adj:+.0f}점(공시 {news.get('n')}건)")
    return adj, reasons


def compute_score(quote: dict, closes: list[float] | None = None,
                  news: dict | None = None) -> dict:
    """merged quote(+일봉 종가, +뉴스 감성) → 투자 매력도 0~100 + 판정 + 축별 근거.

    가치30 + 품질20 + 성장15 + 추세25 + 타이밍10 (=100) ± 뉴스 5. 성장 축이
    트레일링 가치 지표의 사이클 함정을 보정하고, 뉴스는 소폭 가감(보조).
    """
    closes = closes or []
    v, vr = _value_axis(quote)
    ql, qr = _quality_axis(quote)
    gr, grr = _growth_axis(quote)
    mo, mr, sig = _momentum_axis(closes)
    tm, tr = _timing_axis(quote, closes, sig)
    nw, nwr = _news_axis(news)
    has_fund = quote.get("eps") is not None and quote.get("bps") is not None
    has_growth = any(quote.get(k) is not None for k in
                     ("flash_ni_yoy", "flash_op_yoy", "ni_growth_q_pct",
                      "ni_growth_pct"))
    has_chart = len(closes) >= 20
    # 데이터 있는 축만으로 100점 환산 — 재무 결측(미국주식 등)이 구조적으로 저평가되지
    # 않게(가치·품질이 0이라 최대 50점에 갇히던 문제). 전 데이터 있는 국내는 종전과 동일.
    parts = [(v, 30, has_fund), (ql, 20, has_fund), (gr, 15, has_growth),
             (mo, 25, has_chart), (tm, 10, has_chart)]
    got = sum(s for s, m, ok in parts if ok)
    cap = sum(m for s, m, ok in parts if ok)
    base = (got / cap * 100.0) if cap else 0.0
    # 상한 = 증거 비례. 데이터가 적을수록(cap↓) '매력 최상위 100'을 주장할 수 없게 제한.
    # 특히 차트(추세·타이밍)가 없으면 '지금 사도 되는 흐름인가'를 확인 못 하므로 상한을 크게
    # 낮춘다 — 재무만으로 싸 보이는 종목(가치 트랩·비유동 소형주)이 100점으로 뜨던 문제 차단.
    ceiling = 60.0 + 40.0 * (cap / 100.0)
    if not has_chart:
        ceiling = min(ceiling, 55.0)
    total = round(max(0.0, min(base + nw, ceiling, 100.0)), 1)
    reasons = vr + qr + grr + mr + tr + nwr
    # 축별 분해(설명가능성) — applied=이 종목에 데이터가 있어 점수에 반영됐는가.
    axes = [
        {"key": "value", "label": "가치", "score": v, "max": 30, "reasons": vr,
         "applied": has_fund, "desc": "이익·자산 대비 싼가(이익수익률·ROE·PBR·안전마진)"},
        {"key": "quality", "label": "품질", "score": ql, "max": 20, "reasons": qr,
         "applied": has_fund, "desc": "돈 잘 버는 튼튼한 회사인가(흑자·ROE·저PER/PBR)"},
        {"key": "growth", "label": "성장", "score": gr, "max": 15, "reasons": grr,
         "applied": has_growth, "desc": "이익이 크는가(순이익 YoY·잠정실적)"},
        {"key": "momentum", "label": "추세", "score": mo, "max": 25, "reasons": mr,
         "applied": has_chart, "desc": "오르는 흐름인가(정배열·SMA60·모멘텀)"},
        {"key": "timing", "label": "타이밍", "score": tm, "max": 10, "reasons": tr,
         "applied": has_chart, "desc": "지금 살 때인가(MACD·52주 위치)"},
        {"key": "news", "label": "뉴스", "score": round(5 + nw, 1), "max": 10,
         "reasons": nwr, "applied": bool(news and news.get("n")),
         "desc": "최근 공시·뉴스 감성(±5 가감 · 5=중립)"},
    ]
    # 신뢰도: 점수를 구성한 데이터가 얼마나 채워졌나(0~100). 낮으면 '점수가 낮다'가 아니라
    # '판단 근거가 부족'(예: 미국주식은 재무 결측 → 신뢰도 낮게, 점수는 있는 축으로 환산).
    checks = [has_fund, has_fund, has_growth, len(closes) >= 60, has_chart]
    confidence = round(100 * sum(checks) / len(checks))
    return {
        "code": quote.get("code"), "name": quote.get("name"), "price": quote.get("price"),
        "score": total, "verdict": _verdict(total), "confidence": confidence,
        "value": v, "quality": ql, "growth": gr, "momentum": mo, "timing": tm,
        "news_adj": nw, "news_sent": news, "axes": axes,
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
