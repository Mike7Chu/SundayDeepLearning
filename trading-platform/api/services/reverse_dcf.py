"""역DCF(Reverse DCF) — '지금 주가가 요구하는 성장률'을 역산(순수 함수).

타민더마켓 price-decoder 방법론 채택: 적정주가를 계산하지 않는다. 현재 시가총액이
함의하는 향후 10년 FCF 성장률 g를 이분법으로 역산하고, 과거 실제 성장률과 나란히 놓아
판단을 돕는다(예측 아님·판단의 시작). 모든 계산은 코드로(암산 금지). 단위는 market_cap·
fcf·net_debt가 같으면 무관(억원끼리 등).

핵심: 일반 DCF는 성장률을 내가 넣어야 하지만, 역DCF는 시장이 넣은 가정을 뽑아 '말이
되는지'만 본다. 국장·미장 공용.
"""
from __future__ import annotations

_TG = 0.025      # 영구성장률(장기 GDP 수준)
_YEARS = 10      # 1단계 명시적 성장 기간


def _dcf_ev(fcf: float, g: float, wacc: float,
            years: int = _YEARS, tg: float = _TG) -> float | None:
    """2단계 성장 DCF의 기업가치(EV). wacc≤tg면 발산 → None."""
    if wacc <= tg:
        return None
    pv = 0.0
    for t in range(1, years + 1):
        pv += fcf * (1 + g) ** t / (1 + wacc) ** t
    term_fcf = fcf * (1 + g) ** years * (1 + tg)
    pv += (term_fcf / (wacc - tg)) / (1 + wacc) ** years
    return pv


def implied_growth(market_cap: float, fcf: float, net_debt: float = 0.0,
                   wacc: float = 0.10, lo: float = -0.5, hi: float = 1.5,
                   iters: int = 100) -> float | None:
    """시총이 함의하는 10년 FCF 성장률 g(이분법). FCF≤0이면 None(역DCF 불가).

    목표 EV = market_cap + net_debt. DCF_EV(g)=목표 되는 g를 찾는다. DCF는 g에 단조증가
    이므로 목표가 [lo,hi] 밖이면 경계값으로 클램프(신호).
    """
    if not fcf or fcf <= 0 or not market_cap or market_cap <= 0 or wacc <= _TG:
        return None
    target = market_cap + (net_debt or 0.0)
    lo_v, hi_v = _dcf_ev(fcf, lo, wacc), _dcf_ev(fcf, hi, wacc)
    if lo_v is None or hi_v is None:
        return None
    if target <= lo_v:
        return round(lo, 4)
    if target >= hi_v:
        return round(hi, 4)
    for _ in range(iters):
        mid = (lo + hi) / 2
        v = _dcf_ev(fcf, mid, wacc)
        if v is None:
            return None
        if v < target:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2, 4)


def cagr(begin: float, end: float, years: float) -> float | None:
    """연평균 성장률. begin>0·end>0·years>0 아니면 None(음수는 CAGR 무의미)."""
    if not begin or begin <= 0 or end is None or end <= 0 or not years or years <= 0:
        return None
    return round((end / begin) ** (1 / years) - 1, 4)


def series_cagr(series: list | None) -> float | None:
    """시계열(오래된→최신)의 처음↔끝 CAGR. 2점 미만이거나 음수 끼면 None."""
    xs = [x for x in (series or []) if x is not None]
    if len(xs) < 2:
        return None
    return cagr(xs[0], xs[-1], len(xs) - 1)


def normalize_fcf(latest: float | None,
                  series: list | None) -> tuple[float | None, str | None]:
    """최근 FCF가 3년 평균 대비 ±40% 벗어나면 3년 평균 사용(왜곡 방지). (fcf, 주석)."""
    if latest is None:
        return None, None
    xs = [x for x in (series or []) if x is not None][-3:]
    if len(xs) < 3:
        return latest, None
    avg = sum(xs) / len(xs)
    if avg and abs(latest / avg - 1) > 0.40:
        return round(avg, 1), f"최근 FCF가 3년 평균 대비 ±40% 초과 → 3년 평균({avg:,.0f}) 사용"
    return latest, None


def _verdict(g: float, ref: float | None) -> str:
    gp = g * 100
    if ref is None:
        return (f"시장은 향후 10년 매년 {gp:.1f}% FCF 성장을 요구 — 과거 실제 성장률과 "
                "비교해 판단하세요(비교치 부족). 예측 아님·판단의 시작")
    rp = ref * 100
    gap = gp - rp
    tag = ("시장 기대가 과거보다 과함 — 비쌀 수 있음" if gap > 3
           else "시장 기대가 과거보다 낮음 — 쌀 수 있음" if gap < -3
           else "시장 기대가 과거 실적과 비슷")
    return (f"시장 요구 {gp:.1f}%/년 vs 과거 실제 {rp:.1f}%/년 → {tag}. "
            "예측 아님·판단의 시작이지 답이 아님")


def reverse_dcf(market_cap: float | None, fcf: float | None, *,
                net_debt: float = 0.0, wacc: float = 0.10,
                fcf_series: list | None = None,
                rev_series: list | None = None) -> dict:
    """역DCF 종합 → {ok, implied_growth, sensitivity, fcf_cagr, rev_cagr, verdict, ...}.

    단위 통일(억원 등). fcf_series/rev_series는 오래된→최신(과거 CAGR 비교용).
    """
    if not fcf or fcf <= 0:
        return {"ok": False,
                "reason": "FCF 음수/미상 — 역DCF 불가(흑자 전환 시점을 먼저 봐야 함)"}
    if not market_cap or market_cap <= 0:
        return {"ok": False, "reason": "시가총액 미상 — 주가·발행주식수 확인 필요"}
    g = implied_growth(market_cap, fcf, net_debt, wacc)
    if g is None:
        return {"ok": False, "reason": "역산 실패(할인율≤영구성장률 등 입력 확인)"}
    sens = []
    for w in (0.08, 0.10, 0.12):
        sens.append({"wacc": w, "growth": implied_growth(market_cap, fcf, net_debt, w)})
    fcf_cagr = series_cagr(fcf_series)
    rev_cagr = series_cagr(rev_series)
    ref = fcf_cagr if fcf_cagr is not None else rev_cagr
    return {"ok": True, "implied_growth": g, "wacc": wacc,
            "market_cap": round(market_cap, 1), "fcf": round(fcf, 1),
            "net_debt": round(net_debt or 0.0, 1),
            "sensitivity": sens, "fcf_cagr": fcf_cagr, "rev_cagr": rev_cagr,
            "reference_cagr": ref, "verdict": _verdict(g, ref),
            "terminal_growth": _TG, "years": _YEARS}
