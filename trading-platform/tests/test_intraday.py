"""데이 트레이딩 로직(분봉 집계·장중 신호·시간 판정) 순수 함수 테스트."""
from __future__ import annotations

from datetime import datetime

from engine.intraday import add_tick, ema, intraday_signal, krx_intraday


def test_add_tick_buckets():
    bars = []
    bars = add_tick(bars, 100, ts=0, bucket_sec=60)
    bars = add_tick(bars, 102, ts=30, bucket_sec=60)     # 같은 버킷 → 갱신
    assert len(bars) == 1 and bars[-1]["h"] == 102 and bars[-1]["c"] == 102 and bars[-1]["v"] == 2
    bars = add_tick(bars, 101, ts=61, bucket_sec=60)     # 새 버킷
    assert len(bars) == 2 and bars[-1]["o"] == 101


def test_ema():
    assert ema([1, 2, 3], 5) is None
    assert ema([5, 5, 5, 5, 5], 5) == 5.0


def _bars(closes, vols=None):
    vols = vols or [1] * len(closes)
    return [{"t": i * 60, "o": c, "h": c, "l": c, "c": c, "v": v}
            for i, (c, v) in enumerate(zip(closes, vols))]


def test_intraday_signal_buy_and_none():
    # 우상향 + 마지막 양봉 + 거래강도 급증 → buy
    up = list(range(100, 125))                        # 25봉 상승
    bars = _bars(up, vols=[1] * 24 + [9])
    bars[-1] = {**bars[-1], "o": up[-1] - 1, "c": up[-1]}   # 양봉
    sig = intraday_signal(bars)
    assert sig["action"] == "buy"
    # 하락 추세 → none
    down = list(range(125, 100, -1))
    assert intraday_signal(_bars(down))["action"] == "none"
    # 데이터 부족
    assert intraday_signal(_bars([1, 2, 3]))["action"] == "none"


def test_krx_intraday_state():
    assert krx_intraday(datetime(2026, 7, 24, 10, 0)) == "entry"      # 금 10시
    assert krx_intraday(datetime(2026, 7, 24, 15, 20)) == "flatten"  # 청산구간
    assert krx_intraday(datetime(2026, 7, 24, 8, 0)) == "closed"     # 장전
    assert krx_intraday(datetime(2026, 7, 25, 10, 0)) == "closed"    # 토요일
