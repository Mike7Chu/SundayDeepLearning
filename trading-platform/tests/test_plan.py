"""오늘의 매매 플랜(설문 맞춤 스윙) — 1차 랭킹·스윙 점수·매도 신호 테스트(순수)."""
from __future__ import annotations

from engine.plan import exit_plan, sell_checks, stage1_rank, suggest_qty, swing_metrics


def _q(code, price, g=None, hi=None, lo=None, chg=0.0, **kw):
    return {"code": code, "name": code, "price": price, "ni_growth_q_pct": g,
            "high_52w": hi, "low_52w": lo, "change_pct": chg, **kw}


def test_stage1_rank_filters():
    quotes = [
        _q("A", 10000, g=50, hi=11000, lo=6000),        # 통과(실적+상단권)
        _q("B", 10000, g=5, hi=11000, lo=6000),          # 실적 미달
        _q("C", 10000, g=80, hi=20000, lo=9000),         # 52주 하단권 제외
        _q("D", 10000, g=40, hi=11000, lo=6000),         # 보유 중 제외
        _q("E", 300, g=90, hi=400, lo=100),              # 동전주 제외
        _q("F", None, g=90),                              # 가격 없음
        {"code": "NVDA", "name": "엔비디아", "price": 180.0, "currency": "USD",
         "ni_growth_q_pct": 60, "high_52w": 190.0, "low_52w": 90.0,
         "change_pct": 1.0},                              # 미국 통과
    ]
    out = stage1_rank(quotes, held={"D"})
    codes = [q["code"] for q in out]
    assert "A" in codes and "NVDA" in codes
    assert all(c not in codes for c in ("B", "C", "D", "E", "F"))
    # 잠정실적이 분기보다 우선 사용됨(실적 필터 통과)
    flash = _q("G", 10000, g=None, hi=11000, lo=6000, flash_op_yoy=30.0)
    assert stage1_rank([flash], set())


def test_stage1_rank_us_momentum_no_earnings():
    # 미국=모멘텀: 실적 YoY 미상(KIS/DART 국내 전용)이라도 52주 상단권+당일 강세면 통과.
    quotes = [
        {"code": "AAPL", "name": "애플", "price": 250.0, "currency": "USD",
         "high_52w": 260.0, "low_52w": 160.0, "change_pct": 2.0},   # 상단권 통과
        {"code": "INTC", "name": "인텔", "price": 20.0, "currency": "USD",
         "high_52w": 50.0, "low_52w": 18.0, "change_pct": -1.0},     # 하단권 제외
        {"code": "PENNY", "name": "잡주", "price": 2.0, "currency": "USD",
         "high_52w": 9.0, "low_52w": 1.0, "change_pct": 5.0},        # 페니주 제외
        {"code": "NEW", "name": "신규", "price": 30.0, "currency": "USD",
         "change_pct": -0.5},                                        # 52주 미상+약세 제외
    ]
    codes = [q["code"] for q in stage1_rank(quotes, set())]
    assert codes == ["AAPL"]


def test_swing_metrics_gates_and_score():
    # 지그재그 우상향(+15/-10 반복): 정배열·RSI ~60 — 현실적인 스윙 진입 구간
    up, v = [], 1000.0
    for i in range(90):
        v += 15 if i % 2 == 0 else -10
        up.append(v)
    q = _q("A", up[-1], g=40)
    m = swing_metrics(q, up)
    assert m and m["swing"] > 50
    assert any("실적 +40%" in r for r in m["reasons"])
    # 꾸준한 상승(과거 RSI 게이트라면 100으로 탈락)도 이젠 통과 — 실적 랠리
    # 주도주를 놓치던 오판 제거(한미반도체 케이스)
    steady = [1000 + i * 10 for i in range(80)]
    assert swing_metrics(_q("H", steady[-1], g=40), steady) is not None
    # 단, SMA20 대비 +15% 넘게 튄 포물선 급등(진짜 추격 위험)은 탈락
    para = [1000.0] * 65 + [1000 * 1.08 ** i for i in range(1, 16)]
    assert swing_metrics(_q("P", para[-1], g=40), para) is None
    # 하락 추세(SMA60 아래)면 탈락
    down = [2000 - i * 10 for i in range(80)]
    assert swing_metrics(_q("B", down[-1], g=40), down) is None
    # 봉 부족 탈락
    assert swing_metrics(q, up[:30]) is None


