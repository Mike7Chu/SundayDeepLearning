"""매매 비용모델 + 성적 집계(순수 함수) 테스트."""
from __future__ import annotations

from api.services.cost_model import cost_drag_pct, round_trip, side_cost
from api.services.stats import realized_trades, summarize


def test_side_cost_tax_on_sell_only():
    # 매도만 거래세 포함 → 매도 비용 > 매수 비용
    buy = side_cost(10000, 10, "BUY", kr=True)
    sell = side_cost(10000, 10, "SELL", kr=True)
    assert sell > buy > 0
    # 미국은 거래세 없음 → 매도 비용이 국내보다 작음(세율 차이)
    assert side_cost(100, 1, "SELL", kr=False) < side_cost(100, 1, "SELL", kr=True)


def test_round_trip_net_below_gross():
    rt = round_trip(entry=10000, exit_=11000, qty=10, kr=True)
    assert rt["gross"] == 10000.0            # (11000-10000)*10
    assert rt["cost"] > 0 and rt["net"] < rt["gross"]   # 비용만큼 net<gross
    assert rt["net_pct"] < rt["gross_pct"]
    # 무효 입력 방어
    assert round_trip(0, 100, 10)["net"] == 0.0


def test_cost_drag_positive():
    assert cost_drag_pct(kr=True) > cost_drag_pct(kr=False)  # 국내 거래세만큼 큼


def test_realized_fifo_matching():
    entries = [
        {"code": "005930", "side": "BUY", "price": 100, "qty": 10, "ts": 1},
        {"code": "005930", "side": "BUY", "price": 110, "qty": 10, "ts": 2},
        {"code": "005930", "side": "SELL", "price": 120, "qty": 15, "ts": 3},
    ]
    trips = realized_trades(entries)
    # 15주 매도 → 첫 로트 10주(@100) 전량 + 둘째 로트 5주(@110)
    assert len(trips) == 2
    assert trips[0]["entry"] == 100 and trips[0]["qty"] == 10
    assert trips[1]["entry"] == 110 and trips[1]["qty"] == 5


def test_summarize_gross_vs_net():
    entries = [
        {"code": "A0001", "side": "BUY", "price": 100, "qty": 100, "ts": 1},
        {"code": "A0001", "side": "SELL", "price": 105, "qty": 100, "ts": 2},  # 이익
        {"code": "B0002", "side": "BUY", "price": 100, "qty": 100, "ts": 3},
        {"code": "B0002", "side": "SELL", "price": 98, "qty": 100, "ts": 4},   # 손실
    ]
    s = summarize(entries)
    assert s["n"] == 2
    assert 0 <= s["win_rate"] <= 100
    assert s["net"] < s["gross"]             # 비용 차감 → net < gross
    assert s["cost"] > 0
    # 왕복 없으면 안전 기본값
    empty = summarize([{"code": "A0001", "side": "BUY", "price": 100, "qty": 1, "ts": 1}])
    assert empty["n"] == 0 and empty["open"] == 1
