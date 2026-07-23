"""주식(KIS): 관심종목 로드 + 키 미설정 시 비활성."""
from __future__ import annotations

from collector.stock.kis import KISClient, load_watchlist, parse_balance


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
