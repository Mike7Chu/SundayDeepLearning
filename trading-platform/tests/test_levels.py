"""매매 가격 가이드(trade_levels)·KRX 호가 단위 테스트(순수 함수)."""
from __future__ import annotations

from api.services.stock_signal import krx_tick, trade_levels


def test_krx_tick():
    assert krx_tick(1_234) == 1_234          # <2천: 1원
    assert krx_tick(12_344) == 12_340        # <2만: 10원
    assert krx_tick(12_346) == 12_350
    assert krx_tick(123_456) == 123_500      # <20만: 100원
    assert krx_tick(314_567) == 314_500      # <50만: 500원
    assert krx_tick(2_424_300) == 2_424_000  # ≥50만: 1000원


def test_trade_levels_uptrend_pullback():
    # 상승 추세(가격 > SMA20): 추천 매수가 = SMA20 눌림목(추격 매수 방지)
    closes = [1000 + i * 10 for i in range(80)]   # 우상향
    lv = trade_levels(closes)
    assert lv is not None
    assert lv["entry_basis"] == "SMA20 눌림목"
    assert lv["entry"] < closes[-1]               # 현재가보다 낮은 진입가
    assert lv["stop"] < lv["entry"] < lv["target"]
    assert lv["rr"] == 2.0
    assert lv["trend_ok"] is True
    # 손절 클램프: 진입 대비 -3%~-15%
    assert -15.0 <= lv["stop_pct"] <= -3.0
    # 목표는 손익비 1:2 → 상승률 = 손절률의 2배
    assert abs(lv["target_pct"] - 2 * (-lv["stop_pct"])) < 1.5  # 호가 반올림 오차 허용


def test_trade_levels_downtrend_flag():
    closes = [2000 - i * 10 for i in range(80)]   # 우하향
    lv = trade_levels(closes)
    assert lv["trend_ok"] is False                # 매수 보류 신호
    assert lv["entry_basis"] == "현재가"


def test_trade_levels_insufficient_data():
    assert trade_levels([1000] * 10) is None
