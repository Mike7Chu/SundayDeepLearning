"""오늘의 매매 플랜 — 설문 맞춤(실적+추세 스윙 · 후보 3개+근거 · 중립 리스크 · KR+US).

전 시장(국내 3,600 + 미국 100)에서 ①1차: 차트 없이 걸러지는 신호(실적 개선 YoY,
52주 위치, 등락)로 상위 후보를 추리고 ②2차: 일봉으로 스윙 점수(실적 40 + 추세 40 +
타이밍 20)를 매겨 매수 후보 3개, 보유 종목의 매도 신호로 매도 점검 3개를 뽑는다.
전부 순수 함수 — 데이터 적재·저장은 engine/main.py 담당. 판단 보조이며 매매 지시 아님.
"""
from __future__ import annotations

from api.services.stock_signal import evaluate_signals, trade_levels


def _growth_of(q: dict) -> tuple[float | None, str]:
    """실적 YoY 우선순위: 잠정실적(오늘) → 분기보고서 → 연간."""
    for key, label in (("flash_ni_yoy", None), ("flash_op_yoy", None),
                       ("ni_growth_q_pct", None), ("ni_growth_pct", "연간")):
        v = q.get(key)
        if v is not None:
            lab = (label or q.get("flash_label") if key.startswith("flash")
                   else label or q.get("ni_growth_q_label") or "분기")
            return v, lab or "분기"
    return None, ""


def stage1_rank(quotes: list[dict], held: set[str], top: int = 40) -> list[dict]:
    """1차 후보(차트 불필요): 실적 개선 + 52주 상단권 + 당일 흐름(순수 함수).

    스윙 스타일 하드필터 — 실적 YoY +10% 미만·미상 제외, 52주 하단권(추세 프록시)
    제외, 보유·동전주 제외. 상위 top개만 2차(차트) 검증으로.
    """
    rows: list[tuple[float, dict]] = []
    for q in quotes:
        code, price = q.get("code"), q.get("price")
        if not code or not price or code in held:
            continue
        if q.get("currency") != "USD" and price < 500:
            continue                                   # 동전주 제외
        g, _ = _growth_of(q)
        if g is None or g < 10:
            continue                                   # 실적 개선이 스윙의 전제
        hi, lo = q.get("high_52w"), q.get("low_52w")
        pos = (price - lo) / (hi - lo) if (hi and lo and hi > lo) else None
        if pos is not None and pos < 0.5:
            continue                                   # 52주 하단권 = 추세 미확인
        s1 = min(g, 100) + (pos if pos is not None else 0.6) * 50 \
            + (q.get("change_pct") or 0)
        rows.append((s1, q))
    rows.sort(key=lambda x: x[0], reverse=True)
    return [q for _, q in rows[:top]]


def swing_metrics(q: dict, closes: list[float]) -> dict | None:
    """2차 스윙 점수(0~100, 순수 함수): 실적 40 + 추세 40 + 타이밍 20.

    상승추세(정배열·SMA60 위)가 아니거나 RSI 75↑ 과열이면 탈락(None).
    """
    if len(closes) < 60:
        return None
    price = q.get("price") or closes[-1]
    sig = evaluate_signals(closes)
    s20, s60, rsi = sig.get("sma20"), sig.get("sma60"), sig.get("rsi")
    if not (s20 and s60 and price > s60 and s20 > s60):
        return None                                    # 스윙은 상승추세만
    if rsi is not None and rsi > 75:
        return None                                    # 과열 추격 금지
    g, g_label = _growth_of(q)
    g_pts = 15.0 if g is None else max(0.0, min(1.0, (g + 10) / 60)) * 40
    mom3 = sig.get("momentum_pct")
    t_pts = 10.0 + (10.0 if price > s20 else 0.0)      # 정배열 10 + SMA20 위 10
    if mom3 is not None and mom3 > 0:
        t_pts += 10.0
        if mom3 >= 15:
            t_pts += 10.0
    if rsi is None:
        tm = 10.0
    elif 40 <= rsi <= 60:
        tm = 20.0                                      # 눌림·재출발 스위트스팟
    elif rsi < 40:
        tm = 14.0
    elif rsi <= 70:
        tm = 10.0
    else:
        tm = 4.0
    reasons = []
    if g is not None:
        reasons.append(f"실적 {g:+.0f}%({g_label})")
    reasons.append("정배열·SMA60↑" + ("·SMA20↑" if price > s20 else ""))
    if mom3 is not None:
        reasons.append(f"3개월 {mom3:+.0f}%")
    if rsi is not None:
        reasons.append(f"RSI {rsi:.0f}")
    return {"swing": round(g_pts + t_pts + tm, 1), "reasons": reasons,
            "rsi": rsi, "momentum_pct": mom3}


