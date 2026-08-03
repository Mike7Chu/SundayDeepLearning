"""브리핑 문구 조립 (순수 함수 — 테스트 용이).

단순 나열을 넘어 '오늘 뭘 할까'가 보이도록: 시장 온도 → 매매 플랜(진입/손절/목표) →
자동매매(모의) 계좌·성적 → 관심종목·가치·배당 순. 모든 입력은 dict(수집은 main.py).
"""
from __future__ import annotations


def _arrow(pct) -> str:
    if pct is None:
        return ""
    return f"{'🔺' if pct >= 0 else '🔻'}{pct:+.2f}%"


def _won(v) -> str:
    """원화 축약: 1억 이상 'N.N억', 1만 이상 'N,NNN만', 그 외 원."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "–"
    if abs(v) >= 1e8:
        return f"{v/1e8:.2f}억"
    if abs(v) >= 1e4:
        return f"{v/1e4:,.0f}만원"
    return f"{v:,.0f}원"


def _px(v, currency=None) -> str:
    if v is None:
        return "?"
    return f"${v:,.2f}" if currency == "USD" else f"{v:,.0f}"


def _market_section(market: dict) -> list[str]:
    if not market:
        return []
    out = []
    idx = []
    for key, label in (("kospi", "코스피"), ("kosdaq", "코스닥")):
        m = market.get(key) or {}
        if m.get("price") is not None:
            idx.append(f"{label} {m['price']:,.2f} {_arrow(m.get('change_pct'))}")
    inv = (market.get("investor") or {}).get("kospi") or {}
    line2 = ""
    if inv.get("foreigner") is not None or inv.get("institution") is not None:
        f, i = inv.get("foreigner"), inv.get("institution")
        parts = []
        if f is not None:
            parts.append(f"외국인 {f:+,.0f}억")
        if i is not None:
            parts.append(f"기관 {i:+,.0f}억")
        line2 = " · ".join(parts) + " (코스피 순매수)"
    if idx or line2:
        out.append("\n[시장 온도]")
        if idx:
            out.append("· " + " · ".join(idx))
        if line2:
            out.append("· " + line2)
    return out


def _us_section(us: dict) -> list[str]:
    """미장 간밤 동향 — 우리가 수집한 미국 유니버스의 등락 상위/하위."""
    if not us:
        return []
    g, l = us.get("gainers") or [], us.get("losers") or []
    if not (g or l):
        return []
    def _row(q):
        return f"{q.get('name') or q.get('code')} {q.get('change_pct', 0):+.1f}%"
    out = ["\n[🌎 미장 간밤 동향]"]
    if g:
        out.append("▲ " + " · ".join(_row(q) for q in g))
    if l:
        out.append("▼ " + " · ".join(_row(q) for q in l))
    return out


def _radar_section(radar: dict) -> list[str]:
    """발굴 레이더 — 급등 전조(거래대금·신고가·강도·실적·추세) 상위."""
    rows = (radar or {}).get("rows") or []
    if not rows:
        return []
    out = ["\n[🚀 발굴 레이더(급등 전조)]"]
    note = (radar.get("regime") or {}).get("note")
    if note:
        out.append(f"· 환경: {note}")
    for r in rows[:4]:
        sig = " · ".join((r.get("signals") or [])[:2])
        out.append(f"· {r.get('name') or r.get('code')} {r.get('change_pct', 0):+.1f}% "
                   f"(레이더 {r.get('radar')}){(' — ' + sig) if sig else ''}")
    out.append("  ※ 예측이 아니라 '지금 깨어나는' 종목 · 되돌림 주의")
    return out


def _plan_section(plan: dict) -> list[str]:
    if not plan:
        return []
    buys, sells = plan.get("buys") or [], plan.get("sells") or []
    regime = plan.get("regime") or {}
    rline = why = None
    if regime.get("regime") not in (None, "unknown", ""):
        strat = "/".join(regime.get("strategy_labels") or regime.get("strategies") or [])
        exp = regime.get("exposure_pct")
        rline = (f"\n[🧭 시황 국면] {regime.get('label')} · 태세 "
                 f"{regime.get('posture')} · 권장전략 {strat}"
                 + (f" · 목표비중 {exp}%" if exp is not None else ""))
        why = " · ".join((regime.get("reasons") or [])[:2])
    if not (buys or sells or rline):
        return []
    out: list[str] = []
    if rline:                                    # 국면은 매수 후보가 없어도 보여준다
        out.append(rline)
        if why:
            out.append(f"   ↳ {why}")
    style = plan.get("style")
    out.append(f"\n[🎯 오늘의 매매 플랜{(' — ' + style) if style else ''}]")
    if buys:
        out.append("🟢 매수 후보")
        for b in buys[:4]:
            cur = b.get("currency")
            head = (f"· {b.get('name','')}({b.get('code','')}) "
                    f"진입 {_px(b.get('entry'), cur)} → 목표 {_px(b.get('target'), cur)} / "
                    f"손절 {_px(b.get('stop'), cur)}")
            sw = b.get("swing")
            if sw is not None:
                tag = b.get("strategy_label") or b.get("strategy")
                head += f" (점수 {sw:g}{(' · ' + tag) if tag else ''})"
            out.append(head)
            rs = " · ".join((b.get("reasons") or [])[:3])
            if rs:
                out.append(f"   ↳ {rs}")
    if not buys:
        out.append("🟢 매수 후보 없음 — 확신 하한 미달/관망(현금 우대)")
    if sells:
        out.append("🔴 매도 점검(보유 중 위험신호)")
        for s in sells[:4]:
            rs = " · ".join((s.get("reasons") or [])[:2])
            out.append(f"· {s.get('name','')}({s.get('code','')}) "
                       f"{s.get('pnl_pct','?')}% · {s.get('action','')}"
                       f"{(' — ' + rs) if rs else ''}")
    return out


def _auto_section(risk: dict, agent: dict) -> list[str]:
    out = ["\n[🤖 자동매매(모의) 계좌]"]
    total = (risk or {}).get("total_asset")
    if total:
        out.append(f"· 계좌 평가 {_won(total)}")
    if agent and agent.get("ts"):
        mv = agent.get("market_view") or ""
        acted, n = agent.get("acted", 0), agent.get("n", 0)
        did = f"매매 {acted}건 실행" if acted else "관망(신규 매매 없음)"
        slot = agent.get("slot", "")
        out.append(f"· 직전 판정({slot}): {did} · 검토 {n}건")
        if mv:
            out.append(f"   ↳ 시장뷰: {mv}")
    return out if len(out) > 1 else []


def _stats_section(stats: dict) -> list[str]:
    if not stats or not stats.get("n"):
        return []
    n = stats["n"]
    exp = stats.get("expectancy_pct")
    wr = stats.get("win_rate")
    net = stats.get("net")
    tail = " · 표본 부족(20회+ 권장)" if n < 20 else ""
    out = ["\n[📈 매매 성적(순손익 기준)]"]
    if exp is not None:
        edge = "엣지 있음 ✅" if exp > 0 else "엣지 없음 ⚠️"
        out.append(f"· 승률 {wr}% · 1회 기대값 {exp:+.2f}% {edge} · "
                   f"순손익 {_won(net)} ({n}회){tail}")
    else:
        out.append(f"· {n}회 체결{tail}")
    return out


def _risk_discipline_section(plan: dict) -> list[str]:
    """손절 규율 — 하드 손절선 이탈(자본보존) 보유를 최상단에 크게 경고(순수 함수).

    깊은 손실이 브리핑 중간에 묻혀 방치되는 걸 막는다. 토스는 수동 매도라 알림이
    마지막 방어선 — 손실 큰 순으로 정렬해 '지금 결정할 것'으로 올린다.
    """
    sells = (plan or {}).get("sells") or []
    hard = [s for s in sells if (s.get("hard") or 0) >= 3]   # 손절선 이탈·딥로스
    if not hard:
        return []
    hard.sort(key=lambda s: s.get("pnl_pct") if s.get("pnl_pct") is not None else 0)
    out = ["\n🚨 [손절 규율 — 하드 손절선 이탈(자본보존)]",
           "  아래는 손절선·딥로스를 벗어난 보유입니다. 손실 관리가 수익보다 큽니다 — 오늘 결정하세요."]
    for s in hard[:5]:
        pnl = s.get("pnl_pct")
        rs = " · ".join((s.get("reasons") or [])[:2])
        out.append(f"· {s.get('name') or s.get('code')}({s.get('code')}) "
                   f"{('' if pnl is None else f'{pnl:+.1f}%')} — {s.get('action', '손절 검토')}"
                   f"{(' · ' + rs) if rs else ''}")
    return out


def compose_brief(quotes: list[dict], value_rows: list[dict],
                  signal_rows: list[dict], dividend_rows: list[dict],
                  drip: list[dict] | None = None, *,
                  market: dict | None = None, plan: dict | None = None,
                  risk: dict | None = None, agent: dict | None = None,
                  stats: dict | None = None, us: dict | None = None,
                  radar: dict | None = None) -> str:
    """수집 데이터 → 한국어 일일 브리핑. 데이터 없는 섹션은 생략.

    앞쪽(시장·미장·플랜·레이더·자동매매·성적)이 '행동'에 가까운 정보, 뒤쪽이 참고 목록.
    """
    lines: list[str] = ["📊 오늘의 주식 브리핑"]
    lines += _risk_discipline_section(plan or {})     # 손절 규율 — 최상단(자본보존 우선)
    lines += _market_section(market or {})
    lines += _us_section(us or {})
    lines += _plan_section(plan or {})
    lines += _radar_section(radar or {})
    lines += _auto_section(risk or {}, agent or {})
    lines += _stats_section(stats or {})

    if quotes:
        movers = sorted(quotes, key=lambda q: abs(q.get("change_pct") or 0), reverse=True)[:5]
        lines.append("\n[관심종목 등락 TOP]")
        for q in movers:
            nm = q.get("name") or q.get("code") or "?"      # 이름 없으면 코드로 폴백
            px = q.get("price") or 0
            pxs = (f"${px:,.2f}" if q.get("currency") == "USD"  # 통화 구분($/원)
                   else f"{int(px):,}원")
            lines.append(f"· {nm} {pxs} {_arrow(q.get('change_pct'))}")

    buys = [s for s in signal_rows if s.get("signal") == "buy"]
    sells = [s for s in signal_rows if s.get("signal") == "sell"]
    if buys or sells:
        lines.append("\n[기술적 시그널]")
        for s in buys:
            lines.append(f"· 🟢매수 {s.get('name','')} (RSI {s.get('rsi')}, {s.get('sma_cross') or '-'})")
        for s in sells:
            lines.append(f"· 🔴매도 {s.get('name','')} (RSI {s.get('rsi')}, {s.get('sma_cross') or '-'})")

    top_value = [v for v in value_rows if v.get("magic_rank") is not None][:3]
    if top_value:
        lines.append("\n[가치 스크리너 상위(마법공식)]")
        for v in top_value:
            tag = f" [{v['fin_period']} TTM]" if v.get("fin_period") else ""
            lines.append(f"· {v.get('name','')} PER {v.get('per')} PBR {v.get('pbr')} "
                         f"ROE {v.get('roe')}%{tag}")

    top_div = [d for d in dividend_rows if d.get("yield_pct")][:3]
    if top_div:
        lines.append("\n[배당수익률 상위]")
        for d in top_div:
            nx = f" · 기준일 {d['next_ex_date']}" if d.get("next_ex_date") else ""
            lines.append(f"· {d.get('name','')} 배당 {d.get('yield_pct')}%{nx}")

    if drip:
        lines.append("\n[정기 적립 제안(DRIP)]")
        for r in drip:
            sh = f" ≈{r['est_shares']}주" if r.get("est_shares") else ""
            lines.append(f"· {r.get('name','')} {int(r.get('monthly_alloc') or 0):,}원{sh}")

    lines.append("\n※ 모니터링 요약이며 투자 추천이 아닙니다.")
    return "\n".join(lines)


def has_content(quotes, value_rows, signal_rows, dividend_rows) -> bool:
    return bool(quotes or signal_rows or
                any(v.get("magic_rank") is not None for v in value_rows) or
                any(d.get("yield_pct") for d in dividend_rows))
