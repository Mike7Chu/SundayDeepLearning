"""클로드 스윙 결정 에이전트 — 순수 로직(스케줄·컨텍스트·결정 파싱).

완전 위임: 파이썬 정량필터가 깔때기로 후보를 좁힌 뒤(=엔진 플랜의 buys/sells),
하루 N회 클로드가 '최종 BUY/SELL/HOLD'를 판정한다. 단, **매수는 후보 목록 안에서만,
매도는 보유 종목 안에서만** 허용해 환각 티커·임의 주문을 원천 차단(안전).
실제 수량·가격 산정과 한도·쿨다운·리스크실드는 엔진(집행 안전벽)이 담당한다.

이 모듈은 네트워크·부작용이 없는 순수 함수만 — 테스트 용이. 오케스트레이션은 main.py.
"""
from __future__ import annotations

import json
import re


def parse_slots(spec: str) -> list[str]:
    """"09:40,14:30" → ["09:40","14:30"] (유효한 HH:MM만, 정렬)."""
    out = []
    for tok in (spec or "").split(","):
        tok = tok.strip()
        if re.fullmatch(r"[0-2]?\d:[0-5]\d", tok):
            h, m = tok.split(":")
            if 0 <= int(h) <= 23:
                out.append(f"{int(h):02d}:{m}")
    return sorted(set(out))


def _hm_to_min(hm: str) -> int:
    h, m = hm.split(":")
    return int(h) * 60 + int(m)


def market_of(code: str) -> str:
    """종목코드 → 'KR'(국내 6자리) | 'US'(그 외 티커)."""
    return "KR" if str(code).isdigit() else "US"


def tradeable_markets(now_hm: str) -> set:
    """지금(KST HH:MM) 매매 가능한 시장 — 국장 09:00~15:30, 미장 22:30~06:00(KST).

    슬롯을 각 시장 개장 시간에 맞춰 두면(예 09:40=국장, 23:40=미장) 그 슬롯은 해당
    시장만 매매 → '하루 2회, 국장·미장으로' 요구를 시간대로 자연 분리한다.
    """
    m = _hm_to_min(now_hm)
    out = set()
    if 9 * 60 <= m <= 15 * 60 + 30:
        out.add("KR")
    if m >= 22 * 60 + 30 or m <= 6 * 60:      # 자정 넘김
        out.add("US")
    return out


def due_slots(now_hm: str, slots: list[str], fired: dict, today: str) -> list[str]:
    """지금(now_hm=HH:MM) 실행해야 할 슬롯 목록.

    now가 슬롯 시각을 지났고(당일) 아직 오늘 안 돈 슬롯만. fired={slot: 'YYYY-MM-DD'}.
    '지나침' 판정은 슬롯 후 최대 90분 이내에만(재시작 후 과거 슬롯 몰아 실행 방지).
    """
    now_m = _hm_to_min(now_hm)
    out = []
    for s in slots:
        sm = _hm_to_min(s)
        if 0 <= (now_m - sm) <= 90 and fired.get(s) != today:
            out.append(s)
    return out


def parse_decisions(text: str) -> dict:
    """클로드 응답 텍스트 → {market_view, decisions:[{action,code,conviction,qty,reason}]}.

    프롬프트가 JSON을 요구하지만 앞뒤 산문·```json 펜스가 섞일 수 있어 방어적으로 추출.
    action은 BUY/SELL/HOLD로 정규화, code는 대문자, 잘못된 항목은 버린다.
    """
    obj = _extract_json(text)
    if not isinstance(obj, dict):
        return {"market_view": "", "decisions": []}
    out = []
    for d in obj.get("decisions") or []:
        if not isinstance(d, dict):
            continue
        action = str(d.get("action") or "").strip().upper()
        code = str(d.get("code") or "").strip().upper()
        if action not in ("BUY", "SELL", "HOLD") or not code:
            continue
        rec = {"action": action, "code": code,
               "reason": str(d.get("reason") or "").strip()[:280]}
        conv = d.get("conviction")
        if isinstance(conv, (int, float)):
            rec["conviction"] = max(0, min(100, int(conv)))
        qty = d.get("qty")
        if isinstance(qty, (int, float)) and qty > 0:
            rec["qty"] = int(qty)
        out.append(rec)
    return {"market_view": str(obj.get("market_view") or "").strip()[:280],
            "decisions": out}


