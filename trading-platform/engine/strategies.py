"""전략 후보 5 (순수 함수) — 시황 라우터가 국면에 맞춰 골라 실행(전략 리포트 §03).

각 전략은 병합 시세(q: per/roe/부채/실적YoY/52주고가/등락률 등)와 일봉(candles)을 받아
'매수 픽'({strategy,label,score 0~100,entry,stop,target,reasons}) 또는 부적합 시 None을
반환한다. 서명이 같아 라우터가 동일하게 호출하고 최고 점수 하나를 고른다.

  S1 추세 모멘텀     신고가·정배열·상대강도 (강세 추세장)
  S2 퀄리티-밸류     TTM 저PER·고ROE·저부채·FCF (중립·순환장, 저회전)
  S3 저변동 방어     저변동·저부채·배당 (위험 회피장)
  S4 단기 평균회귀   과매도 반등+거래량 (횡보 레인지장)
  S5 실적 서프라이즈  실적 beat 후 표류(PEAD)+돌파 (뉴스·리스크온)
  S6 수급 모멘텀     외국인·기관 5일 순매집+추세 (한국 전용 선행지표)

예측 아님·판단 보조. 최종 심판은 비용 반영 net(백테스트/포워드로그). 재무 결측이면
해당 전략은 None(억지 매수 금지 — 리포트 P0 확신 하한과 일관).
"""
from __future__ import annotations

from api.services.stock_signal import (
    adx, bollinger_pos, macd, momentum_pct, rsi, sma, trade_levels,
)

STRATEGY_LABELS = {
    "S1": "추세 모멘텀", "S2": "퀄리티-밸류", "S3": "저변동 방어",
    "S4": "단기 평균회귀", "S5": "실적 서프라이즈(PEAD)", "S6": "수급 모멘텀",
}


def _series(candles: list) -> tuple[list, list, list, list]:
    """일봉 → (closes, highs, lows, vols) 오래된→최신, 결측 방어."""
    cs, hs, ls, vs = [], [], [], []
    for c in candles or []:
        if not isinstance(c, dict) or not c.get("close"):
            continue
        cs.append(c["close"])
        hs.append(c.get("high") or c["close"])
        ls.append(c.get("low") or c["close"])
        vs.append(c.get("volume") or c.get("vol") or 0)
    return cs, hs, ls, vs


