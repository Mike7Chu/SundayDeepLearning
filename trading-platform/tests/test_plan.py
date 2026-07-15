"""오늘의 매매 플랜(설문 맞춤 스윙) — 1차 랭킹·스윙 점수·매도 신호 테스트(순수)."""
from __future__ import annotations

from engine.plan import sell_checks, stage1_rank, suggest_qty, swing_metrics


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
    # 순수 단조 상승(RSI 100 과열)은 추격 금지 → 탈락
    hot = [1000 + i * 10 for i in range(80)]
    assert swing_metrics(_q("H", hot[-1], g=40), hot) is None
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
