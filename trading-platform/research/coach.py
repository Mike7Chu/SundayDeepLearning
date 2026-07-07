"""AI 포트폴리오 코치 — 매일 아침 실계좌 기준 점검(벤치마크: 개인 투자 코치 브리핑).

실보유(토스) 비중·손익 + 종목별 정량 데이터 + 최근 공시 + 리스크 실드 + 사용자의
목표(수익률·기한)를 한 프롬프트로 모아, 종목별 '보유/일부 매도/위험 신호' 판정과
'오늘의 한 줄 결론'을 생성한다. ChatGPT류와 달리 서버가 24h 돌므로 매일 아침
정해진 시각(KST)에 먼저 텔레그램으로 발송할 수 있다. 하루 1콜(토큰 절약).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import redis.asyncio as aioredis

from research.data import gather
from shared.redis_keys import (
    COACH_GOAL_KEY,
    DART_RECENT_KEY,
    ENGINE_RISK_KEY,
    TOSS_ACCOUNT_KEY,
    TOSS_HOLDINGS_KEY,
)

KST = timezone(timedelta(hours=9))


def should_run(now_ts: float, last_ts: float | None, hour_kst: int = 8) -> bool:
    """오늘 KST hour시가 지났고 마지막 점검이 그 이전이면 True (순수 함수).

    재시작해도 Redis의 마지막 리포트 ts 기준이라 하루 1회를 넘지 않는다.
    """
    now = datetime.fromtimestamp(now_ts, KST)
    due = now.replace(hour=hour_kst, minute=0, second=0, microsecond=0)
    if now < due:
        return False
    return (last_ts or 0.0) < due.timestamp()


def _pct(part: float, total: float) -> float:
    return round(part / total * 100, 1) if total else 0.0


def build_coach_prompt(snap: dict, cash: float | None, goal: dict,
                       details: dict[str, dict], filings: list[dict],
                       risk: dict, today: str = "") -> str:
    """보유 스냅샷 + 목표 + 종목 정량 + 공시 + 리스크 → 데이터 블록(순수 함수).

    details: {종목코드: {"score","verdict","ni_growth_q_pct","ni_growth_q_label",
    "change_pct","margin_pct"}} — research.data.gather 결과에서 추림.
    """
    hs = snap.get("holdings") or []
    total = sum(h.get("eval_amount") or 0 for h in hs)
    lines = [f"[내 실계좌 — {today or '오늘'} 기준]" if today else "[내 실계좌]"]
    te = snap.get("total_eval") or total
    lines.append(f"- 총 평가액: {te:,.0f}원"
                 + (f" · 현금(매수여력): {cash:,.0f}원" if cash else ""))
    if snap.get("pnl_pct") is not None:
        lines.append(f"- 전체 수익률: {snap['pnl_pct']:+.2f}%")
    lines.append("보유 종목(비중=평가액 기준):")
    for h in sorted(hs, key=lambda x: -(x.get("eval_amount") or 0)):
        code = h.get("symbol", "")
        w = _pct(h.get("eval_amount") or 0, total)
        row = (f"- {h.get('name') or code}({code}) | 비중 {w}% | "
               f"평가 {h.get('eval_amount') or 0:,.0f}원 | "
               f"수익률 {h.get('pnl_pct') or 0:+.2f}%")
        d = details.get(code) or {}
        extra = []
        if d.get("change_pct") is not None:
            extra.append(f"전일대비 {d['change_pct']:+.2f}%")
        if d.get("score") is not None:
            extra.append(f"투자매력도 {d['score']:.0f}({d.get('verdict') or '?'})")
        if d.get("ni_growth_q_pct") is not None:
            extra.append(f"분기 순이익 YoY {d['ni_growth_q_pct']:+.1f}%"
                         f"({d.get('ni_growth_q_label') or '최근 분기'})")
        if d.get("margin_pct") is not None:
            extra.append(f"안전마진 {d['margin_pct']:+.1f}%")
        if extra:
            row += "\n  · " + " · ".join(extra)
        lines.append(row)
    codes = {h.get("symbol") for h in hs}
    mine = [f for f in filings if f.get("stock_code") in codes][:5]
    if mine:
        lines.append("보유 종목 최근 공시:")
        lines += [f"  · {f.get('corp_name','')} — {f.get('report_nm','')}"
                  f" ({f.get('rcept_dt','')})" for f in mine]
    if risk:
        st = "매수 잠금(서킷브레이커)" if risk.get("buy_lock") else "정상"
        lines.append(f"[리스크 실드] {st}"
                     + (f" · 최고점 대비 -{risk['mdd_pct']:.1f}%"
                        if risk.get("mdd_pct") is not None else "")
                     + (f" · 현금 비중 {risk['cash_pct']:.1f}%"
                        if risk.get("cash_pct") is not None else ""))
    if goal.get("target_pct") is not None:
        g = f"[내 목표] 수익률 {goal['target_pct']:+.0f}%"
        if goal.get("deadline"):
            g += f" (기한: {goal['deadline']})"
        if goal.get("memo"):
            g += f" — 메모: {goal['memo']}"
        lines.append(g)
    return "\n".join(lines)


async def gather_coach(redis: aioredis.Redis) -> str | None:
    """Redis에서 코치 점검에 필요한 전부를 모아 프롬프트 데이터 블록 생성.

    보유가 없으면 None(점검 생략). 종목 정량은 research.data.gather 재사용.
    """
    raw = await redis.get(TOSS_HOLDINGS_KEY)
    if not raw:
        return None
    try:
        snap = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not snap.get("holdings"):
        return None
    acc_raw = await redis.get(TOSS_ACCOUNT_KEY)
    cash = None
    if acc_raw:
        try:
            cash = json.loads(acc_raw).get("buying_power")
        except (json.JSONDecodeError, TypeError):
            cash = None
    goal_raw = await redis.get(COACH_GOAL_KEY)
    goal: dict = {}
    if goal_raw:
        try:
            goal = json.loads(goal_raw)
        except (json.JSONDecodeError, TypeError):
            goal = {}
    risk_raw = await redis.get(ENGINE_RISK_KEY)
    risk: dict = {}
    if risk_raw:
        try:
            risk = json.loads(risk_raw)
        except (json.JSONDecodeError, TypeError):
            risk = {}
    filings: list[dict] = []
    for item in await redis.lrange(DART_RECENT_KEY, 0, 30):
        try:
            filings.append(json.loads(item))
        except (json.JSONDecodeError, TypeError):
            continue
    details: dict[str, dict] = {}
    for h in snap["holdings"]:
        code = h.get("symbol", "")
        if not (code.isdigit() and len(code) == 6):   # 국내 6자리만 정량 보유
            continue
        sd = await gather(redis, code)
        if sd:
            details[code] = {
                "score": sd.score, "verdict": sd.verdict,
                "change_pct": sd.change_pct, "margin_pct": sd.margin_pct,
                "ni_growth_q_pct": sd.ni_growth_q_pct,
                "ni_growth_q_label": sd.ni_growth_q_label,
            }
    today = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    return build_coach_prompt(snap, cash, goal, details, filings, risk, today)
