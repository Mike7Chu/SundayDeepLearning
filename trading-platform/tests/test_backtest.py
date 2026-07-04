"""백테스트 엔진 테스트 (순수 함수, 룩어헤드 없음)."""
from __future__ import annotations

from backtest.engine import (
    backtest,
    positions_momentum,
    positions_sma_cross,
    run_backtest,
)


def test_positions_no_lookahead_length():
    closes = [100 + i for i in range(80)]
    pos = positions_sma_cross(closes)
    assert len(pos) == len(closes)
    # 워밍업(<60봉) 구간은 0(슬로우 SMA 불가)
    assert pos[:59] == [0] * 59
    # 상승추세면 이후 롱(1) 등장
    assert 1 in pos[60:]


def test_run_backtest_full_long_equals_buyhold():
    closes = [100, 110, 121]      # +10%, +10%
    pos = [1, 1, 0]              # 끝까지 보유(마지막 포지션은 미사용)
    r = run_backtest(closes, pos)
    assert r["total_return_pct"] == 21.0      # 1.1*1.1-1
    assert r["buy_hold_pct"] == 21.0
    assert r["trades"] == 1 and r["win_rate_pct"] == 100.0


def test_run_backtest_flat_no_trades():
    closes = [100, 90, 80]
    r = run_backtest([*closes], [0, 0, 0])
    assert r["total_return_pct"] == 0.0       # 현금 보유 → 손실 회피
    assert r["trades"] == 0 and r["win_rate_pct"] is None
    assert r["buy_hold_pct"] == -20.0


def test_backtest_dispatch_and_unknown():
    closes = [100 + i for i in range(80)]
    d = backtest(closes, "momentum")
    assert d["strategy"] == "momentum" and "total_return_pct" in d
    assert "error" in backtest(closes, "nope")


def test_momentum_positions():
    closes = [100] * 60 + [120]          # 61봉째 모멘텀 양
    pos = positions_momentum(closes, lookback=60)
    assert pos[-1] == 1 and pos[0] == 0
