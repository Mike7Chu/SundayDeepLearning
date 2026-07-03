"""토스증권 클라이언트 순수 파서 테스트 (네트워크 無).

응답 봉투 {result}/{error} 처리 + 보유 평가액·수익률 산출을 검증.
필드명은 토스 응답 편차를 대비해 다후보를 수용(_first) — 대표 케이스만 고정 검증.
"""
from __future__ import annotations

import pytest

from collector.stock.toss import (
    TossError,
    _json_or_raise,
    _unwrap,
    parse_accounts,
    parse_buying_power,
    parse_candles,
    parse_holdings,
    parse_order,
    parse_prices,
)


class _Resp:
    """httpx.Response 흉내 (json()만 필요)."""

    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p

    def raise_for_status(self):
        pass


def test_unwrap_strips_result_envelope():
    assert _unwrap({"result": {"a": 1}}) == {"a": 1}
    assert _unwrap({"a": 1}) == {"a": 1}          # result 없으면 그대로
    assert _unwrap([1, 2]) == [1, 2]


def test_json_or_raise_on_error_envelope():
    resp = _Resp({"error": {"code": "E1", "message": "bad", "requestId": "r9"}})
    with pytest.raises(TossError) as ei:
        _json_or_raise(resp)
    assert ei.value.code == "E1"
    assert ei.value.request_id == "r9"


def test_json_or_raise_passthrough_success():
    assert _json_or_raise(_Resp({"result": {"ok": True}})) == {"result": {"ok": True}}


def test_parse_accounts_picks_seq_variants():
    res = {"accounts": [{"accountSeq": 12, "accountName": "주식"},
                        {"accountNumber": "34", "name": "연금"}]}
    accs = parse_accounts(res)
    assert [a["accountSeq"] for a in accs] == ["12", "34"]
    assert accs[0]["name"] == "주식"


def test_parse_holdings_computes_eval_and_pnl():
    res = {"holdings": [
        {"symbol": "005930", "name": "삼성전자", "quantity": 10,
         "averagePrice": 60000, "currentPrice": 66000},   # eval 660k, pnl +60k, +10%
    ], "cash": 40000}
    out = parse_holdings(res)
    h = out["holdings"][0]
    assert h["eval_amount"] == 660000
    assert h["pnl"] == 60000
    assert h["pnl_pct"] == 10.0
    assert out["total_eval"] == 660000
    assert out["pnl"] == 60000
    assert out["pnl_pct"] == 10.0
    assert out["cash"] == 40000


def test_parse_holdings_prefers_given_values():
    res = {"holdings": [
        {"symbol": "AAPL", "qty": 2, "avgPrice": 100, "lastPrice": 120,
         "evaluationAmount": 999, "profitLoss": 42, "profitLossRate": 3.5},
    ]}
    h = parse_holdings(res)["holdings"][0]
    assert h["eval_amount"] == 999      # 응답값 우선(산출 안 함)
    assert h["pnl"] == 42
    assert h["pnl_pct"] == 3.5


def test_parse_holdings_empty():
    out = parse_holdings({"holdings": []})
    assert out["holdings"] == []
    assert out["total_eval"] == 0
    assert out["pnl_pct"] == 0.0


def test_parse_buying_power_variants():
    assert parse_buying_power({"buyingPower": 1000})["buying_power"] == 1000
    assert parse_buying_power({"orderableAmount": 500})["buying_power"] == 500
    assert parse_buying_power("x")["buying_power"] is None


def test_parse_prices():
    res = {"prices": [{"symbol": "005930", "lastPrice": 66000, "changeRate": 1.2}]}
    rows = parse_prices(res)
    assert rows == [{"symbol": "005930", "price": 66000.0, "change_pct": 1.2}]


def test_parse_candles_sorted_and_skips_empty():
    res = {"candles": [
        {"date": "20240102", "close": 110, "open": 100, "high": 115, "low": 99, "volume": 5},
        {"date": "20240101", "close": 100},
        {"date": "20240103", "closePrice": None},   # 종가 없음 → 제외
    ]}
    rows = parse_candles(res)
    assert [r["date"] for r in rows] == ["20240101", "20240102"]
    assert rows[1]["high"] == 115


def test_parse_order_normalizes():
    res = {"orderId": "A1", "symbol": "005930", "side": "buy",
           "quantity": 3, "price": 66000, "status": "open", "orderType": "limit"}
    o = parse_order(res)
    assert o["order_id"] == "A1"
    assert o["side"] == "BUY"
    assert o["status"] == "OPEN"
    assert o["order_type"] == "LIMIT"
