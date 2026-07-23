"""오늘의 매매 플랜 — 설문 맞춤(실적+추세 스윙 · 후보 3개+근거 · 중립 리스크 · KR+US).

전 시장(국내 3,600 + 미국 100)에서 ①1차: 차트 없이 걸러지는 신호(실적 개선 YoY,
52주 위치, 등락)로 상위 후보를 추리고 ②2차: 일봉으로 스윙 점수(실적 40 + 추세 40 +
타이밍 20)를 매겨 매수 후보 3개, 보유 종목의 매도 신호로 매도 점검 3개를 뽑는다.
전부 순수 함수 — 데이터 적재·저장은 engine/main.py 담당. 판단 보조이며 매매 지시 아님.
"""
from __future__ import annotations

import datetime

from api.services.stock_signal import (
    adx,
    evaluate_signals,
    krx_tick,
    macd,
    trade_levels,
)


def _parse_ymd(s: str | None) -> datetime.date | None:
    """YYYYMMDD 또는 YYYY-MM-DD → date. 파싱 실패 시 None."""
    if not s:
        return None
    digits = s.replace("-", "")[:8]
    try:
        return datetime.date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
    except (ValueError, IndexError):
        return None


def _days_since(date_str: str | None, today: str | None) -> int | None:
    """두 날짜(YYYYMMDD/YYYY-MM-DD)의 달력일 차이(today − date). 실패 시 None."""
    d1, d2 = _parse_ymd(date_str), _parse_ymd(today)
    if d1 is None or d2 is None:
        return None
    return (d2 - d1).days


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
    """1차 후보(차트 불필요): 전략 분기 — 국내=실적 스윙 / 미국=모멘텀(순수 함수).

    - 국내(KR): 실적 YoY +10% 미만·미상 제외(실적 개선이 스윙 전제) + 52주 상단권.
    - 미국(US): KIS/DART 펀더멘털이 국내 전용이라 실적 YoY가 없음 → 실적 게이트를
      적용하지 않고 **모멘텀**(52주 상단권 + 당일 강세)으로 선별. 2차 swing_metrics가
      SMA·MACD·ADX로 추세를 최종 검증하므로 여기선 하락/약세만 걸러 통과폭을 준다.
    공통: 보유·동전주(잡주) 제외. 상위 top개만 2차(차트) 검증으로.
    """
    rows: list[tuple[float, dict]] = []
    for q in quotes:
        code, price = q.get("code"), q.get("price")
        if not code or not price or code in held:
            continue
        is_us = q.get("currency") == "USD"
        if is_us:
            if price < 5:
                continue                               # 미국 페니주(잡주) 제외
        elif price < 500:
            continue                                   # 국내 동전주 제외
        hi, lo = q.get("high_52w"), q.get("low_52w")
        pos = (price - lo) / (hi - lo) if (hi and lo and hi > lo) else None
        if pos is not None and pos < 0.5:
            continue                                   # 52주 하단권 = 추세 미확인
        chg = q.get("change_pct") or 0
        if is_us:
            # 미국=모멘텀: 실적 없음 → 추세·흐름으로. 근거 전무(52주 미상+당일 약세)면 제외.
            if pos is None and chg <= 0:
                continue
            s1 = (pos if pos is not None else 0.6) * 60 + chg
        else:
            g, _ = _growth_of(q)
            if g is None or g < 10:
                continue                               # 국내 스윙은 실적 개선 전제
            s1 = min(g, 100) + (pos if pos is not None else 0.6) * 50 + chg
        rows.append((s1, q))
    rows.sort(key=lambda x: x[0], reverse=True)
    return [q for _, q in rows[:top]]


