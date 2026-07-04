"""2단계 시그널 필터 — 1단계 정량(능력 범위) 순수 함수.

멍거의 '능력 범위': 숫자가 완벽히 존재하는 종목만 다룬다. 하나라도 누락이면
후보 탈락(추측 금지). 기준: ROE > 10%, 0 < PBR < 1.5, 0 < PER < 15.
"""
from __future__ import annotations

_REQUIRED = ("price", "per", "pbr", "eps", "bps")


def quant_filter(quotes: list[dict], *, roe_min: float = 10.0,
                 pbr_max: float = 1.5, per_max: float = 15.0) -> list[dict]:
    """병합 시세 리스트 → 정량 기준 통과 후보(데이터 완전 종목만)."""
    out: list[dict] = []
    for q in quotes:
        if any(q.get(k) is None for k in _REQUIRED):
            continue                      # 데이터 누락 → 능력 범위 밖, 자동 탈락
        eps, bps = q["eps"], q["bps"]
        roe = q.get("roe")
        if roe is None:
            if not bps or bps <= 0:
                continue
            roe = round(eps / bps * 100, 2)
        per, pbr = q["per"], q["pbr"]
        if roe > roe_min and 0 < pbr < pbr_max and 0 < per < per_max:
            out.append({**q, "roe": roe})
    return out


def final_score(quant_score: float, penalty: int | None) -> float | None:
    """최종 점수 = 정량 매력도 − AI 역방향 감점(0~30). 감점 미산출이면 None(대기)."""
    if penalty is None:
        return None
    return round(max(0.0, min(100.0, quant_score)) - penalty, 1)
