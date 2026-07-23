"""100억 로드맵 트래커 — 목표까지 필요한 연복리(CAGR) 역산 + 현재 페이스 + 궤도.

'열심히'가 아니라 '얼마의 복리로 언제까지'를 숫자로 — 목표를 현실적 속도로 환산.
전부 순수 함수. 자산 스냅샷 적재는 engine, 조회는 API가 담당.
"""
from __future__ import annotations

import math

_YEAR = 365.25 * 86400


def cagr(begin: float, end: float, years: float) -> float | None:
    """연복리 수익률 — begin이 years 만에 end가 되려면 매년 몇 %? (순수 함수)."""
    if begin <= 0 or end <= 0 or years <= 0:
        return None
    return round(((end / begin) ** (1 / years) - 1) * 100, 1)


def years_to_target(current: float, target: float, rate_pct: float) -> float | None:
    """rate_pct 복리로 current가 target에 닿기까지 걸리는 햇수 (순수 함수)."""
    if current <= 0 or target <= 0 or rate_pct is None:
        return None
    if current >= target:
        return 0.0
    r = rate_pct / 100
    if r <= 0:
        return None                                   # 성장 없으면 도달 불가
    return round(math.log(target / current) / math.log(1 + r), 1)


def roadmap(current: float | None, target: float, now_ts: float,
            deadline_ts: float | None, history: list[dict]) -> dict:
    """100억 로드맵 요약(순수 함수).

    - required_cagr: 지금부터 기한까지 목표 달성에 필요한 연복리(기한 있을 때).
    - pace_cagr: 자산 히스토리(가장 오래된 스냅샷~현재)로 계산한 실제 성장 속도.
    - projected_years/date: 현재 페이스로 목표 도달까지 남은 햇수(pace 있을 때).
    - on_track: 실제 페이스가 필요 속도 이상인가.
    history=[{ts, eval}] 최신순 무관(가장 오래된·최신을 자동 선택). 30일 미만이면 pace None.
    """
    out: dict = {"current": current, "target": target, "progress_pct": None,
                 "required_cagr": None, "pace_cagr": None,
                 "projected_years": None, "on_track": None, "gap": None}
    if not current or current <= 0:
        return out
    out["progress_pct"] = round(current / target * 100, 2)
    if current >= target:
        out["on_track"] = True
        out["projected_years"] = 0.0
        return out
    # 필요 CAGR(기한 있을 때)
    if deadline_ts and deadline_ts > now_ts:
        yrs = (deadline_ts - now_ts) / _YEAR
        out["required_cagr"] = cagr(current, target, yrs)
    # 실제 페이스(자산 히스토리)
    pts = sorted([h for h in history
                  if h.get("ts") and h.get("eval") and h["eval"] > 0],
                 key=lambda h: h["ts"])
    if len(pts) >= 2:
        first, last = pts[0], pts[-1]
        span_yrs = (last["ts"] - first["ts"]) / _YEAR
        if span_yrs >= 30 / 365.25:                   # 최소 30일 이상 쌓여야 의미
            out["pace_cagr"] = cagr(first["eval"], current, span_yrs)
            out["projected_years"] = years_to_target(current, target,
                                                      out["pace_cagr"])
    # 궤도 판정
    if out["required_cagr"] is not None and out["pace_cagr"] is not None:
        out["on_track"] = out["pace_cagr"] >= out["required_cagr"]
        out["gap"] = round(out["pace_cagr"] - out["required_cagr"], 1)
    return out
