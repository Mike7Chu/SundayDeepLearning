"""브리핑 문구 조립 (순수 함수 — 테스트 용이)."""
from __future__ import annotations


def _arrow(pct) -> str:
    if pct is None:
        return ""
    return f"{'🔺' if pct >= 0 else '🔻'}{pct:+.2f}%"


def compose_brief(quotes: list[dict], value_rows: list[dict],
                  signal_rows: list[dict], dividend_rows: list[dict],
                  drip: list[dict] | None = None) -> str:
    """수집 데이터 → 한국어 일일 브리핑. 데이터 없는 섹션은 생략."""
    lines: list[str] = ["📊 오늘의 주식 브리핑"]

    if quotes:
        movers = sorted(quotes, key=lambda q: abs(q.get("change_pct") or 0), reverse=True)[:5]
        lines.append("\n[관심종목 등락 TOP]")
        for q in movers:
            lines.append(f"· {q.get('name','')} {int(q.get('price') or 0):,}원 {_arrow(q.get('change_pct'))}")

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
            lines.append(f"· {v.get('name','')} PER {v.get('per')} PBR {v.get('pbr')} ROE {v.get('roe')}%")

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