def test_sell_checks_severity():
    down = [2000 - i * 10 for i in range(80)]            # 추세 붕괴
    h = {"symbol": "042700", "cur_price": down[-1], "pnl_pct": -33.8,
         "_growth": -20.0}
    chk = sell_checks(h, down)
    assert chk["severity"] >= 5
    assert chk["action"] in ("손절 검토", "정리 검토")
    assert any("추세 이탈" in r for r in chk["reasons"])
    assert any("손실" in r for r in chk["reasons"])
    # 건강한 보유는 이상무
    up = [1000 + i * 10 for i in range(80)]
    ok = sell_checks({"symbol": "005930", "cur_price": up[-1], "pnl_pct": 5.0},
                     up)
    assert ok["severity"] in (0, 2) or ok["action"] in ("보유", "익절 검토")


def test_suggest_qty():
    # 자산 4,000만 × 7.5% = 300만 / 진입 10만 = 30주
    assert suggest_qty(100_000, 40_000_000, None) == 30
    # 종목당 한도 100만이 더 작으면 그걸 적용 → 10주
    assert suggest_qty(100_000, 40_000_000, 1_000_000) == 10
    # 미국: 예산 300만원 ÷ 환율 1400 = $2,142 / $180 = 11주
    assert suggest_qty(180.0, 40_000_000, None, fx=1400.0, usd=True) == 11
    # 자산 미상·환율 미상(미국)이면 None
    assert suggest_qty(100_000, None, None) is None
    assert suggest_qty(180.0, 40_000_000, None, usd=True) is None


def test_sell_checks_fundamental_offset():
    """실적 서프라이즈 상한가 날 '추세 이탈'만으로 정리를 재촉하지 않는다."""
    down = [280000 - i * 800 for i in range(80)]         # 하락기(SMA60 높음)
    # 한미반도체 시나리오: 잠정실적 +117% 발표 → 상한가(+29.9%), 손실 -18% 잔존
    h = {"symbol": "042700", "cur_price": down[-1] * 1.1, "pnl_pct": -18.0,
         "_growth": 117.0, "_chg": 29.9}
    chk = sell_checks(h, down)
    # 기술 신호(추세 이탈+손실)는 기록되지만 상쇄로 심각도 3 미만 → 목록 제외 수준
    assert chk["severity"] < 3
    assert any("펀더멘털 우위" in r for r in chk["reasons"])
    assert any("급등 중" in r for r in chk["reasons"])
    # 삼성전자 시나리오: 추세 이탈 하나뿐 + 실적 대폭 개선 → 사실상 이상무
    h2 = {"symbol": "005930", "cur_price": down[-1], "pnl_pct": -3.0,
          "_growth": 80.0, "_chg": 1.2}
    chk2 = sell_checks(h2, down)
    assert chk2["severity"] < 3
    # 대한전선 시나리오: 실적 개선 없음 → 상쇄 없이 그대로 경고 유지
    h3 = {"symbol": "001440", "cur_price": down[-1], "pnl_pct": -43.8,
          "_growth": -10.0, "_chg": -2.0}
    chk3 = sell_checks(h3, down)
    assert chk3["severity"] >= 3
    assert chk3["action"] in ("손절 검토", "정리 검토")


