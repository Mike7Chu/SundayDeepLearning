"""토스증권 클라이언트 순수 파서 테스트 (네트워크 無).

응답 봉투 {result}/{error} 처리 + 보유 평가액·수익률 산출을 검증.
필드명은 토스 응답 편차를 대비해 다후보를 수용(_first) — 대표 케이스만 고정 검증.
"""
from __future__ import annotations

import pytest

from collector.stock.toss import (
    TossClient,
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


# ---------- v1.2.2 신규: 랭킹·시장지표·수급·조건주문 파서 ----------
def test_parse_rankings():
    from collector.stock.toss import parse_rankings

    res = {"rankedAt": "2026-06-10T14:30:00+09:00", "rankings": [
        {"rank": 1, "symbol": "005930", "currency": "KRW",
         "price": {"lastPrice": "56500", "basePrice": "55800", "changeRate": "0.0125"},
         "tradingVolume": "18432100", "tradingAmount": "1041436650000"},
        {"rank": 2, "symbol": "NVDA", "currency": "USD",
         "price": {"lastPrice": "131.38", "basePrice": "128.45", "changeRate": None},
         "tradingVolume": "342100", "tradingAmount": "44942580"},
    ]}
    rows = parse_rankings(res)
    assert rows[0]["symbol"] == "005930" and rows[0]["change_pct"] == 1.25
    assert rows[0]["amount_eok"] == 10414.4          # 1조 414억
    assert rows[1]["currency"] == "USD" and rows[1]["change_pct"] is None
    assert parse_rankings({"rankedAt": None, "rankings": []}) == []


def test_parse_indicator_prices_and_investor():
    from collector.stock.toss import parse_indicator_prices, parse_investor_trading

    out = parse_indicator_prices([
        {"symbol": "KOSPI", "lastPrice": "2812.45"},
        {"symbol": "KR_BOND_10Y", "lastPrice": "3.25"},
    ])
    assert out == {"KOSPI": 2812.45, "KR_BOND_10Y": 3.25}
    inv = parse_investor_trading({"nextUntil": None, "records": [{
        "date": "2026-06-11",
        "individual": {"buyAmount": "5200000000000", "sellAmount": "5350000000000"},
        "foreigner": {"buyAmount": "3800000000000", "sellAmount": "3600000000000"},
        "institution": {"buyAmount": "2100000000000", "sellAmount": "2180000000000"},
        "otherCorporation": {"buyAmount": "450000000000", "sellAmount": "420000000000"},
    }]})
    assert inv[0]["foreigner"] == 2000.0     # +2,000억 순매수
    assert inv[0]["individual"] == -1500.0
    assert inv[0]["institution"] == -800.0


def test_parse_conditional_orders():
    from collector.stock.toss import parse_conditional_orders

    res = {"conditionalOrders": [{
        "conditionalOrderId": "gaZIG", "type": "OCO", "status": "WATCHING",
        "symbol": "005930", "market": "KR", "quantity": "100",
        "orderType": "LIMIT", "expireDate": "2026-09-10",
        "first": {"type": "STOP", "status": "WATCHING", "triggerPrice": "305",
                  "orderPrice": "305", "triggeredOrderId": None},
        "second": {"type": "STOP", "status": "WATCHING", "triggerPrice": "295",
                   "orderPrice": "294.5", "triggeredOrderId": None},
        "createdAt": "2026-06-12T09:00:00+09:00"}], "hasNext": False}
    rows = parse_conditional_orders(res)
    assert rows[0]["id"] == "gaZIG" and rows[0]["type"] == "OCO"
    assert rows[0]["first"]["trigger"] == 305.0
    assert rows[0]["second"]["price"] == 294.5



def test_ttl_cache_get_or_compute():
    import asyncio

    from api.services.cache import get_or_compute

    async def run():
        calls = {"n": 0}

        async def factory():
            calls["n"] += 1
            return {"v": calls["n"]}

        # TTL 내 반복 호출은 1회만 계산
        a = await get_or_compute("t:key", 5, factory)
        b = await get_or_compute("t:key", 5, factory)
        assert a == b == {"v": 1} and calls["n"] == 1
        # 동시 요청도 1회만 계산(락)
        rs = await asyncio.gather(*(get_or_compute("t:key2", 5, factory)
                                    for _ in range(5)))
        assert all(r == rs[0] for r in rs) and calls["n"] == 2
        # TTL 0이면 매번 재계산
        await get_or_compute("t:key3", 0, factory)
        await get_or_compute("t:key3", 0, factory)
        assert calls["n"] == 4
    asyncio.run(run())


def test_live_overlay_recomputes_rows_and_totals():
    from collector.stock.toss import live_overlay

    holdings = [
        {"symbol": "005930", "currency": "KRW", "qty": 100.0,
         "avg_price": 65000.0, "cur_price": 70000.0, "eval_amount": 7_000_000.0,
         "pnl": 500_000.0, "pnl_pct": 7.69},
        {"symbol": "NVDA", "currency": "USD", "qty": 10.0,
         "avg_price": 150.0, "cur_price": 170.0, "eval_amount": 1700.0},
    ]
    prices = {"005930": 72000.0, "NVDA": 180.0}
    totals = live_overlay(holdings, prices, fx_usdkrw=1400.0)
    # 행: 실시간가로 평가·손익 재계산
    assert holdings[0]["cur_price"] == 72000.0
    assert holdings[0]["eval_amount"] == 7_200_000.0
    assert holdings[0]["pnl"] == 700_000.0 and holdings[0]["pnl_pct"] == 10.77
    assert holdings[1]["eval_amount"] == 1800.0
    # 합계: KR 720만 + US $1,800×1,400 = 972만 / 원금 650만+210만=860만
    assert totals["total_eval"] == 9_720_000.0
    assert totals["pnl"] == 1_120_000.0
    # USD 보유 있는데 환율 없으면 합계 None(스냅샷 유지), 행은 갱신
    h2 = [dict(h) for h in holdings]
    assert live_overlay(h2, prices, fx_usdkrw=None) is None
    assert h2[0]["cur_price"] == 72000.0
    # 시세 없는 종목은 스냅샷 값 유지
    h3 = [{"symbol": "X", "currency": "KRW", "qty": 1.0, "avg_price": 100.0,
           "cur_price": 110.0, "eval_amount": 110.0}]
    t3 = live_overlay(h3, {}, None)
    assert h3[0]["cur_price"] == 110.0 and t3["total_eval"] == 110.0


class _FakeClient:
    """호출 시퀀스대로 응답을 돌려주는 httpx.AsyncClient 흉내."""

    def __init__(self, get_responses):
        self._get = list(get_responses)
        self.token_calls = 0
        self.get_calls = 0

    async def post(self, url, **kw):
        self.token_calls += 1                      # /oauth2/token
        return _Resp({"access_token": "T", "expires_in": 3600})

    async def get(self, url, **kw):
        self.get_calls += 1
        return _Resp(self._get.pop(0))


def _run_toss(get_responses, monkeypatch, setup=None):
    import asyncio
    from collector.stock import toss as T
    monkeypatch.setattr(T.settings, "toss_client_id", "id")
    monkeypatch.setattr(T.settings, "toss_client_secret", "sec")
    monkeypatch.setattr(T.settings, "toss_min_interval_sec", 0.0)

    async def _nosleep(*a, **k):
        return None
    monkeypatch.setattr(T.asyncio, "sleep", _nosleep)   # 백오프 즉시
    c = _FakeClient(get_responses)
    client = TossClient()
    if setup:
        setup(client)
    out = asyncio.run(client._get(c, "/api/v1/x"))
    return out, c


def test_toss_retries_rate_limit(monkeypatch):
    """rate-limit-exceeded는 백오프 후 재시도해 결국 성공한다."""
    out, c = _run_toss([
        {"error": {"code": "rate-limit-exceeded", "message": "over", "requestId": "r"}},
        {"result": {"ok": 1}},
    ], monkeypatch)
    assert out == {"ok": 1}
    assert c.get_calls == 2                          # 1회 실패 + 1회 재시도 성공


def test_toss_invalid_token_refreshes(monkeypatch):
    """invalid-token이면 캐시 토큰을 폐기하고 재발급 후 재시도한다."""
    def _stale(client):
        client._token, client._exp = "STALE", 9e18
    out, c = _run_toss([
        {"error": {"code": "invalid-token", "message": "bad", "requestId": "r"}},
        {"result": {"ok": 2}},
    ], monkeypatch, setup=_stale)
    assert out == {"ok": 2}
    # 캐시된 STALE 토큰으로 시작 → 폐기 후 재발급(post 1회)해 성공
    assert c.token_calls >= 1


def test_toss_gives_up_after_max_retry(monkeypatch):
    """계속 rate-limit이면 max_retry 후 TossError를 던진다(무한 재시도 아님)."""
    import pytest as _pt
    err = {"error": {"code": "rate-limit-exceeded", "message": "over", "requestId": "r"}}
    with _pt.raises(TossError):
        _run_toss([err, err, err, err, err], monkeypatch)
