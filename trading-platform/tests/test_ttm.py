"""TTM(최근 4분기) 펀더멘털 계산 — 순수 함수 검증.

KIS 연간(작년 말) EPS/ROE를 DART 분기 누적으로 이어붙여 '현재' 밸류에이션을 만드는
로직. 표본은 원 단위 순이익·자본총계, 시총(억원)·현재가로 EPS/ROE/PER/BPS를 낸다.
"""
from collector.news.dart import parse_financials, ttm_fundamentals


def _accnt(name, ths, frm, fs="CFS"):
    return {"account_nm": name, "thstrm_amount": ths, "frmtrm_amount": frm,
            "fs_div": fs}


def test_parse_financials_exposes_raw_amounts():
    payload = {"status": "000", "list": [
        _accnt("당기순이익", "1,000", "800"),
        _accnt("자본총계", "10,000", "9,000"),
        _accnt("부채총계", "5,000", "4,500"),
        _accnt("매출액", "20,000", "18,000"),
    ]}
    out = parse_financials(payload)
    assert out["ni"] == 1000.0            # 당기(thstrm)
    assert out["ni_prev"] == 800.0        # 전년 동기(frmtrm)
    assert out["equity"] == 10000.0
    assert out["debt_ratio"] == 50.0      # 5000/10000*100
    assert out["ni_yoy"] == 25.0          # (1000-800)/800


def test_ttm_stitch_and_valuation():
    # 작년 연간 100억, 작년 상반기 40억, 올해 상반기 60억 → TTM = 100-40+60 = 120억
    annual_ni = 100e8
    q_cum, q_prev = 60e8, 40e8
    equity = 600e8                        # 자본총계 600억
    # 시총 1,200억, 현재가 24,000원 → 주식수 = 1200억/24000 = 500만주
    ttm = ttm_fundamentals(annual_ni, q_cum, q_prev, equity,
                           market_cap_eok=1200, price=24000)
    assert ttm["ni_ttm_eok"] == 120.0
    # EPS = 120억 / 500만주 = 2,400원
    assert ttm["eps_ttm"] == 2400.0
    # PER = 24000 / 2400 = 10
    assert ttm["per_ttm"] == 10.0
    # ROE = 120억 / 600억 = 20%
    assert ttm["roe_ttm"] == 20.0
    # BPS = 600억 / 500만주 = 12,000원
    assert ttm["bps_ttm"] == 12000.0


def test_ttm_missing_quarter_returns_empty():
    # 분기 미공시(누적/전년 없음) → TTM 계산 불가, 빈 dict → 연간값 폴백 신호
    assert ttm_fundamentals(100e8, None, None, 600e8, 1200, 24000) == {}


def test_ttm_loss_skips_per():
    # 적자(TTM 순이익 음수) → PER 무의미하므로 생략, ROE는 음수로 표시
    ttm = ttm_fundamentals(-50e8, -20e8, 10e8, 600e8,
                           market_cap_eok=1200, price=24000)
    assert "per_ttm" not in ttm            # TTM = -50-10-20... 음수 → PER 생략
    assert ttm["roe_ttm"] < 0
