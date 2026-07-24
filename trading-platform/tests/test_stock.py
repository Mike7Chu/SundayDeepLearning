"""주식(KIS): 관심종목 로드 + 키 미설정 시 비활성."""
from __future__ import annotations

from collector.stock.kis import (
    KISClient,
    load_watchlist,
    parse_balance,
    parse_growth_ratio,
    parse_overseas_balance,
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


def test_kis_dual_key_creds(monkeypatch):
    # 실전 조회키 + 모의 주문키 '둘 다' — 도메인별로 맞는 앱키를 쓴다.
    from collector.stock import kis as k
    monkeypatch.setattr(k.settings, "kis_app_key", "PAPERKEY")
    monkeypatch.setattr(k.settings, "kis_app_secret", "ps")
    monkeypatch.setattr(k.settings, "kis_real_app_key", "REALKEY")
    monkeypatch.setattr(k.settings, "kis_real_app_secret", "rs")
    monkeypatch.setattr(k.settings, "kis_paper", True)
    c = k.KISClient()
    assert c._has_real and c.base == k._REAL          # 시세는 실전 도메인
    assert c.order_base == k._PAPER                    # 주문은 모의 도메인
    assert c._creds_for(k._REAL) == ("REALKEY", "rs")     # 실전 도메인 → 실전키
    assert c._creds_for(k._PAPER) == ("PAPERKEY", "ps")   # 모의 도메인 → 주문키
    # 실전키 없으면 모든 도메인에서 주문키 사용(단일 키 모드)
    monkeypatch.setattr(k.settings, "kis_real_app_key", "")
    c2 = k.KISClient()
    assert not c2._has_real
    assert c2._creds_for(k._REAL) == ("PAPERKEY", "ps")


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


def test_parse_overseas_balance():
    payload = {"output2": [{"evlu_amt_smtl_amt": "12000.50", "frcr_dncl_amt_2": "3000"}]}
    b = parse_overseas_balance(payload)
    assert b["eval"] == 12000.50 and b["cash"] == 3000.0
    # 후보 필드 폴백(문서 편차)
    alt = parse_overseas_balance({"output2": {"tot_asst_amt": "5000"}})
    assert alt["eval"] == 5000.0
    assert parse_overseas_balance({}) == {"eval": None, "cash": None}
