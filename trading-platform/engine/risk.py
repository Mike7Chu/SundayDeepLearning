"""멍거 리스크 실드 — 순수 함수(네트워크 無, 테스트 용이).

역방향 사고(Inversion): '어떻게 벌까'보다 '어떻게 망하지 않을까'를 먼저 검증한다.
  1) 포트폴리오 서킷 브레이커: 최고점 대비 MDD가 한도(-15%) 도달 → 모든 자동 매수 잠금.
  2) 단일 종목 한도: 1회 매수금액이 총자산의 5%를 초과할 수 없음.
  3) 현금 바닥: 현금 비중이 25% 미만이면 추가 매수 시그널 전부 무시.
"""
from __future__ import annotations


def evaluate_risk(total_asset: float | None, peak_asset: float | None,
                  cash: float | None, *,
                  mdd_limit_pct: float = 15.0,
                  max_stock_pct: float = 5.0,
                  cash_floor_pct: float = 25.0) -> dict:
    """자산 스냅샷 → {buy_lock, mdd_pct, cash_pct, per_stock_cap, reasons}.

    데이터가 없으면 해당 규칙은 판단 보류(None) — 단 total_asset이 없으면
    안전하게 buy_lock=True (모르면 사지 않는다).
    """
    reasons: list[str] = []
    buy_lock = False

    if not total_asset or total_asset <= 0:
        return {"buy_lock": True, "mdd_pct": None, "cash_pct": None,
                "per_stock_cap": None,
                "reasons": ["자산 데이터 없음 — 확인 전 매수 금지(능력 범위 밖)"]}

    mdd_pct = None
    if peak_asset and peak_asset > 0:
        mdd_pct = round((peak_asset - total_asset) / peak_asset * 100, 2)
        if mdd_pct >= mdd_limit_pct:
            buy_lock = True
            reasons.append(f"서킷 브레이커: 최고점 대비 -{mdd_pct:.1f}% "
                           f"(한도 -{mdd_limit_pct:.0f}%) → 자동 매수 잠금")

    cash_pct = None
    if cash is not None:
        cash_pct = round(cash / total_asset * 100, 2)
        if cash_pct < cash_floor_pct:
            buy_lock = True
            reasons.append(f"현금 비중 {cash_pct:.1f}% < {cash_floor_pct:.0f}% "
                           "→ 추가 매수 시그널 무시")

    per_stock_cap = round(total_asset * max_stock_pct / 100.0, 0)
    if not reasons:
        reasons.append("정상 — 매수 허용 범위")
    return {"buy_lock": buy_lock, "mdd_pct": mdd_pct, "cash_pct": cash_pct,
            "per_stock_cap": per_stock_cap, "reasons": reasons}


def order_allowed(risk: dict, side: str, est_amount: float,
                  paper: bool = False) -> tuple[bool, str]:
    """주문 1건이 리스크 실드를 통과하는지. 매도(SELL)는 항상 허용(위험 축소).

    paper=True(모의 계좌 리허설)면 buy_lock(실계좌 현금·MDD 기준)은 무시한다 —
    가짜 돈 리허설을 실제 계좌 상태로 막으면 모의투자의 의미가 없다. 단일종목
    한도(per_stock_cap)는 과대 주문 방지용이라 모의에서도 유지.
    """
    if side.upper() != "BUY":
        return True, ""
    if not isinstance(risk, dict) or not risk:
        return True, ""     # 엔진 미가동 시 기존 게이트(TOSS_TRADING_ENABLED 등)만 적용
    if risk.get("buy_lock") and not paper:
        return False, "리스크 실드: " + " / ".join(risk.get("reasons", ["매수 잠금"]))
    cap = risk.get("per_stock_cap")
    if cap and est_amount > cap:
        return False, (f"단일 종목 한도 초과: 주문 {est_amount:,.0f}원 > "
                       f"한도 {cap:,.0f}원(자산의 5%)")
    return True, ""
