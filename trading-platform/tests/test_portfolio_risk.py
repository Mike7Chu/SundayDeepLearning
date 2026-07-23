"""포트폴리오 리스크 — 섹터·집중도·상관·종합 등급 테스트(순수 함수)."""
from __future__ import annotations

from api.services.portfolio_risk import assess_risk, correlations, sector_of


def test_sector_of():
    assert sector_of("NVDA") == "반도체"
    assert sector_of("005930") == "반도체"       # 삼성전자
    assert sector_of("JPM") == "금융"
    assert sector_of("999999", "대한바이오제약") == "바이오"   # 키워드 추정
    assert sector_of("000000", "무명종목") == "기타"


def _prices(rets, base=100.0):
    """수익률 시퀀스 → 가격 시퀀스."""
    px = [base]
    for r in rets:
        px.append(px[-1] * (1 + r))
    return px


def test_correlations_detects_comovement():
    # 변동성 있는 수익률 패턴(단조 아님) — 상관이 진짜 방향을 반영하도록
    rets = [0.02, -0.03, 0.01, 0.04, -0.02, -0.01, 0.03, -0.04, 0.02, 0.01,
            -0.02, 0.03, -0.01, 0.02, -0.03, 0.01, 0.04, -0.02, -0.01, 0.03,
            0.02, -0.03, 0.01, 0.04, -0.02]
    a = _prices(rets)
    b = _prices([r for r in rets])                    # 완전 동행 → r≈1
    c = _prices([-r for r in rets])                   # 완전 반대 → r≈-1
    out = correlations({"A": a, "B": b, "C": c},
                       {"A": "에이", "B": "비", "C": "씨"}, threshold=0.7)
    ab = next(x for x in out if {x["a"], x["b"]} == {"A", "B"})
    assert ab["r"] >= 0.9 and ab["high"]
    ac = next(x for x in out if {x["a"], x["b"]} == {"A", "C"})
    assert ac["r"] <= -0.9 and not ac["high"]
    # 봉 20개 미만이면 상관 계산 안 함
    assert correlations({"X": [1, 2, 3], "Y": [3, 2, 1]}, {}) == []


def test_assess_risk_concentration_and_level():
    # 한 종목이 70% → 단일 쏠림 플래그 + 높음 등급
    holdings = [
        {"symbol": "NVDA", "name": "엔비디아", "eval_amount": 7000, "currency": "USD"},
        {"symbol": "AMD", "name": "AMD", "eval_amount": 2000, "currency": "USD"},
        {"symbol": "AVGO", "name": "브로드컴", "eval_amount": 1000, "currency": "USD"},
    ]
    r = assess_risk(holdings, {}, fx=1)               # fx=1로 단순화
    assert r["total"] == 10000
    assert r["max_single"]["code"] == "NVDA" and r["max_single"]["weight"] == 70.0
    # 셋 다 반도체 → 섹터 100% 쏠림
    assert r["top_sector"]["sector"] == "반도체" and r["top_sector"]["weight"] == 100.0
    assert any("단일 종목 쏠림" in f for f in r["flags"])
    assert any("섹터 쏠림" in f for f in r["flags"])
    assert r["level"] == "높음" and r["hhi"] > 0.5


def test_assess_risk_diversified_low():
    # 다른 섹터 5종목 균등 → 분산 양호(낮음)
    holdings = [
        {"symbol": "NVDA", "name": "엔비디아", "eval_amount": 20, "currency": "USD"},
        {"symbol": "JPM", "name": "JP모건", "eval_amount": 20, "currency": "USD"},
        {"symbol": "LLY", "name": "일라이릴리", "eval_amount": 20, "currency": "USD"},
        {"symbol": "KO", "name": "코카콜라", "eval_amount": 20, "currency": "USD"},
        {"symbol": "XOM", "name": "엑슨", "eval_amount": 20, "currency": "USD"},
    ]
    r = assess_risk(holdings, {}, fx=1)
    assert r["level"] == "낮음" and not r["flags"]
    assert r["max_single"]["weight"] == 20.0
    # 보유 없으면 안전 기본값
    empty = assess_risk([], {}, fx=1)
    assert empty["total"] == 0 and empty["level"] is None