def _stdev(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = sum(xs) / len(xs)
    return (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5


def _ttm(q: dict) -> tuple[float | None, float | None]:
    """PER·ROE (TTM 우선, 없으면 연간)."""
    per = q.get("per_ttm") if q.get("per_ttm") is not None else q.get("per")
    roe = q.get("roe_ttm") if q.get("roe_ttm") is not None else q.get("roe")
    return per, roe


def _levels(q: dict, price: float, closes: list, *,
            stop_pct: float | None = None, rr: float = 2.0) -> dict:
    """진입/손절/목표. 기본은 trade_levels(SMA20 눌림목·지지 기반), stop_pct 주면 고정%."""
    kr = str(q.get("code", "")).isdigit()
    if stop_pct is not None:
        entry = price
        stop = round(entry * (1 - stop_pct / 100), 2 if not kr else 0)
        target = round(entry * (1 + stop_pct * rr / 100), 2 if not kr else 0)
        return {"entry": entry, "stop": stop, "target": target}
    lv = trade_levels(closes, price, kr=kr) or {}
    return {"entry": lv.get("entry") or price,
            "stop": lv.get("stop"), "target": lv.get("target")}


# ── S1 추세 모멘텀 ─────────────────────────────────────────────────────────
def s1_momentum(q: dict, candles: list) -> dict | None:
    cs, _hs, _ls, _vs = _series(candles)
    if len(cs) < 60:
        return None
    price = q.get("price") or cs[-1]
    s20, s60 = sma(cs, 20), sma(cs, 60)
    if not (s20 and s60 and price > s60 and s20 > s60):
        return None                                  # 상승 정배열만
    if price > s20 * 1.15:
        return None                                  # 과확장 추격 금지
    mom = momentum_pct(cs, 60) or 0.0
    if mom <= 0:
        return None                                  # 절대 모멘텀 +만
    hi = q.get("high_52w")
    near_high = bool(hi and price >= hi * 0.90)
    a, m = adx(candles), macd(cs)
    score = 40 + min(30.0, mom)
    if near_high:
        score += 12
    if a is not None:
        score += 10 if a >= 25 else (5 if a >= 20 else 0)
    if m and m["hist"] > 0:
        score += 8
    score = min(100.0, score)
    if score < 55:
        return None
    reasons = [f"모멘텀 60일 {mom:+.0f}%", "정배열·SMA60↑"]
    if near_high:
        reasons.append("52주 신고가 근접")
    if a is not None:
        reasons.append(f"추세강도 {a:.0f}")
    return {"strategy": "S1", "label": STRATEGY_LABELS["S1"], "score": round(score, 1),
            "reasons": reasons, **_levels(q, price, cs)}


# ── S2 퀄리티-밸류 ─────────────────────────────────────────────────────────
def s2_quality_value(q: dict, candles: list) -> dict | None:
    per, roe = _ttm(q)
    if per is None or roe is None:
        return None                                  # 재무 필수(억지 매수 금지)
    if not (per > 0 and per <= 15) or roe < 8:
        return None                                  # 저평가 + 최소 품질
    cs, *_ = _series(candles)
    price = q.get("price") or (cs[-1] if cs else None)
    if not price:
        return None
    s60 = sma(cs, 60) if len(cs) >= 60 else None
    if s60 and price < s60 * 0.90:
        return None                                  # 폭락 종목 제외(가치 함정)
    debt, pbr, fcf = q.get("debt_ratio"), q.get("pbr"), q.get("fcf")
    val = min(35.0, (1.0 / per) * 100 * 2.5)         # 이익수익률
    qual = min(30.0, max(0.0, roe - 8) * 2)
    safe = (15 if debt <= 100 else 8 if debt <= 150 else 0) if debt is not None else 6
    pbr_pts = 10 if (pbr is not None and 0 < pbr < 1.5) else 0
    fcf_pts = 8 if (fcf is not None and fcf > 0) else 0
    score = min(100.0, val + qual + safe + pbr_pts + fcf_pts)
    if score < 55:
        return None
    reasons = [f"PER {per:.1f}·ROE {roe:.0f}%"]
    if debt is not None:
        reasons.append(f"부채 {debt:.0f}%")
    if pbr_pts:
        reasons.append("저PBR")
    if fcf_pts:
        reasons.append("FCF+")
    return {"strategy": "S2", "label": STRATEGY_LABELS["S2"], "score": round(score, 1),
            "reasons": reasons, **_levels(q, price, cs, stop_pct=10.0, rr=2.0)}


# ── S3 저변동 방어 ─────────────────────────────────────────────────────────
def s3_defensive(q: dict, candles: list) -> dict | None:
    cs, *_ = _series(candles)
    if len(cs) < 60:
        return None
    price = q.get("price") or cs[-1]
    rets = [cs[i] / cs[i - 1] - 1 for i in range(1, len(cs)) if cs[i - 1]]
    vol = _stdev(rets[-20:])
    if vol > 0.03:                                    # 일간 변동성 3%↑ = 방어주 아님
        return None
    s60 = sma(cs, 60)
    if s60 and price < s60 * 0.92:
        return None
    _per, roe = _ttm(q)
    debt, dy = q.get("debt_ratio"), q.get("yield_pct")
    defensive = ((roe is not None and roe >= 6) or (debt is not None and debt <= 120)
                 or (dy is not None and dy >= 2))
    if not defensive:
        return None
    score = 50 + min(30.0, (0.03 - vol) / 0.03 * 30)
    if dy is not None and dy >= 2:
        score += 10
    if debt is not None and debt <= 100:
        score += 8
    score = min(100.0, score)
    if score < 50:
        return None
    reasons = [f"저변동(일 {vol * 100:.1f}%)"]
    if dy is not None and dy >= 2:
        reasons.append(f"배당 {dy:.1f}%")
    if debt is not None and debt <= 100:
        reasons.append("저부채")
    return {"strategy": "S3", "label": STRATEGY_LABELS["S3"], "score": round(score, 1),
            "reasons": reasons, **_levels(q, price, cs, stop_pct=8.0, rr=1.5)}


# ── S4 단기 평균회귀 ───────────────────────────────────────────────────────
def s4_meanrev(q: dict, candles: list) -> dict | None:
    cs, _hs, _ls, vs = _series(candles)
    if len(cs) < 30:
        return None
    price = q.get("price") or cs[-1]
    r = rsi(cs, 14)
    bpos = bollinger_pos(cs, 20, 2.0)
    if r is None or r > 35:                           # 과매도만
        return None
    if bpos is None or bpos > 0.15:                   # 볼린저 하단 근처
        return None
    if len(vs) >= 6 and sum(vs[-6:-1]):               # 거래량 급증 동반
        vsurge = vs[-1] > (sum(vs[-6:-1]) / 5) * 1.3
    else:
        vsurge = False
    if not vsurge:
        return None
    s60 = sma(cs, 60) if len(cs) >= 60 else None
    if s60 and price < s60 * 0.85:                    # 폭락(떨어지는 칼) 제외
        return None
    score = min(100.0, 50 + (35 - r) + 10)
    reasons = [f"RSI {r:.0f} 과매도", "볼린저 하단·거래량 급증"]
    return {"strategy": "S4", "label": STRATEGY_LABELS["S4"], "score": round(score, 1),
            "reasons": reasons, **_levels(q, price, cs, stop_pct=4.0, rr=1.5)}


# ── S5 촉매·수급 이벤트 ────────────────────────────────────────────────────
def s5_catalyst(q: dict, candles: list) -> dict | None:
    cs, *_ = _series(candles)
    price = q.get("price") or (cs[-1] if cs else None)
    if not price:
        return None
    flash = q.get("flash_ni_yoy")
    if flash is None:
        flash = q.get("flash_op_yoy")
    chg = q.get("change_pct") or 0.0
    hi = q.get("high_52w")
    near_high = bool(hi and price >= hi * 0.92)
    if flash is None or flash < 20:                   # 실적 서프라이즈 촉매
        return None
    if not (near_high or chg >= 3):                   # 돌파/강세 동반
        return None
    score = 50 + min(25.0, max(0.0, flash) / 4) + (12 if near_high else 0) + min(8.0, max(0.0, chg))
    score = min(100.0, score)
    if score < 55:
        return None
    reasons = [f"실적 서프라이즈 {flash:+.0f}%"]
    if near_high:
        reasons.append("신고가 돌파")
    if chg > 0:
        reasons.append(f"당일 {chg:+.1f}%")
    return {"strategy": "S5", "label": STRATEGY_LABELS["S5"], "score": round(score, 1),
            "reasons": reasons, **_levels(q, price, cs)}


# ── S6 수급 모멘텀 (외국인·기관 매집) ───────────────────────────────────────
def s6_flow(q: dict, candles: list) -> dict | None:
    """한국 전용 선행지표: 외국인·기관 5일 순매집 + 추세 확인.

    q에 flow_net_eok(외인+기관 5일 순매수 억), flow_foreign_eok가 채워져 있어야 함
    (_swing_plan이 S6 활성 시 supply_demand로 주입). 하락추세 매집은 제외(추세 확인).
    """
    fnet = q.get("flow_net_eok")
    if fnet is None or fnet < 50:                     # 최소 매집 강도(합 50억+)
        return None
    cs, *_ = _series(candles)
    price = q.get("price") or (cs[-1] if cs else None)
    if not price or len(cs) < 60:
        return None
    s20, s60 = sma(cs, 20), sma(cs, 60)
    if not (s60 and price > s60):                     # 추세 확인(하락장 매집 제외)
        return None
    score = 50 + min(35.0, fnet / 20)                 # 700억+ → 만점권
    if s20 and price > s20:
        score += 8
    foreign = q.get("flow_foreign_eok")
    if foreign and foreign > 0:
        score += 7
    score = min(100.0, score)
    if score < 55:
        return None
    reasons = [f"외인+기관 5일 +{fnet:,.0f}억 매집"]
    if foreign and foreign > 0:
        reasons.append(f"외인 +{foreign:,.0f}억")
    if s20 and price > s20:
        reasons.append("정배열")
    return {"strategy": "S6", "label": STRATEGY_LABELS["S6"], "score": round(score, 1),
            "reasons": reasons, **_levels(q, price, cs)}


STRATEGY_FUNCS = {"S1": s1_momentum, "S2": s2_quality_value, "S3": s3_defensive,
                  "S4": s4_meanrev, "S5": s5_catalyst, "S6": s6_flow}

# 국면 미상/폴백: 강세·중립 성격의 두 축을 기본 활성(억지 매매 금지는 각 전략 임계로 보장).
DEFAULT_ACTIVE = ["S1", "S2"]


def run_strategies(active_ids: list[str], q: dict, candles: list) -> dict | None:
    """활성 전략들을 한 종목에 적용 → 최고 점수 픽 하나(없으면 None).

    active_ids는 시황 라우터(engine.regime)가 국면별로 넘긴 전략 ID 목록.
    한 전략이라도 임계를 넘기면 매수 픽이 되고, 여럿이면 최고 점수를 채택.
    """
    picks = []
    for sid in (active_ids or DEFAULT_ACTIVE):
        f = STRATEGY_FUNCS.get(sid)
        if not f:
            continue
        try:
            p = f(q, candles)
        except Exception:
            p = None
        if p:
            picks.append(p)
    return max(picks, key=lambda p: p["score"]) if picks else None
