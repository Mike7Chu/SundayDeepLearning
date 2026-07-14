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
    FX_USDKRW_KEY,
    MARKET_INDICATORS_KEY,
    STOCK_MARKET_KEY,
    TOSS_ACCOUNT_KEY,
    TOSS_HOLDINGS_KEY,
)

KST = timezone(timedelta(hours=9))

# 미국 반도체 참조 바스켓 — 토스 미장 유니버스가 수집한 간밤 종가·등락을 코치에 주입.
# (SOX 지수는 토스 미제공 → 대표 종목 바스켓 평균으로 근사. 웹검색 불필요·실측.)
US_SEMI_REFS = [("NVDA", "엔비디아"), ("AMD", "AMD"), ("AVGO", "브로드컴"),
                ("TSM", "TSMC"), ("MU", "마이크론"), ("INTC", "인텔")]


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


def market_block(ind: dict | None) -> list[str]:
    """시장 지표(지수·투자자별 수급) → 프롬프트 라인(순수 함수). 없으면 빈 리스트."""
    if not isinstance(ind, dict):
        return []
    lines: list[str] = []
    idx = []
    for sym, label in (("kospi", "코스피"), ("kosdaq", "코스닥")):
        d = ind.get(sym) or {}
        if d.get("price") is not None:
            chg = (f" ({d['change_pct']:+.2f}%)"
                   if d.get("change_pct") is not None else "")
            idx.append(f"{label} {d['price']:,.2f}{chg}")
    if idx:
        lines.append("[시장 지표] " + " · ".join(idx))
    inv = (ind.get("investor") or {}).get("kospi")
    if isinstance(inv, dict) and inv.get("foreigner") is not None:
        lines.append(
            f"[수급 — 코스피 투자자별 순매수({inv.get('date', '최근일')}, 억원)] "
            f"외국인 {inv['foreigner']:+,.0f} · "
            f"기관 {(inv.get('institution') or 0):+,.0f} · "
            f"개인 {(inv.get('individual') or 0):+,.0f}")
    return lines


def us_semi_block(rows: list[dict] | None) -> list[str]:
    """미국 반도체 간밤 종가·등락 → 프롬프트 라인(순수 함수). 데이터 없으면 빈 리스트.

    rows: [{name, symbol, price, change_pct}] — 토스 수집 실측(전일 종가 기준).
    """
    rows = [r for r in (rows or []) if r.get("price") is not None]
    if not rows:
        return []
    parts = []
    chgs = []
    for r in rows:
        c = r.get("change_pct")
        txt = f"{r.get('name') or r.get('symbol')} ${r['price']:,.2f}"
        if c is not None:
            txt += f" ({c:+.2f}%)"
            chgs.append(c)
        parts.append(txt)
    lines = ["[미국 반도체 — 간밤 종가·등락(실측, 토스)] " + " · ".join(parts)]
    if chgs:
        avg = sum(chgs) / len(chgs)
        lines.append(f"- 반도체 바스켓 평균 등락: {avg:+.2f}% "
                     "(SOX 지수 근사 — 위 대표 종목 단순평균)")
    return lines


def build_coach_prompt(snap: dict, cash: float | None, goal: dict,
                       details: dict[str, dict], filings: list[dict],
                       risk: dict, today: str = "",
                       fx_usdkrw: float | None = None,
                       indicators: dict | None = None,
                       us_semis: list[dict] | None = None) -> str:
    """보유 스냅샷 + 목표 + 종목 정량 + 공시 + 리스크 → 데이터 블록(순수 함수).

    details: {종목코드: {"score","verdict","ni_growth_q_pct","ni_growth_q_label",
    "change_pct","margin_pct"}} — research.data.gather 결과에서 추림.
    미국 보유(currency=USD)는 환율(fx_usdkrw)로 원화 환산해 비중을 계산한다.
    """
    hs = snap.get("holdings") or []

    def _krw(h: dict) -> float:
        ev = h.get("eval_amount") or 0
        if h.get("currency") == "USD":
            return ev * fx_usdkrw if fx_usdkrw else 0.0   # 환율 없으면 비중 계산서 제외
        return ev

    total = sum(_krw(h) for h in hs)
    lines = market_block(indicators) + us_semi_block(us_semis)
    lines.append(f"[내 실계좌 — {today or '오늘'} 기준]" if today else "[내 실계좌]")
    te = snap.get("total_eval") or total
    lines.append(f"- 총 평가액: {te:,.0f}원"
                 + (f" · 현금(매수여력): {cash:,.0f}원" if cash else ""))
    if snap.get("pnl_pct") is not None:
        lines.append(f"- 전체 수익률: {snap['pnl_pct']:+.2f}%")
    if fx_usdkrw:
        lines.append(f"- 환율: 1달러 = {fx_usdkrw:,.1f}원")
    lines.append("보유 종목(비중=평가액 기준, 원화 환산):")
    for h in sorted(hs, key=lambda x: -_krw(x)):
        code = h.get("symbol", "")
        usd = h.get("currency") == "USD"
        w = _pct(_krw(h), total)
        ev_txt = (f"${h.get('eval_amount') or 0:,.2f}" if usd
                  else f"{h.get('eval_amount') or 0:,.0f}원")
        row = (f"- {h.get('name') or code}({code}{', 미국' if usd else ''}) | "
               f"비중 {w}% | 평가 {ev_txt} | "
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
    fx = None
    fx_raw = await redis.get(FX_USDKRW_KEY)
    if fx_raw:
        try:
            fx = float(json.loads(fx_raw).get("rate") or 0) or None
        except (json.JSONDecodeError, TypeError, ValueError):
            fx = None
    details: dict[str, dict] = {}
    for h in snap["holdings"]:
        code = h.get("symbol", "")
        if not code:
            continue
        sd = await gather(redis, code)   # 미국 티커도 토스 수집 quote 있으면 포함
        if sd:
            details[code] = {
                "score": sd.score, "verdict": sd.verdict,
                "change_pct": sd.change_pct, "margin_pct": sd.margin_pct,
                "ni_growth_q_pct": sd.ni_growth_q_pct,
                "ni_growth_q_label": sd.ni_growth_q_label,
            }
    ind = None
    ind_raw = await redis.get(MARKET_INDICATORS_KEY)
    if ind_raw:
        try:
            ind = json.loads(ind_raw)
        except (json.JSONDecodeError, TypeError):
            ind = None
    # 미국 반도체 간밤 실측(토스 미장 유니버스 수집분) — 웹검색 없이 주입
    us_semis: list[dict] = []
    for sym, name in US_SEMI_REFS:
        raw_m = await redis.hget(STOCK_MARKET_KEY, sym)
        if not raw_m:
            continue
        try:
            rec = json.loads(raw_m)
        except (json.JSONDecodeError, TypeError):
            continue
        us_semis.append({"symbol": sym, "name": rec.get("name") or name,
                         "price": rec.get("price"),
                         "change_pct": rec.get("change_pct")})
    today = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    return build_coach_prompt(snap, cash, goal, details, filings, risk, today,
                              fx_usdkrw=fx, indicators=ind, us_semis=us_semis)