def test_sell_checks_hard_exit_no_offset():
    """v2 Hard Exit: 딥로스(-20%↓)는 실적·급등으로도 상쇄 불가(자본 보존)."""
    down = [280000 - i * 800 for i in range(80)]
    # 한미와 같은 최상의 상쇄 조건(실적 +117%·급등 +29.9%)이어도 딥로스면 하드
    h = {"symbol": "042700", "cur_price": down[-1] * 1.1, "pnl_pct": -25.0,
         "_growth": 117.0, "_chg": 29.9}
    chk = sell_checks(h, down)
    assert chk["hard"] >= 3
    assert chk["severity"] >= 3                        # 플랜 표시 문턱 유지
    assert chk["action"] == "손절 검토"                # 관찰로 완화되지 않음
    assert any("상쇄 불가" in r for r in chk["reasons"])
    # 경계 확인: -18%는 소프트(한미 사례 보존) → 상쇄로 3 미만
    soft_case = {**h, "pnl_pct": -18.0}
    chk2 = sell_checks(soft_case, down)
    assert chk2["hard"] == 0 and chk2["severity"] < 3


def test_swing_metrics_pead_cooling():
    """잠정실적 발표 2일 내 + 당일 +8%↑ 갭 → 성장 가점 절반 캡(PEAD 쿨링)."""
    up = []
    v = 1000.0
    for i in range(90):
        v += 15 if i % 2 == 0 else -10
        up.append(v)
    base = _q("A", up[-1], g=None, flash_ni_yoy=120.0, flash_label="잠정",
              flash_date="20260716")
    hot = {**base, "change_pct": 12.0}                 # 발표 다음날 +12% 갭
    cooled = swing_metrics(hot, up, today="20260717")
    normal = swing_metrics({**base, "change_pct": 2.0}, up, today="20260717")
    assert cooled and normal
    assert cooled["swing"] < normal["swing"]           # 성장 가점 캡 확인
    assert any("PEAD" in r for r in cooled["reasons"])
    # 발표 5일 지나면 갭이 커도 쿨링 없음
    later = swing_metrics(hot, up, today="20260722")
    assert later and later["swing"] == normal["swing"]
    assert not any("PEAD" in r for r in later["reasons"])
    # today 미제공(과거 데이터 백필 등)이면 판정 생략 — 기존 동작 보존
    no_today = swing_metrics(hot, up)
    assert no_today and no_today["swing"] == normal["swing"]


def test_exit_plan_trailing_and_partial():
    closes = [10000 + i * 30 for i in range(80)]     # 완만한 상승 추세
    cur = closes[-1]
    # 수익 구간 보유 — 트레일링 스탑이 본전(진입가) 위로 올라와 있고 액션은 보유
    ep = exit_plan(entry=9000, cur=cur, peak=cur, closes=closes, trail_pct=10.0)
    assert ep and ep["action"] == "보유"
    assert ep["trail_stop"] >= 9000               # 본전 보장(수익 구간)
    assert ep["pnl_pct"] > 0
    # 고점 대비 트레일링 폭 이상 하락 → 트레일링 스탑 도달(익절/청산)
    high = cur * 1.3
    dropped = exit_plan(entry=9000, cur=cur, peak=high, closes=closes, trail_pct=10.0)
    assert dropped and dropped["stage"] == "트레일링 스탑 도달"
    assert dropped["action"] == "익절/청산 검토"
    # 데이터 부족/무효 입력이면 None
    assert exit_plan(9000, cur, cur, closes[:10]) is None
    assert exit_plan(0, cur, cur, closes) is None


def test_exit_plan_target_partial_and_hard_stop():
    # 하락 추세에서 손절선 이탈 → 손절 검토
    down = [20000 - i * 100 for i in range(80)]
    lossp = exit_plan(entry=25000, cur=down[-1], peak=25000, closes=down)
    assert lossp and lossp["action"] in ("손절 검토", "익절/청산 검토")
    # 목표가(1:2) 첫 도달 + 미익절 → 절반 익절 검토
    closes = [10000 + i * 20 for i in range(80)]
    from api.services.stock_signal import trade_levels
    lv = trade_levels(closes, closes[-1])
    at_target = exit_plan(entry=closes[-1] * 0.85, cur=lv["target"] + 10,
                          peak=lv["target"] + 10, closes=closes, half_taken=False)
    assert at_target and at_target["stage"] == "목표 도달"
    assert "절반" in at_target["action"]
