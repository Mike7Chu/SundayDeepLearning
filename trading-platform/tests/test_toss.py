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
    candle_metrics,
    parse_accounts,
    parse_buying_power,
    parse_candles,
    parse_holdings,
    parse_order,
    parse_prices,
    parse_stocks,
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


def test_parse_holdings_real_schema():
    # 실제 토스 HoldingsOverview 구조(중첩 marketValue/profitLoss + 요약 krw/usd).
    res = {
        "marketValue": {"amount": {"krw": "7200000", "usd": "1785"}},
        "profitLoss": {"amount": {"krw": "700000"}, "rate": "0.1179"},
        "items": [
            {"symbol": "005930", "name": "삼성전자", "currency": "KRW",
             "quantity": "100", "lastPrice": "72000", "averagePurchasePrice": "65000",
             "marketValue": {"amount": "7200000"},
             "profitLoss": {"amount": "700000", "rate": "0.1077"}},
        ],
    }
    out = parse_holdings(res)
    h = out["holdings"][0]
    assert h["qty"] == 100 and h["avg_price"] == 65000 and h["cur_price"] == 72000
    assert h["eval_amount"] == 7200000     # marketValue.amount(중첩)
    assert h["pnl"] == 700000              # profitLoss.amount(중첩)
    assert h["pnl_pct"] == 10.77           # profitLoss.rate(소수) × 100
    assert h["currency"] == "KRW"
    assert out["total_eval_krw"] == 7200000
    assert out["total_eval_usd"] == 1785
    assert out["pnl"] == 700000
    assert out["pnl_pct"] == 11.79         # 전체 원화환산 rate × 100


def test_parse_holdings_empty():
    out = parse_holdings({"items": [], "marketValue": {"amount": {"krw": "0"}}})
    assert out["holdings"] == []
    assert out["total_eval_krw"] == 0.0
    assert out["total_eval_usd"] is None


def test_parse_buying_power_real_field():
    # 실제 필드는 cashBuyingPower.
    assert parse_buying_power({"currency": "KRW", "cashBuyingPower": "5000000"})["buying_power"] == 5000000
    assert parse_buying_power({"cashBuyingPower": "3500.5"})["buying_power"] == 3500.5
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


def test_parse_stocks():
    res = [
        {"symbol": "005930", "name": "삼성전자", "market": "KOSPI",
         "sharesOutstanding": "5919637922", "currency": "KRW"},
        {"symbol": "AAPL", "name": "애플", "market": "NASDAQ",
         "sharesOutstanding": "14702703000", "currency": "USD"},
    ]
    out = parse_stocks(res)
    assert out["005930"]["name"] == "삼성전자"
    assert out["005930"]["shares"] == 5919637922
    assert out["AAPL"]["market"] == "NASDAQ"


def test_candle_metrics():
    candles = [
        {"date": "20240101", "high": 100, "low": 90, "close": 95},
        {"date": "20240102", "high": 120, "low": 95, "close": 100},   # 전일종가
        {"date": "20240103", "high": 130, "low": 98, "close": 110},   # 마지막(+10%)
    ]
    m = candle_metrics(candles)
    assert m["change_pct"] == 10.0        # (110-100)/100
    assert m["high_52w"] == 130
    assert m["low_52w"] == 90
    assert m["prev_close"] == 100
    assert m["last_close"] == 110


def test_candle_metrics_empty():
    m = candle_metrics([])
    assert m["change_pct"] is None and m["high_52w"] is None and m["last_close"] is None


def test_parse_order_normalizes():
    res = {"orderId": "A1", "symbol": "005930", "side": "buy",
           "quantity": 3, "price": 66000, "status": "open", "orderType": "limit"}
    o = parse_order(res)
    assert o["order_id"] == "A1"
    assert o["side"] == "BUY"
    assert o["status"] == "OPEN"
    assert o["order_type"] == "LIMIT"
