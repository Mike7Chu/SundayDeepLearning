"""검증 계층(Validation First) — 점수가 실제 수익률을 예측하는지 측정.

STOCKLAB v2 원칙: 규칙은 '좋아 보여서'가 아니라 '검증돼서' 존재한다.
- 캘리브레이션: 점수 구간별 실현 수익률(포워드 로그 T+N vs 현재가)
- 축 IC(Information Coefficient): 각 축 점수와 미래 수익률의 스피어만 상관
- 중복 반영(Double Counting): 축끼리의 피어슨 상관 — 높으면 같은 정보를 두 번 셈
전부 순수 함수 — 데이터 적재는 engine(포워드 로그), 조회는 API가 담당.
"""
from __future__ import annotations


def _rank(xs: list[float]) -> list[float]:
    """평균 순위(동점은 순위 평균) — 스피어만 상관용.

    동점 평균 처리가 없으면 상수열도 서로 다른 순위를 받아 무의미한
    상관값이 나온다(상수열은 pearson이 None을 돌려주는 게 정답).
    """
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2
        for k in range(i, j + 1):
            r[order[k]] = avg
        i = j + 1
    return r


def pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 8:
        return None
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return round(sxy / (sxx * syy) ** 0.5, 3)


def spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 8:
        return None
    return pearson(_rank(xs), _rank(ys))


def calibration_buckets(pairs: list[tuple[float, float]],
                        edges: tuple = (45, 60, 70, 80, 90)) -> list[dict]:
    """(점수, 수익률%) 쌍 → 점수 구간별 통계(순수 함수).

    반환 [{bucket, n, avg_ret, median_ret, win_rate}] — 점수가 높을수록
    수익률이 계단식으로 좋아져야 '점수가 작동한다'고 말할 수 있다.
    """
    def bucket_of(s: float) -> str:
        prev = 0
        for e in edges:
            if s < e:
                return f"{prev}~{e}"
            prev = e
        return f"{edges[-1]}+"

    groups: dict[str, list[float]] = {}
    for s, r in pairs:
        groups.setdefault(bucket_of(s), []).append(r)
    out = []
    for b, rets in groups.items():
        rets.sort()
        n = len(rets)
        out.append({
            "bucket": b, "n": n,
            "avg_ret": round(sum(rets) / n, 2),
            "median_ret": round(rets[n // 2], 2),
            "win_rate": round(100 * sum(1 for r in rets if r > 0) / n, 1),
        })
    order = {f"{0}~{edges[0]}": 0}
    prev = edges[0]
    for i, e in enumerate(edges[1:], 1):
        order[f"{prev}~{e}"] = i
        prev = e
    order[f"{edges[-1]}+"] = len(edges)
    out.sort(key=lambda r: order.get(r["bucket"], 99))
    return out


AXES = ("value", "quality", "growth", "momentum", "timing")


def axis_ic(rows: list[dict], rets: list[float]) -> dict[str, float | None]:
    """축별 IC — 각 축 점수와 미래 수익률의 스피어만 상관(순수 함수).

    IC>0.05면 유의미한 예측력, ~0이면 그 축은 성과에 기여하지 않는 것.
    Ablation의 경량 버전('Rule Importance').
    """
    out: dict[str, float | None] = {}
    for ax in AXES:
        xs, ys = [], []
        for r, ret in zip(rows, rets):
            v = r.get(ax)
            if v is not None:
                xs.append(float(v))
                ys.append(ret)
        out[ax] = spearman(xs, ys)
    return out


def axis_correlation(rows: list[dict]) -> dict[str, float | None]:
    """축 간 피어슨 상관(중복 반영 탐지, 순수 함수).

    |r|>0.7이면 두 축이 사실상 같은 정보 — 가중치 중복(Double Counting) 신호.
    반환 {"momentum×timing": 0.82, ...} (상삼각만).
    """
    out: dict[str, float | None] = {}
    for i, a in enumerate(AXES):
        for b in AXES[i + 1:]:
            xs, ys = [], []
            for r in rows:
                va, vb = r.get(a), r.get(b)
                if va is not None and vb is not None:
                    xs.append(float(va))
                    ys.append(float(vb))
            out[f"{a}×{b}"] = pearson(xs, ys)
    return out


def forward_pairs(snapshot: dict[str, dict], prices: dict[str, float],
                  score_key: str = "s") -> tuple[list[tuple[float, float]],
                                                 list[dict], list[float]]:
    """포워드 로그 스냅샷 ∪ 현재가 → (점수,수익률) 쌍 + 축 rows + 수익률(순수).

    snapshot[code] = {"s":점수, "p":당시가, "v","q","g","m","t":축}.
    """
    pairs: list[tuple[float, float]] = []
    rows: list[dict] = []
    rets: list[float] = []
    for code, rec in snapshot.items():
        p0, cur = rec.get("p"), prices.get(code)
        if not p0 or not cur:
            continue
        ret = (cur / p0 - 1) * 100
        pairs.append((rec.get(score_key) or 0.0, round(ret, 2)))
        rows.append({"value": rec.get("v"), "quality": rec.get("q"),
                     "growth": rec.get("g"), "momentum": rec.get("m"),
                     "timing": rec.get("t")})
        rets.append(ret)
    return pairs, rows, rets
