"""주식(KIS): 관심종목 로드 + 키 미설정 시 비활성."""
from __future__ import annotations

from collector.stock.kis import (
    KISClient,
    is_derivative_etf,
    load_watchlist,
    parse_kis_investor,
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
    empty = parse_balance({})
    assert empty["total_eval"] is None and empty["cash"] is None and empty["holdings"] == []
    # output1 종목별 보유 파싱(에이전트 매도 대상 — KIS 계좌)
    hb = parse_balance({"output2": [{"nass_amt": "1000000"}], "output1": [
        {"pdno": "005930", "prdt_name": "삼성전자", "hldg_qty": "10",
         "evlu_pfls_rt": "5.2", "prpr": "80000"},
        {"pdno": "000660", "prdt_name": "SK하이닉스", "hldg_qty": "0"}]})   # 0주 제외
    assert len(hb["holdings"]) == 1
    assert hb["holdings"][0] == {"symbol": "005930", "name": "삼성전자",
                                 "quantity": 10.0, "pnl_pct": 5.2,
                                 "price": 80000.0, "currency": "KRW"}


def test_is_derivative_etf():
    # 레버리지·인버스·곱버스·ETN은 True(자동매매 제외), 일반주는 False
    assert is_derivative_etf("KODEX 레버리지")
    assert is_derivative_etf("KODEX 인버스")
    assert is_derivative_etf("TIGER 반도체TOP10레버리지")
    assert is_derivative_etf("KODEX 코스닥150선물인버스")
    assert not is_derivative_etf("삼성전자")
    assert not is_derivative_etf("NAVER")
    assert not is_derivative_etf("")


def test_parse_kis_investor():
    # 순매수 거래대금(원)을 억원으로 환산, 최신 days개, supply_demand 호환 키
    payload = {"output": [
        {"stck_bsop_date": "20260805", "frgn_ntby_tr_pbmn": "32000000000",   # +320억
         "orgn_ntby_tr_pbmn": "5000000000", "prsn_ntby_tr_pbmn": "-37000000000"},
        {"stck_bsop_date": "20260804", "frgn_ntby_tr_pbmn": "-1,000,000,000",  # -10억(콤마)
         "orgn_ntby_tr_pbmn": "2000000000", "prsn_ntby_tr_pbmn": "-1000000000"}]}
    rows = parse_kis_investor(payload, days=5)
    assert len(rows) == 2
    assert rows[0] == {"date": "20260805", "foreigner": 320.0,
                       "institution": 50.0, "individual": -370.0}
    assert rows[1]["foreigner"] == -10.0                  # 콤마·음수 파싱
    # supply_demand와 결합 시 외인+기관 합산 정상
    from api.services.stock_radar import supply_demand
    sd = supply_demand(rows)
    assert sd["net_eok"] == 320 + 50 - 10 + 20            # 외인+기관 2일 합
    # 거래대금 필드가 비면 수량×종가로 폴백(엔드포인트별 필드 편차 방어)
    qonly = {"output": [{"stck_bsop_date": "20260805", "stck_clpr": "50000",
                         "frgn_ntby_qty": "100000", "orgn_ntby_qty": "-20000",
                         "prsn_ntby_qty": "0"}]}
    r2 = parse_kis_investor(qonly)
    assert r2[0]["foreigner"] == 50.0 and r2[0]["institution"] == -10.0  # 10만주×5만/1e8
    # 빈 응답 방어
    assert parse_kis_investor({}) == [] and parse_kis_investor({"output": {}}) == []


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
    empty = parse_overseas_balance({})
    assert empty["eval"] is None and empty["cash"] is None and empty["holdings"] == []
    # output1 미국 보유 파싱
    hb = parse_overseas_balance({"output2": [{"tot_asst_amt": "5000"}], "output1": [
        {"ovrs_pdno": "aapl", "ovrs_item_name": "Apple", "ovrs_cblc_qty": "3",
         "evlu_pfls_rt": "-2.1", "now_pric2": "225.5"}]})
    assert hb["holdings"][0] == {"symbol": "AAPL", "name": "Apple", "quantity": 3.0,
                                 "pnl_pct": -2.1, "price": 225.5, "currency": "USD"}
