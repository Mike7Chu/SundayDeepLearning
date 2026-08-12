"""시황 국면 판정기(순수 함수) — 전략 라우터의 두뇌.

'항상 매매'가 아니라 '맞는 국면에 맞는 전략, 아니면 현금'을 만들기 위해, 매일 시장을
4국면으로 분류한다(전략 리포트 §04):

  bull_trend  강세 추세   지수>200일선·MA상승·저변동   → S1 모멘텀 + S5 촉매·수급
  neutral     중립·순환   방향성 애매·변동성 보통        → S2 퀄리티-밸류(저회전)
  range       횡보 레인지  지수 200일선 근처·저변동       → S4 단기 평균회귀
  risk_off    위험 회피   지수<200일선 + 스트레스        → S3 저변동 방어 · 또는 현금

판정 신호: ①지수 vs 200일선(추세 방향) ②200일선 기울기(상승/하락) ③변동성(최근 vs 기준)
④외국인 수급(억) ⑤시장 폭 breadth(200일선 위 종목 비중 %, 선택). 순수 함수라 네트워크
없이 테스트 가능 — 입력만 어댑터가 Redis에서 모아 넣는다. 예측 아님·판단 보조(면책).
"""
from __future__ import annotations

# 전략 ID → 사람이 읽는 이름(대시보드·브리핑·로그 공용, engine.strategies와 동기)
STRATEGY_LABELS = {
    "S1": "추세 모멘텀", "S2": "퀄리티-밸류", "S3": "저변동 방어",
    "S4": "단기 평균회귀", "S5": "실적 서프라이즈(PEAD)", "S6": "수급 모멘텀",
    "S7": "RSI2 평균회귀",
}


def _sma(xs: list[float], n: int) -> float | None:
    if not xs or n <= 0 or len(xs) < n:
        return None
    return sum(xs[-n:]) / n


def _stdev(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = sum(xs) / len(xs)
    return (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5


def classify_regime(index_closes: list[float], *,
                    foreign_net_eok: float | None = None,
                    breadth_pct: float | None = None,
                    ma_long: int = 200, vol_window: int = 20) -> dict:
    """지수 일봉 종가열(+선택 수급·breadth) → 국면 dict.

    반환 {regime, label, posture, strategies:[ID], confidence, reasons:[..], metrics}.
    데이터 부족(40봉 미만)이면 'unknown'(중립 전략으로 안전 폴백).
    """
    closes = [c for c in (index_closes or []) if c]
    n = len(closes)
    if n < 40:
        return {"regime": "unknown", "label": "판정 불가", "posture": "중립",
                "strategies": ["S2"], "confidence": "low", "exposure_pct": 50,
                "strategy_labels": [STRATEGY_LABELS["S2"]],
                "reasons": ["지수 일봉 부족 — 데이터 축적 후 판정"], "metrics": {}}

    p = min(ma_long, n)                         # 200일 우선, 부족하면 가능한 최장
    ma = _sma(closes, p) or closes[-1]
    ma_prev = _sma(closes[:-vol_window], p) if n > p + vol_window else ma
    price = closes[-1]
    dist = price / ma - 1 if ma else 0.0
    rising = ma_prev is not None and ma > ma_prev

    rets = [closes[i] / closes[i - 1] - 1
            for i in range(1, n) if closes[i - 1]]
    vol = _stdev(rets[-vol_window:])
    base = _stdev(rets[-4 * vol_window:]) if len(rets) >= 4 * vol_window else vol
    vol_hi = base > 0 and vol > base * 1.3
    vol_lo = base > 0 and vol < base * 0.85

    foreign_neg = foreign_net_eok is not None and foreign_net_eok <= -1000
    foreign_pos = foreign_net_eok is not None and foreign_net_eok > 0
    breadth_weak = breadth_pct is not None and breadth_pct < 40
    breadth_ok = breadth_pct is not None and breadth_pct >= 60

    above = dist >= 0
    uptrend = above and rising                    # 추세 방향(200일선 위 + 장기선 상승)
    stress = breadth_weak or foreign_neg          # 구조적 스트레스(변동성은 노출도로 분리)

    reasons = [f"지수 200일선 {'위 +' if above else '아래 '}{dist * 100:.1f}%",
               "장기선 상승" if rising else "장기선 하락"]
    if vol_hi:
        reasons.append("변동성 확대")
    elif vol_lo:
        reasons.append("변동성 축소")
    if foreign_net_eok is not None:
        reasons.append(f"외인수급 {foreign_net_eok:+,.0f}억")
    if breadth_pct is not None:
        reasons.append(f"breadth {breadth_pct:.0f}%")

    # 추세 방향이 전략 '선택'을, 변동성이 '비중'을 정한다(분리). 상승추세면 변동성이
    # 커도 모멘텀을 끄지 않고 비중만 줄인다 — 명백한 상승장을 '중립'으로 깔던 문제 해소.
    # 순서 = 자본보존 우선(위험회피) → 강세추세 → 횡보 → 나머지 중립.
    if not above and (stress or vol_hi):
        regime, label, posture, strat = "risk_off", "위험 회피", "방어", ["S3"]
        exposure = 20
    elif uptrend and not breadth_weak:
        # 강세추세: 모멘텀(S1/S5/S6) + RSI2 눌림 매수(S7) — 상승장 눌림목 고승률·빠른 회전.
        regime, label, posture, strat = "bull_trend", "강세 추세", "공격", ["S1", "S5", "S6", "S7"]
        exposure = 60 if vol_hi else 100          # 추세는 타되 변동성 크면 비중 축소
    elif abs(dist) < 0.03 and not vol_hi:
        # 횡보: 단기 평균회귀(S4) + RSI2 극단 과매도 반등(S7).
        regime, label, posture, strat = "range", "횡보 레인지", "역발상", ["S4", "S7"]
        exposure = 40
    else:
        regime, label, posture, strat = "neutral", "중립·순환", "선별", ["S2", "S6"]
        exposure = 60 if not vol_hi else 40
    if foreign_pos and regime == "neutral" and above:
        reasons.append("외인 순매수 — 위험선호 편")
    if regime == "bull_trend" and vol_hi:
        reasons.append("상승추세·고변동 — 모멘텀 유지·비중 축소")

    return {"regime": regime, "label": label, "posture": posture,
            "strategies": strat, "confidence": "high" if n >= ma_long else "mid",
            "strategy_labels": [STRATEGY_LABELS.get(s, s) for s in strat],
            "exposure_pct": exposure, "reasons": reasons,
            "metrics": {"dist_ma_pct": round(dist * 100, 2), "ma_rising": rising,
                        "vol": round(vol, 4), "vol_base": round(base, 4),
                        "vol_high": vol_hi, "foreign_eok": foreign_net_eok,
                        "breadth_pct": breadth_pct}}
