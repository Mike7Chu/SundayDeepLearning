"""백테스트 엔진 테스트 (순수 함수, 룩어헤드 없음, 비용 반영)."""
from __future__ import annotations

from backtest.engine import (
    _trend_pullback_trades,
    backtest,
    positions_momentum,
    positions_sma_cross,
    summarize,
)


def _candles(closes):
    """종가 리스트 → 캔들(고저는 종가 ±0.5% 근사)."""
    return [{"close": c, "high": c * 1.005, "low": c * 0.995,
             "volume": 1000, "time": f"2026-01-{i%28+1:02d}"}
            for i, c in enumerate(closes)]


def test_positions_no_lookahead_length():
    closes = [100 + i for i in range(80)]
    pos = positions_sma_cross(closes)
    assert len(pos) == len(closes)
    assert pos[:59] == [0] * 59           # 워밍업(<60봉)은 0
    assert 1 in pos[60:]                   # 상승추세면 이후 롱


def test_momentum_positions():
    closes = [100] * 60 + [120]
    pos = positions_momentum(closes, lookback=60)
    assert pos[-1] == 1 and pos[0] == 0


def test_summarize_costs_reduce_gross():
    # 왕복 +10% 한 건 → net은 비용만큼 gross보다 작아야(거래세·수수료·슬리피지)
    candles = _candles([100, 105, 110])
    out = summarize(candles, [(100.0, 110.0, 2)], kr=True)
    assert out["trades"] == 1
    assert out["gross_return_pct"] == 10.0
    assert out["net_return_pct"] < out["gross_return_pct"]   # 비용 반영
    assert out["cost_drag_pct"] > 0
    assert out["win_rate_pct"] == 100.0
    assert "verdict" in out


def test_summarize_no_trades_reports_buyhold():
    candles = _candles([100, 90, 80])
    out = summarize(candles, [], kr=True)
    assert out["trades"] == 0 and out["buy_hold_pct"] == -20.0


def test_backtest_dispatch_and_guards():
    up = _candles([100 + i for i in range(80)])
    d = backtest(up, "momentum", kr=True)
    assert d["strategy"] == "momentum" and "buy_hold_pct" in d
    # 우리 전략(추세 눌림목) — 구조 반환(거래 0 이상, 비용/판정 포함)
    s = backtest(up, "strategy", kr=True)
    assert s["label"].startswith("추세") and "verdict" in s
    # 데이터 부족·미지 전략
    assert "error" in backtest(_candles([100, 101]), "strategy")
    assert "error" in backtest(up, "nope")


def test_trend_pullback_produces_trades_on_pullback():
    # 상승추세 속 눌림 후 재돌파를 만들어 진입이 잡히는지(구조 검증)
    closes = [100 + i for i in range(70)]          # 꾸준한 상승 → SMA20>SMA60
    closes += [168, 160, 172, 178, 185]            # 눌림(160) 후 재돌파
    trades = _trend_pullback_trades(_candles(closes))
    assert isinstance(trades, list)
    for e, x, days in trades:
        assert e > 0 and x > 0 and days >= 0
