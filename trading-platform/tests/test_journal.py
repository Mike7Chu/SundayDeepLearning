"""매매 일지 — 판단 스냅샷·복기(당시 판단 vs 결과) 순수 함수 테스트."""
from __future__ import annotations

from api.services.journal import judgment_snapshot, review


def test_judgment_snapshot():
    sc = {"score": 81, "verdict": "적극 매수 검토", "confidence": 100,
          "value": 21, "quality": 16, "growth": 14, "momentum": 21, "timing": 9}
    sd = {"net_eok": 85}
    j = judgment_snapshot(sc, sd)
    assert j["score"] == 81 and j["verdict"] == "적극 매수 검토"
    assert j["supply_net_eok"] == 85
    # None 입력도 안전
    empty = judgment_snapshot(None, None)
    assert empty["score"] is None and empty["supply_net_eok"] is None


def test_review_buy_outcome_and_hindsight():
    # 높은 점수(81)에 매수 후 상승 → 이익 + 판단 부합
    e = {"side": "BUY", "price": 10000, "judgment": {"score": 81}}
    r = review(e, 11500)
    assert r["ret_pct"] == 15.0 and r["outcome"] == "이익" and r["judged_ok"] is True
    # 낮은 점수(40)에 매수 후 하락 → 손실이지만 '낮은 점수였으니' 판단은 부합
    e2 = {"side": "BUY", "price": 10000, "judgment": {"score": 40}}
    r2 = review(e2, 9000)
    assert r2["outcome"] == "손실" and r2["judged_ok"] is True
    # 높은 점수에 샀는데 하락 → 판단 빗나감
    r3 = review({"side": "BUY", "price": 10000, "judgment": {"score": 80}}, 9500)
    assert r3["judged_ok"] is False


def test_review_sell_and_missing():
    # 매도: 이후 하락이 '잘 판 것' → ret 부호 반전
    r = review({"side": "SELL", "price": 10000, "judgment": {"score": 50}}, 9000)
    assert r["ret_pct"] == 10.0 and r["outcome"] == "이익"
    assert r["judged_ok"] is None                 # 매도 판단은 별도(생략)
    # 현재가 없으면 진행 중
    prog = review({"side": "BUY", "price": 10000, "judgment": {"score": 70}}, None)
    assert prog["ret_pct"] is None and prog["outcome"] == "진행 중"
    # 당시 판단 기록 없으면 judged_ok None
    noj = review({"side": "BUY", "price": 10000, "judgment": {}}, 11000)
    assert noj["outcome"] == "이익" and noj["judged_ok"] is None