def _extract_json(text: str) -> dict | None:
    if not text:
        return None
    # ```json … ``` 펜스 우선
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # 중괄호 균형으로 '완전한 객체 전부'를 모아, 결정 키를 가진 걸 우선(산문에 섞인
    # 스키마 예시·다른 중괄호를 잘못 집지 않게). 없으면 마지막 유효 객체.
    objs: list[dict] = []
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        o = json.loads(text[start:i + 1])
                        if isinstance(o, dict):
                            objs.append(o)
                    except json.JSONDecodeError:
                        pass
                    break
        start = text.find("{", start + 1)
    if not objs:
        return None
    for o in reversed(objs):                 # 결정/시장뷰 키를 가진 마지막 객체 우선
        if "decisions" in o or "market_view" in o:
            return o
    return objs[-1]


def build_context(plan: dict, holdings: list[dict], risk: dict,
                  asset: float | None, cash_pct: float | None,
                  market_view: str = "", regime: dict | None = None) -> str:
    """엔진 플랜 + 보유 + 리스크 → 클로드에게 넘길 압축 컨텍스트(순수 텍스트).

    후보(buys)·매도점검(sells)은 이미 정량필터를 통과한 깔때기 결과. 여기에
    TTM 밸류에이션·근거가 담겨 있어 클로드는 '최종 선별'만 한다.
    """
    lines: list[str] = []
    if asset is not None:
        lines.append(f"[계좌] 총자산 {asset:,.0f}원 · 현금비중 "
                     f"{cash_pct if cash_pct is not None else '?'}%")
    if risk:
        lock = "🔒매수잠금(서킷)" if risk.get("buy_lock") else "정상"
        lines.append(f"[리스크실드] {lock} · MDD {risk.get('mdd_pct','?')}% · "
                     f"종목당한도 {risk.get('per_stock_cap','?')}원")
    if market_view:
        lines.append(f"[시장] {market_view}")
    if regime and regime.get("regime") not in (None, "unknown"):
        strat = "/".join(regime.get("strategy_labels") or regime.get("strategies") or [])
        lines.append(f"[시황 국면] {regime.get('label')}({regime.get('posture')}) · "
                     f"권장전략 {strat} · {' · '.join((regime.get('reasons') or [])[:3])}")
        lines.append("  → 국면 태세에 맞게: 위험회피=신규매수 보수적·현금 우대, "
                     "강세추세=주도주 보유 지속, 횡보=과열 추격 금지, 중립=우량 선별.")

    hold = [h for h in (holdings or []) if h.get("symbol")]
    if hold:
        lines.append("\n[보유 종목 — 매도/유지 판단 대상]")
        for h in hold[:20]:
            pnl = h.get("pnl_pct")
            lines.append(
                f"- {h.get('name') or h.get('symbol')}({h.get('symbol')}) "
                f"{h.get('quantity','?')}주 · 손익 "
                f"{('+' if (pnl or 0) >= 0 else '')}{pnl if pnl is not None else '?'}%")

    buys = plan.get("buys") or []
    if buys:
        lines.append("\n[매수 후보 — 이 목록 안에서만 BUY 가능]")
        for b in buys[:30]:
            cur = "$" if b.get("currency") == "USD" else ""
            rs = " · ".join((b.get("reasons") or [])[:4])
            tag = b.get("strategy_label") or b.get("strategy") or ""
            lines.append(
                f"- {b.get('name')}({b.get('code')}) 현재 {cur}{b.get('price')} "
                f"· {tag} {b.get('swing','?')}점 · 진입 {cur}{b.get('entry')}"
                f"/손절 {cur}{b.get('stop')}/목표 {cur}{b.get('target')} · {rs}")

    sells = plan.get("sells") or []
    if sells:
        lines.append("\n[매도 점검 대상(보유 중 위험신호)]")
        for s in sells[:20]:
            rs = " · ".join((s.get("reasons") or [])[:3])
            lines.append(
                f"- {s.get('name')}({s.get('code')}) 손익 {s.get('pnl_pct','?')}% "
                f"· 심각도 {s.get('severity','?')} · {s.get('action','')} · {rs}")
    return "\n".join(lines)
