"""시황 국면 판정기(engine.regime) 순수 함수 테스트."""
from __future__ import annotations

from engine.regime import STRATEGY_LABELS, classify_regime


def _trend(n, start=100.0, step=0.4):
    """완만한 상승 추세 종가열."""
    return [start + step * i for i in range(n)]


def test_insufficient_data():
    r = classify_regime([100, 101, 102])
    assert r["regime"] == "unknown" and r["strategies"] == ["S2"]
    assert r["confidence"] == "low"


def test_bull_trend_uptrend():
    # 200일 이상 꾸준한 상승 → 지수>200일선·MA상승·저변동 → 강세 추세(S1+S5+S6)
    closes = _trend(230)
    r = classify_regime(closes, foreign_net_eok=500)
    assert r["regime"] == "bull_trend"
    assert r["strategies"] == ["S1", "S5", "S6"] and r["posture"] == "공격"
    assert r["metrics"]["ma_rising"] is True and r["metrics"]["dist_ma_pct"] > 0


def test_risk_off_downtrend_stress():
    # 하락 추세 + 마지막 급락(변동성 확대) → 지수<200일선 + 스트레스 → 위험 회피(S3)
    closes = _trend(220, start=200, step=-0.4)      # 하락
    closes = closes[:-5] + [closes[-6] * f for f in (0.97, 0.94, 0.90, 0.86, 0.82)]
    r = classify_regime(closes, foreign_net_eok=-2000)
    assert r["regime"] == "risk_off" and r["strategies"] == ["S3"]


def test_range_near_ma():
    # 200일선 바로 근처에서 저변동 횡보 → 횡보 레인지(S4)
    base = [100.0] * 210
    closes = base[:-20] + [100 + (0.3 if i % 2 else -0.3) for i in range(20)]
    r = classify_regime(closes)
    assert r["regime"] in ("range", "neutral")       # 근처·저변동 → 역발상/선별
    if r["regime"] == "range":
        assert r["strategies"] == ["S4"]


def test_exposure_by_regime():
    # 강세=100% 풀노출, 위험회피=20% 축소(비중 오버레이)
    assert classify_regime(_trend(230))["exposure_pct"] == 100          # bull_trend
    off = _trend(220, start=200, step=-0.4)
    off = off[:-5] + [off[-6] * f for f in (0.97, 0.94, 0.90, 0.86, 0.82)]
    assert classify_regime(off, foreign_net_eok=-2000)["exposure_pct"] == 20
    assert classify_regime([1, 2, 3])["exposure_pct"] == 50             # unknown 폴백


def test_strategy_labels_present():
    r = classify_regime(_trend(230))
    assert set(r["strategies"]).issubset(STRATEGY_LABELS)
    assert r["strategy_labels"] and all(isinstance(x, str) for x in r["strategy_labels"])
