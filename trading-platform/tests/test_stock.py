"""주식(KIS): 관심종목 로드 + 키 미설정 시 비활성."""
from __future__ import annotations

from collector.stock.kis import (
    KISClient,
    load_watchlist,
    parse_balance,
    parse_growth_ratio,
    parse_overseas_daily,
    parse_overseas_price,
    parse_stability_ratio,
    quote_excd,
)


def test_watchlist_loads():
    w = load_watchlist()
    assert len(w) >= 1
    assert any(i["code"] == "005930" for i in w)   # 삼성전자
    assert all("code" in i and "name" in i for i in w)


def test_kis_disabled_without_keys():
    # 테스트 환경엔 KIS 키 없음 → 비활성
    assert KISClient().enabled is False


def test_parse_balance():
    # 순자산(nass_amt)=총자산, 예수금(dnca_tot_amt)=현금
    payload = {"output2": [{"dnca_tot_amt": "4980000", "nass_amt": "10120000",
                            "scts_evlu_amt": "5140000"}]}
    b = parse_balance(payload)
    assert b["total_eval"] == 10120000.0 and b["cash"] == 4980000.0
    # 순자산 없으면 유가증권평가+예수금 폴백
    fb = parse_balance({"output2": [{"dnca_tot_amt": "1000000",
                                     "scts_evlu_amt": "3000000"}]})
    assert fb["total_eval"] == 4000000.0 and fb["cash"] == 1000000.0
    # 빈 응답은 None
    assert parse_balance({}) == {"total_eval": None, "cash": None}


def test_quote_excd():
    assert quote_excd("NASD") == "NAS" and quote_excd("NYSE") == "NYS"
    assert quote_excd("AMEX") == "AMS" and quote_excd("") == "NAS"   # 기본 NAS


def test_parse_overseas_price():
    o = {"last": "313.78", "rate": "1.25", "base": "309.90", "tvol": "1000000"}
    p = parse_overseas_price(o)
    assert p["price"] == 313.78 and p["change_pct"] == 1.25 and p["prev_close"] == 309.90
    assert parse_overseas_price({})["price"] is None


def test_parse_overseas_daily():
    rows = [  # 최신→오래된으로 와도 정렬은 오래된→최신
        {"xymd": "20260724", "open": "310", "high": "315", "low": "309", "clos": "313.78", "tvol": "9"},
        {"xymd": "20260723", "open": "305", "high": "311", "low": "304", "clos": "309.90", "tvol": "8"},
    ]
    out = parse_overseas_daily(rows)
    assert [c["date"] for c in out] == ["20260723", "20260724"]      # 오름차순
    assert out[-1]["close"] == 313.78 and out[0]["open"] == 305.0
    assert parse_overseas_daily([]) == []


def test_parse_finance_ratios():
    # 여러 결산 기간 중 최신(stac_yymm 최대) 행 사용
    growth = [{"stac_yymm": "202409", "grs": "12.5", "bsop_prfi_inrt": "8.3"},
              {"stac_yymm": "202406", "grs": "5.0", "bsop_prfi_inrt": "3.0"}]
    g = parse_growth_ratio(growth)
    assert g["rev_yoy"] == 12.5 and g["op_yoy"] == 8.3 and g["period"] == "202409"
    stab = [{"stac_yymm": "202409", "lblt_rate": "45.2"}]
    s = parse_stability_ratio(stab)
    assert s["debt_ratio"] == 45.2
    # 빈/딕셔너리 입력 방어
    assert parse_growth_ratio([])["rev_yoy"] is None
    assert parse_stability_ratio({"lblt_rate": "100"})["debt_ratio"] == 100.0