def swing_metrics(q: dict, candles: list, today: str | None = None) -> dict | None:
    """2차 스윙 점수(0~100, 순수 함수): 실적 40 + 추세 40 + 타이밍 20.

    지표는 RSI 대신 스윙 검증력이 높은 조합:
    - MACD(12·26·9): 추세 방향·전환 — 강한 추세에서 RSI처럼 조기 과열 오판 없음
    - ADX(14): 추세 '강도' — 진짜 추세와 횡보를 구분(25↑ 강추세)
    - 이격 과열: SMA20 대비 +15% 초과 확장만 추격 금지(실적 랠리 주도주는 통과)
    - PEAD 쿨링(v2): 잠정실적 발표 2일 이내 + 당일 +8% 이상 갭이면 성장 가점을
      절반으로 캡 — 서프라이즈 갭 직후 추격 매수(이미 가격에 반영)를 억제.
      today(YYYYMMDD)가 주어질 때만 판정.
    입력은 캔들 dict 리스트(고가·저가로 ADX 계산) 또는 종가 리스트(ADX 생략).
    탈락 조건: 하락추세(SMA60 아래·역배열) 또는 이격 과열.
    """
    if candles and isinstance(candles[0], dict):
        closes = [c["close"] for c in candles if c.get("close")]
        a = adx(candles)
    else:
        closes = list(candles or [])
        a = None
    if len(closes) < 60:
        return None
    price = q.get("price") or closes[-1]
    sig = evaluate_signals(closes)
    s20, s60 = sig.get("sma20"), sig.get("sma60")
    if not (s20 and s60 and price > s60 and s20 > s60):
        return None                                    # 스윙은 상승추세만
    if price > s20 * 1.15:
        return None                                    # 단기 이격 +15%↑ = 추격 금지
    g, g_label = _growth_of(q)
    g_pts = 15.0 if g is None else max(0.0, min(1.0, (g + 10) / 60)) * 40
    # PEAD 쿨링: 잠정실적 발표 직후(2일 내) 당일 +8%↑ 갭이면 성장 가점 절반 캡.
    # 서프라이즈는 이미 갭에 반영 — 갭 위 추격은 실적이 아니라 흥분을 사는 것.
    pead = False
    flash_used = (q.get("flash_ni_yoy") is not None
                  or q.get("flash_op_yoy") is not None)
    if flash_used and g_pts > 20.0:
        days = _days_since(q.get("flash_date"), today)
        chg = q.get("change_pct")
        if days is not None and 0 <= days <= 2 and chg is not None and chg >= 8:
            g_pts = min(g_pts, 20.0)
            pead = True
    m = macd(closes)
    t_pts = 10.0                                       # 정배열·SMA60 위(게이트 통과)
    if a is not None:
        t_pts += 15.0 if a >= 25 else (10.0 if a >= 20 else 3.0)
    else:
        t_pts += 8.0                                   # ADX 계산 불가 → 중립
    if m:
        if m["hist"] > 0:
            t_pts += 15.0
        elif m["rising"]:
            t_pts += 7.0                               # 하락이지만 반전 중
    if m is None:
        tm = 10.0
    elif m["recent_golden"]:
        tm = 20.0                                      # MACD 상승 전환 직후 = 진입 적기
    elif m["hist"] > 0:
        tm = 12.0
    else:
        tm = 5.0
    if s20 and abs(price / s20 - 1) <= 0.04:
        tm = max(tm, 14.0)                             # SMA20 눌림목 근처
    reasons = []
    if g is not None:
        reasons.append(f"실적 {g:+.0f}%({g_label})")
    if pead:
        reasons.append("실적 갭 반영 중 — PEAD 쿨링(가점 절반, 2일)")
    reasons.append("정배열·SMA60↑")
    if m:
        reasons.append("MACD " + ("골든(상승 전환)" if m["recent_golden"]
                                  else "상승" if m["hist"] > 0
                                  else "반전 중" if m["rising"] else "하락"))
    if a is not None:
        reasons.append(f"추세강도 {a:.0f}" + ("(강함)" if a >= 25 else ""))
    if s20 and abs(price / s20 - 1) <= 0.04:
        reasons.append("눌림목(SMA20 근처)")
    return {"swing": round(g_pts + t_pts + tm, 1), "reasons": reasons,
            "adx": a, "macd_hist": m["hist"] if m else None,
            "momentum_pct": sig.get("momentum_pct")}


