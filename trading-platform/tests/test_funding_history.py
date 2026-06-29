"""펀비 히스토리 병합(시각×거래소) 테스트 — 순수 함수."""
from __future__ import annotations

from api.services.funding_history import merge_history


def test_merge_history_aligns_by_time_desc():
    per_ex = {
        "binance": {1000: 0.01, 2000: 0.02, 3000: 0.03},
        "bybit":   {2000: -0.01, 3000: 0.04},   # 1000엔 데이터 없음
    }
    rows = merge_history(per_ex, limit=10)
    # 시각 내림차순
    assert [r["ts"] for r in rows] == [3000, 2000, 1000]
    # 정렬·병합: 3000은 둘 다, 1000은 binance만
    assert rows[0]["by_ex"] == {"binance": 0.03, "bybit": 0.04}
    assert rows[2]["by_ex"] == {"binance": 0.01}


def test_merge_history_limit_and_empty():
    per_ex = {"binance": {i: 0.01 for i in range(100)}}
    assert len(merge_history(per_ex, limit=5)) == 5
    assert merge_history({}, limit=10) == []
