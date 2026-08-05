"""역DCF(api.services.reverse_dcf) 순수 함수 테스트 — 역산 라운드트립·경계·CAGR."""
from __future__ import annotations

from api.services.reverse_dcf import (
    _dcf_ev, cagr, implied_growth, normalize_fcf, reverse_dcf, series_cagr,
)


def test_implied_growth_roundtrip():
    # g를 넣어 EV를 만들고, 그 EV(=시총, 순부채 0)로 역산하면 같은 g가 나와야 한다.
    fcf, wacc, g = 100.0, 0.10, 0.05
    ev = _dcf_ev(fcf, g, wacc)
    back = implied_growth(ev, fcf, net_debt=0.0, wacc=wacc)
    assert abs(back - g) < 1e-3
    # 순부채가 있으면 목표 EV=시총+순부채 → 같은 g 복원
    mc = ev - 30.0                                    # 시총 = EV - 순부채
    assert abs(implied_growth(mc, fcf, net_debt=30.0, wacc=wacc) - g) < 1e-3


def test_implied_growth_monotonic_and_guards():
    # 시총이 클수록 요구 성장률도 커진다(단조).
    lo = implied_growth(1000, 100, 0, 0.10)
    hi = implied_growth(2000, 100, 0, 0.10)
    assert hi > lo
    # FCF≤0 → None(역DCF 불가), 시총 미상 → None
    assert implied_growth(1000, 0, 0, 0.10) is None
    assert implied_growth(0, 100, 0, 0.10) is None
    # 할인율 ≤ 영구성장률 → None
    assert implied_growth(1000, 100, 0, 0.02) is None


def test_cagr():
    assert abs(cagr(100, 200, 5) - (2 ** (1 / 5) - 1)) < 1e-3   # 4자리 반올림
    assert cagr(0, 200, 5) is None and cagr(100, -5, 5) is None
    assert abs(series_cagr([100, 150, 220]) - cagr(100, 220, 2)) < 1e-9
    assert series_cagr([100]) is None


def test_normalize_fcf():
    # 최근값이 3년 평균 대비 ±40% 초과 → 평균 사용 + 주석
    fcf, note = normalize_fcf(300, [90, 100, 110])   # 평균 100, 300은 +200%
    assert fcf == 100.0 and note is not None
    # 정상 범위면 원값 유지
    fcf2, note2 = normalize_fcf(110, [90, 100, 110])
    assert fcf2 == 110 and note2 is None


def test_reverse_dcf_verdict():
    # 시총이 요구하는 성장률 vs 과거 실제 → 판정
    fcf, wacc, g = 100.0, 0.10, 0.08
    ev = _dcf_ev(fcf, g, wacc)
    # 과거 FCF CAGR을 낮게(3%) 주면 '시장 기대 과함(비쌈)'
    out = reverse_dcf(ev, fcf, wacc=wacc, fcf_series=[100, 103, 106])  # ~3%
    assert out["ok"] and abs(out["implied_growth"] - g) < 1e-2
    assert "비쌀" in out["verdict"] and len(out["sensitivity"]) == 3
    assert out["sensitivity"][1]["wacc"] == 0.10
    # FCF 음수 → 역DCF 불가
    assert reverse_dcf(1000, -50)["ok"] is False
