"""AI 포트폴리오 코치 — 스케줄·프롬프트 빌더 테스트(순수 함수)."""
from __future__ import annotations

from datetime import datetime

from research.coach import KST, build_coach_prompt, should_run


def _ts(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=KST).timestamp()


def test_should_run_daily_8am():
    # 8시 전이면 안 돌고, 8시 지나면 1회, 같은 날 재실행 없음, 다음날 다시 1회
    assert should_run(_ts(2026, 7, 7, 7, 59), 0, 8) is False
    assert should_run(_ts(2026, 7, 7, 8, 1), 0, 8) is True
    ran = _ts(2026, 7, 7, 8, 5)
    assert should_run(_ts(2026, 7, 7, 15, 0), ran, 8) is False   # 오늘 이미 점검
    assert should_run(_ts(2026, 7, 8, 8, 1), ran, 8) is True     # 다음날 아침
    assert should_run(_ts(2026, 7, 8, 7, 0), ran, 8) is False    # 다음날 8시 전


def test_should_run_missed_catchup():
    # 재시작 등으로 8시를 놓쳤어도 그날 안에 켜지면 1회 실행(어제 리포트 기준)
    yesterday = _ts(2026, 7, 6, 8, 3)
    assert should_run(_ts(2026, 7, 7, 22, 0), yesterday, 8) is True


def _snap():
    return {
        "total_eval": 40_000_000.0, "pnl_pct": 5.5,
        "holdings": [
            {"symbol": "000660", "name": "SK하이닉스", "eval_amount": 24_000_000,
             "pnl_pct": 12.3},
            {"symbol": "005930", "name": "삼성전자", "eval_amount": 14_000_000,
             "pnl_pct": 8.1},
            {"symbol": "042700", "name": "한미반도체", "eval_amount": 1_000_000,
             "pnl_pct": -40.0},
            {"symbol": "001440", "name": "대한전선", "eval_amount": 1_000_000,
             "pnl_pct": -40.2},
        ],
    }


def test_build_coach_prompt_weights_and_goal():
    goal = {"target_pct": 35, "deadline": "2026-12-31", "memo": "코인 손실 복구"}
    details = {"000660": {"score": 82.0, "verdict": "매수 검토", "change_pct": 2.1,
                          "ni_growth_q_pct": 120.0, "ni_growth_q_label": "2026.1Q",
                          "margin_pct": 15.0}}
    filings = [{"stock_code": "005930", "corp_name": "삼성전자",
                "report_nm": "주요사항보고서", "rcept_dt": "20260706"},
               {"stock_code": "999999", "corp_name": "무관회사",
                "report_nm": "기타", "rcept_dt": "20260706"}]
    risk = {"buy_lock": False, "mdd_pct": 3.2, "cash_pct": 30.0}
    block = build_coach_prompt(_snap(), 12_000_000, goal, details, filings, risk,
                               today="2026-07-07 08:00")
    # 비중: 하이닉스 60%, 삼성 35%(평가액 기준) — 벤치마크 예시 그대로
    assert "SK하이닉스(000660) | 비중 60.0%" in block
    assert "삼성전자(005930) | 비중 35.0%" in block
    # 정량 디테일·분기 실적이 붙는다
    assert "투자매력도 82(매수 검토)" in block
    assert "분기 순이익 YoY +120.0%(2026.1Q)" in block
    # 보유 종목 공시만 포함(무관 종목 제외)
    assert "주요사항보고서" in block and "무관회사" not in block
    # 목표·리스크·현금
    assert "수익률 +35%" in block and "2026-12-31" in block and "코인 손실 복구" in block
    assert "현금 비중 30.0%" in block
    assert "현금(매수여력): 12,000,000원" in block


def test_build_coach_prompt_minimal():
    # 목표·디테일·공시·리스크 전부 없어도 보유만 있으면 생성
    block = build_coach_prompt(_snap(), None, {}, {}, [], {})
    assert "대한전선" in block and "[내 목표]" not in block


def test_build_coach_prompt_usd_holding():
    # 미장 보유: 환율로 원화 환산해 비중 계산, 표시는 달러
    snap = {"total_eval": 41_400_000.0, "holdings": [
        {"symbol": "005930", "name": "삼성전자", "eval_amount": 14_000_000,
         "pnl_pct": 8.0, "currency": "KRW"},
        {"symbol": "NVDA", "name": "엔비디아", "eval_amount": 10_000.0,
         "pnl_pct": 20.0, "currency": "USD"},
    ]}
    block = build_coach_prompt(snap, None, {}, {}, [], {}, fx_usdkrw=1400.0)
    # NVDA 원화 환산 1,400만원 → 삼성전자와 각각 50%
    assert "NVDA, 미국) | 비중 50.0% | 평가 $10,000.00" in block
    assert "삼성전자(005930) | 비중 50.0%" in block
    assert "1달러 = 1,400.0원" in block


def test_market_block_in_prompt():
    from research.coach import market_block

    ind = {"kospi": {"price": 8123.45, "change_pct": 0.52},
           "kosdaq": {"price": 912.3},
           "investor": {"kospi": {"date": "2026-07-09", "foreigner": 3800.0,
                                  "institution": -800.0, "individual": -1500.0}}}
    lines = market_block(ind)
    assert any("코스피 8,123.45 (+0.52%)" in s for s in lines)
    assert any("외국인 +3,800" in s and "기관 -800" in s for s in lines)
    assert market_block(None) == []
    # 프롬프트 전체에 시장 블록이 앞머리로 붙는다
    block = build_coach_prompt(_snap(), None, {}, {}, [], {}, indicators=ind)
    assert block.startswith("[시장 지표]")


def test_us_semi_block_in_prompt():
    from research.coach import us_semi_block

    rows = [
        {"symbol": "NVDA", "name": "엔비디아", "price": 131.38, "change_pct": 2.28},
        {"symbol": "AMD", "name": "AMD", "price": 162.5, "change_pct": -1.1},
        {"symbol": "TSM", "name": "TSMC", "price": 210.0, "change_pct": None},
    ]
    lines = us_semi_block(rows)
    assert any("엔비디아 $131.38 (+2.28%)" in s for s in lines)
    # 바스켓 평균은 등락률 있는 종목만: (2.28 - 1.1) / 2 = +0.59
    assert any("바스켓 평균 등락: +0.59%" in s for s in lines)
    assert us_semi_block([]) == [] and us_semi_block(None) == []
    # 프롬프트에 미국 블록 포함
    block = build_coach_prompt(_snap(), None, {}, {}, [], {}, us_semis=rows)
    assert "[미국 반도체 — 간밤 종가·등락(실측, 토스)]" in block
