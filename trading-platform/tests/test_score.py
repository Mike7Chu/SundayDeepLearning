"""투자 매력도 스코어 순수 함수 테스트."""
from __future__ import annotations

import math

from api.services.stock_score import (
    compute_score,
    graham_number,
    margin_of_safety,
)


def test_graham_number():
    # √(22.5 · 5000 · 50000) = √5,625,000,000 ≈ 75000
    assert graham_number(5000, 50000) == round(math.sqrt(22.5 * 5000 * 50000), 1)
    assert graham_number(-100, 50000) is None    # 적자
    assert graham_number(5000, None) is None


def test_margin_of_safety():
    g = graham_number(5000, 50000)               # ~75000
    # 현재가 60000 → (75000-60000)/75000 ≈ 20% 저평가
    mos = margin_of_safety(60000, 5000, 50000)
    assert mos is not None and 18 < mos < 22
    # 고평가(현재가 > 그레이엄)면 음수
    assert margin_of_safety(90000, 5000, 50000) < 0


def test_compute_score_quality_value_stock():
    # 저PER·저PBR·흑자·고ROE·저평가 → 높은 가치+품질 점수
    q = {"code": "005930", "name": "삼성전자", "price": 60000,
         "per": 8, "pbr": 0.9, "eps": 7500, "bps": 66000,
         "high_52w": 90000, "low_52w": 55000}
    rising = [100 + i for i in range(130)]       # 상승추세 종가
    out = compute_score(q, rising)
    assert 0 <= out["score"] <= 100
    assert out["value"] > 20            # 가치 축 우수
    assert out["quality"] > 15          # 품질 축 우수
    assert out["momentum"] > 0          # 추세 반영
    assert out["verdict"] in ("적극 매수 검토", "분할매수 구간", "관찰", "관망")
    assert out["margin_pct"] is not None


def test_compute_score_no_chart_partial():
    q = {"code": "000660", "name": "x", "price": 1000, "per": 30, "pbr": 5,
         "eps": 33, "bps": 200}
    out = compute_score(q, [])          # 일봉 없음
    assert out["has_chart"] is False
    assert out["momentum"] == 0.0       # 추세 미반영
    assert out["timing"] == 5.0         # 중립


def test_compute_score_loss_making_low():
    q = {"code": "111111", "name": "적자", "price": 5000, "per": -5, "pbr": 3,
         "eps": -400, "bps": 1000}
    out = compute_score(q, [])
    assert out["value"] < 10            # 적자·고PBR → 낮은 가치
    assert out["margin_pct"] is None    # 그레이엄 계산 불가
