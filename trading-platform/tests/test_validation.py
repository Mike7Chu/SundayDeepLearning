"""검증 계층(Validation First) — 캘리브레이션·IC·중복 반영·confidence 테스트(순수)."""
from __future__ import annotations

from api.services.stock_score import compute_score
from api.services.validation import (
    axis_correlation,
    axis_ic,
    calibration_buckets,
    forward_pairs,
    pearson,
    spearman,
)


def test_pearson_spearman_basics():
    xs = [1.0, 2, 3, 4, 5, 6, 7, 8]
    assert pearson(xs, xs) == 1.0
    assert pearson(xs, [-x for x in xs]) == -1.0
    # 단조 비선형(제곱)도 스피어만은 1.0 — 순위 기반
    assert spearman(xs, [x * x for x in xs]) == 1.0
    # 표본 8개 미만·상수열은 None(통계 무의미)
    assert pearson([1, 2, 3], [1, 2, 3]) is None
    assert pearson(xs, [5.0] * 8) is None


def test_calibration_buckets_order_and_stats():
    # 점수 높을수록 수익률이 좋은 인위 데이터 → 계단식 확인
    pairs = [(30, -5.0), (40, -3.0), (50, 1.0), (55, -1.0),
             (65, 3.0), (68, 5.0), (75, 8.0), (95, 12.0)]
    out = calibration_buckets(pairs)
    assert [r["bucket"] for r in out] == ["0~45", "45~60", "60~70", "70~80", "90+"]
    b0 = out[0]
    assert b0["n"] == 2 and b0["avg_ret"] == -4.0 and b0["win_rate"] == 0.0
    b60 = next(r for r in out if r["bucket"] == "60~70")
    assert b60["win_rate"] == 100.0


def test_forward_pairs_and_axis_ic():
    # 스냅샷 10종목: growth 축만 미래 수익률과 완전 단조 — IC 1.0이어야 함
    snapshot = {}
    prices = {}
    for i in range(10):
        code = f"C{i}"
        snapshot[code] = {"s": 50 + i, "p": 100.0,
                          "v": 10.0, "q": 10.0, "g": float(i),
                          "m": float((i * 7) % 10), "t": 5.0}
        prices[code] = 100.0 + i          # 수익률 = i%
    pairs, rows, rets = forward_pairs(snapshot, prices)
    assert len(pairs) == 10
    assert pairs[0][1] == round((prices["C0"] / 100 - 1) * 100, 2)
    ic = axis_ic(rows, rets)
    assert ic["growth"] == 1.0
    assert ic["value"] is None            # 상수열 → 통계 무의미
    # 현재가 없는 종목은 제외
    snapshot["X"] = {"s": 70, "p": 100.0}
    pairs2, _, _ = forward_pairs(snapshot, prices)
    assert len(pairs2) == 10


def test_axis_correlation_double_counting():
    # momentum과 timing이 완전 동행(중복 반영) → r=1.0
    rows = [{"value": float(i % 4), "quality": float((i * 3) % 7),
             "growth": float(i), "momentum": float(i), "timing": float(i)}
            for i in range(12)]
    corr = axis_correlation(rows)
    assert corr["momentum×timing"] == 1.0
    assert corr["growth×momentum"] == 1.0
    assert -1.0 <= (corr["value×quality"] or 0) <= 1.0


def test_compute_score_confidence():
    full = {"code": "A", "name": "A", "price": 10000, "eps": 1000, "bps": 8000,
            "per": 10, "pbr": 1.2, "ni_growth_q_pct": 30.0}
    closes = [8000 + i * 30 for i in range(70)]
    sc = compute_score(full, closes)
    assert sc["confidence"] == 100
    # 재무·성장 없음 + 차트 없음 → 신뢰도 0 (점수와 별개 축)
    bare = compute_score({"code": "B", "name": "B", "price": 500.0}, [])
    assert bare["confidence"] == 0
    # 차트만 있음(미국 종목 흔한 케이스) → 40 (closes 60↑·20↑ 두 체크만)
    chart_only = compute_score({"code": "NVDA", "name": "n", "price": 180.0},
                               closes)
    assert chart_only["confidence"] == 40
