"""전략 후보 5(engine.strategies) 순수 함수 테스트 — 각 전략의 적합/부적합·라우터."""
from __future__ import annotations

from engine.strategies import (
    DEFAULT_ACTIVE, STRATEGY_FUNCS, run_strategies,
    s1_momentum, s2_quality_value, s3_defensive, s4_meanrev, s5_catalyst, s6_flow,
)


def _candles(closes, vols=None, highs=None, lows=None):
    vols = vols or [1000] * len(closes)
    return [{"close": c, "high": (highs[i] if highs else c),
             "low": (lows[i] if lows else c), "volume": vols[i]}
            for i, c in enumerate(closes)]


def test_s1_momentum_uptrend_qualifies():
    closes = [100 + i for i in range(80)]                 # 꾸준한 상승 → 정배열·모멘텀+
    q = {"code": "005930", "price": closes[-1], "high_52w": closes[-1]}
    p = s1_momentum(q, _candles(closes))
    assert p and p["strategy"] == "S1" and p["score"] >= 55
    assert p["entry"] and p["stop"] and p["target"]
    # 하락추세는 부적합
    assert s1_momentum({"code": "005930", "price": 100},
                       _candles([180 - i for i in range(80)])) is None


def test_s2_quality_value_needs_cheap_and_quality():
    closes = [100 + (i % 3) for i in range(70)]
    good = {"code": "005930", "price": 100, "per_ttm": 8.0, "roe_ttm": 18.0,
            "debt_ratio": 80.0, "pbr": 0.9, "fcf": 500}
    p = s2_quality_value(good, _candles(closes))
    assert p and p["strategy"] == "S2" and p["score"] >= 55
    # 비싸면(PER 30) 부적합
    assert s2_quality_value({**good, "per_ttm": 30.0}, _candles(closes)) is None
    # 재무 결측이면 None(억지 매수 금지)
    assert s2_quality_value({"code": "005930", "price": 100}, _candles(closes)) is None


def test_s3_defensive_low_vol_only():
    flat = [100 + (0.1 if i % 2 else -0.1) for i in range(70)]   # 초저변동
    q = {"code": "005930", "price": flat[-1], "roe_ttm": 10.0,
         "debt_ratio": 60.0, "yield_pct": 3.0}
    p = s3_defensive(q, _candles(flat))
    assert p and p["strategy"] == "S3"
    # 고변동(±5% 진동)이면 방어주 아님
    vol = [100 * (1.05 if i % 2 else 0.95) for i in range(70)]
    assert s3_defensive(q, _candles(vol)) is None


def test_s4_meanrev_oversold_with_volume():
    closes = [100 - i * 0.8 for i in range(40)]           # 하락 → RSI 과매도·볼린저 하단
    vols = [1000] * 39 + [5000]                           # 마지막 거래량 급증
    q = {"code": "035420", "price": closes[-1]}
    p = s4_meanrev(q, _candles(closes, vols=vols))
    assert p and p["strategy"] == "S4"
    assert p["stop"] and p["target"]                      # 짧은 손절/목표
    # 거래량 급증 없으면 부적합
    assert s4_meanrev(q, _candles(closes, vols=[1000] * 40)) is None


def test_s5_catalyst_needs_surprise_and_breakout():
    closes = [100 + i for i in range(70)]
    q = {"code": "005930", "price": closes[-1], "high_52w": closes[-1],
         "flash_ni_yoy": 40.0, "change_pct": 4.0}
    p = s5_catalyst(q, _candles(closes))
    assert p and p["strategy"] == "S5" and p["score"] >= 55
    # 실적 촉매 없으면 부적합
    assert s5_catalyst({**q, "flash_ni_yoy": None, "flash_op_yoy": None},
                       _candles(closes)) is None


def test_s6_flow_needs_accumulation_and_trend():
    closes = [100 + i for i in range(70)]                 # 상승추세
    q = {"code": "005930", "price": closes[-1], "flow_net_eok": 400.0,
         "flow_foreign_eok": 300.0}
    p = s6_flow(q, _candles(closes))
    assert p and p["strategy"] == "S6" and p["score"] >= 55
    assert "매집" in p["reasons"][0]
    # 수급 데이터 없으면 None(주입 안 된 종목)
    assert s6_flow({"code": "005930", "price": 170}, _candles(closes)) is None
    # 순매집 약하면(50억 미만) None
    assert s6_flow({**q, "flow_net_eok": 10.0}, _candles(closes)) is None
    # 하락추세 매집은 제외(추세 확인)
    down = [170 - i for i in range(70)]
    assert s6_flow({**q, "price": down[-1]}, _candles(down)) is None


def test_run_strategies_picks_best_of_active():
    closes = [100 + i for i in range(80)]
    q = {"code": "005930", "price": closes[-1], "high_52w": closes[-1],
         "per_ttm": 8.0, "roe_ttm": 18.0, "debt_ratio": 80.0, "flash_ni_yoy": 40.0,
         "change_pct": 4.0}
    # 강세추세장 활성(S1+S5) → 둘 중 최고 점수 하나
    pick = run_strategies(["S1", "S5"], q, _candles(closes))
    assert pick and pick["strategy"] in ("S1", "S5")
    # 상승 추세주는 S4(과매도 반등) 조건에 안 맞음 → None
    assert run_strategies(["S4"], q, _candles(closes)) is None
    # active 비면 DEFAULT_ACTIVE 사용
    assert run_strategies([], q, _candles(closes)) is not None
    assert set(STRATEGY_FUNCS) == {"S1", "S2", "S3", "S4", "S5", "S6"}
    assert DEFAULT_ACTIVE == ["S1", "S2"]