def sell_checks(h: dict, closes: list[float]) -> dict:
    """보유 종목 매도 신호(순수 함수) — 심각도와 근거. 중립 리스크 기준.

    반환 {"severity"(0=이상무), "action", "reasons"}.
    """
    reasons: list[str] = []
    sev = 0
    cur = h.get("cur_price")
    kr = (h.get("symbol") or "").isdigit()
    action = "보유"
    if cur and len(closes) >= 20:
        lv = trade_levels(closes, cur, kr=kr)
        if lv:
            if cur <= lv["stop"]:
                sev += 3
                reasons.append(f"손절선({lv['stop']:,.0f}) 이탈")
                action = "손절 검토"
            elif cur >= lv["target"]:
                sev += 2
                reasons.append(f"목표가({lv['target']:,.0f}) 도달")
                action = "익절 검토"
        sig = evaluate_signals(closes)
        s60 = sig.get("sma60")
        if s60 and cur < s60:
            sev += 2
            reasons.append("추세 이탈(SMA60 아래)")
            if action == "보유":
                action = "정리 검토"
        if sig.get("sma_cross") == "dead":
            sev += 1
            reasons.append("데드크로스")
    pnl = h.get("pnl_pct")
    if pnl is not None and pnl <= -8:                  # 중립 성향 손절폭
        sev += 2
        reasons.append(f"손실 {pnl:.1f}% (중립 손절폭 -8% 초과)")
        if action == "보유":
            action = "손절 검토"
    g = h.get("_growth")
    if g is not None and g < 0:
        sev += 1
        reasons.append(f"실적 감소 {g:.0f}%")
    # ---- 펀더멘털·당일 흐름 상쇄: 기술 신호만으로 '정리'를 재촉하지 않는다 ----
    # (예: 실적 서프라이즈로 상한가 치는 날 'SMA60 아래'는 사실이어도 맥락이 다름.
    #  SMA는 하락기 과거 60일 평균이라 급반등 초기를 항상 '이탈'로 읽는다.)
    chg = h.get("_chg")
    if chg is not None and chg >= 5 and sev > 0:
        sev = max(0, sev - 1)
        reasons.append(f"오늘 {chg:+.1f}% 급등 중")
    if g is not None and g >= 20 and sev > 0:
        sev = max(0, sev - 2)
        reasons.append(f"단, 실적 {g:+.0f}% 개선 — 기술 신호와 상충(펀더멘털 우위)")
        if action in ("정리 검토", "손절 검토") and sev < 3:
            action = "관찰(실적 우위)"
    return {"severity": sev, "action": action, "reasons": reasons}


def suggest_qty(entry: float, asset: float | None, cap: float | None,
                pct: float = 7.5, fx: float | None = None,
                usd: bool = False) -> int | None:
    """제안 수량(순수 함수): 자산의 pct%(중립 5~10%의 중간)와 종목당 한도 중 작은 쪽."""
    if not entry or entry <= 0 or not asset:
        return None
    budget = asset * pct / 100
    if cap:
        budget = min(budget, cap)
    if usd:
        if not fx:
            return None
        budget = budget / fx                           # 원화 예산 → 달러
    n = int(budget // entry)
    return n if n > 0 else None