def sell_checks(h: dict, closes: list[float]) -> dict:
    """보유 종목 매도 신호(순수 함수) — Hard/Soft 분리(v2). 중립 리스크 기준.

    - Hard(상쇄 절대 불가 — 자본 보존): 손절선 이탈, 딥로스(-20% 이하 — 원금
      회복에 +25%가 필요한 구간). 어떤 실적·급등도 이 심각도를 깎지 못한다.
    - Soft(맥락 상쇄 가능 — 기술 신호): SMA60 이탈, MACD 하락, 목표가 도달,
      완만한 손실(-8%), 실적 감소. 실적 서프라이즈(+20%↑)·당일 급등(+5%↑)이
      soft만 감점한다(예: 상한가 날 'SMA60 아래'는 사실이어도 맥락이 다름 —
      SMA는 하락기 과거 60일 평균이라 급반등 초기를 항상 '이탈'로 읽는다).
    반환 {"severity"(hard+soft), "hard", "soft", "action", "reasons"}.
    """
    reasons: list[str] = []
    hard = 0
    soft = 0
    cur = h.get("cur_price")
    kr = (h.get("symbol") or "").isdigit()
    action = "보유"
    if cur and len(closes) >= 20:
        lv = trade_levels(closes, cur, kr=kr)
        if lv:
            if cur <= lv["stop"]:
                hard += 3
                reasons.append(f"손절선({lv['stop']:,.0f}) 이탈 — 하드(상쇄 불가)")
                action = "손절 검토"
            elif cur >= lv["target"]:
                soft += 2
                reasons.append(f"목표가({lv['target']:,.0f}) 도달")
                action = "익절 검토"
        sig = evaluate_signals(closes)
        s60 = sig.get("sma60")
        if s60 and cur < s60:
            soft += 2
            reasons.append("추세 이탈(SMA60 아래)")
            if action == "보유":
                action = "정리 검토"
        m = macd(closes)
        if m and m["hist"] < 0 and not m["rising"]:
            soft += 1
            reasons.append("MACD 하락(추세 힘 약화)")
    pnl = h.get("pnl_pct")
    if pnl is not None and pnl <= -20:                 # 딥로스 = 하드 손절선
        hard += 3
        reasons.append(f"손실 {pnl:.1f}% — 딥로스 하드 손절선(-20%) 이탈(상쇄 불가)")
        if action in ("보유", "정리 검토"):
            action = "손절 검토"
    elif pnl is not None and pnl <= -8:                # 중립 성향 손절폭
        soft += 2
        reasons.append(f"손실 {pnl:.1f}% (중립 손절폭 -8% 초과)")
        if action == "보유":
            action = "손절 검토"
    g = h.get("_growth")
    if g is not None and g < 0:
        soft += 1
        reasons.append(f"실적 감소 {g:.0f}%")
    # ---- 상쇄는 soft에만: 기술 신호만으로 '정리'를 재촉하지 않는다 ----
    chg = h.get("_chg")
    if chg is not None and chg >= 5 and soft > 0:
        soft = max(0, soft - 1)
        reasons.append(f"오늘 {chg:+.1f}% 급등 중")
    if g is not None and g >= 20 and soft > 0:
        soft = max(0, soft - 2)
        reasons.append(f"단, 실적 {g:+.0f}% 개선 — 기술 신호와 상충(펀더멘털 우위)")
    sev = hard + soft
    # 액션 완화도 hard가 0일 때만 — 하드 신호가 있으면 '손절 검토' 유지.
    if (hard == 0 and g is not None and g >= 20 and sev < 3
            and action in ("정리 검토", "손절 검토")):
        action = "관찰(실적 우위)"
    return {"severity": sev, "hard": hard, "soft": soft,
            "action": action, "reasons": reasons}


def exit_plan(entry: float, cur: float, peak: float | None,
              closes: list[float], kr: bool = True, trail_pct: float = 10.0,
              half_taken: bool = False) -> dict | None:
    """보유 종목 매도 규율(순수) — 트레일링 스탑 + 부분 익절 + 본전 보장.

    '손실은 짧게, 이익은 길게': 오르면 스탑을 고점을 따라 올려 이익을 태우고,
    목표(손익비 1:2) 첫 도달 시 절반 익절 후 나머지는 트레일링으로 러너 관리.
    - entry=평단, cur=현재가, peak=진입 후 최고가(호출부가 갱신·저장).
    - trail_pct=고점 대비 트레일링 폭(%). 유효 스탑은 초기 손절선과 트레일링 중
      높은 쪽(가격이 오르면 스탑도 상승, 절대 내려가지 않음).
    - 본전 보장: 의미있는 수익(+3%↑) 구간이면 스탑을 진입가 아래로 내리지 않음.
    반환 {trail_stop, target, peak, pnl_pct, action, stage, reason} 또는 None.
    """
    if not entry or not cur or len(closes) < 20:
        return None
    lv = trade_levels(closes, cur, kr=kr)
    if not lv:
        return None
    pnl = (cur / entry - 1) * 100
    peak = max(peak or 0.0, cur, entry)
    tick = krx_tick if kr else (lambda p: round(p, 2))
    trail = peak * (1 - trail_pct / 100)
    if cur > entry * 1.03:                       # 본전 보장(수익 구간)
        trail = max(trail, entry)
    stop = tick(max(lv["stop"], trail))          # 트레일링은 초기 손절선보다 위로만
    target = lv["target"]
    if cur <= stop:
        if pnl >= 0:
            action, stage = "익절/청산 검토", "트레일링 스탑 도달"
            reason = f"고점 대비 되돌림 — 트레일링 스탑({stop:,.0f}) 도달, 이익 실현 검토"
        else:
            action, stage = "손절 검토", "손절선 이탈"
            reason = f"손절선({stop:,.0f}) 이탈 — 손실 확대 차단"
    elif not half_taken and cur >= target:
        action, stage = "절반 익절 검토", "목표 도달"
        reason = (f"목표가({target:,.0f}) 도달 — 절반 익절 후 나머지는 "
                  "트레일링으로 이익 태우기")
    else:
        action = "보유"
        stage = "러너 관리" if half_taken else "보유"
        gap = (cur / stop - 1) * 100 if stop else 0
        reason = f"스탑 {stop:,.0f}(현재가 −{gap:.1f}%)까지 보유 — 오르면 스탑도 따라 올라감"
    return {"trail_stop": stop, "target": target, "peak": round(peak, 2),
            "pnl_pct": round(pnl, 1), "action": action, "stage": stage,
            "reason": reason}


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
