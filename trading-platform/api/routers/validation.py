"""검증 리포트 API(Validation First) — 포워드 로그로 점수의 실제 예측력을 측정.

엔진이 매일 저장한 점수 스냅샷(fwd:scores:{date})을 T+5/20/60 거래일(달력
≈7/28/84일) 전에서 찾아 현재가와 비교한다:
- 캘리브레이션: 점수 구간별 실현 수익률 — 높은 구간이 계단식으로 좋아야 정상
- 축 IC: 각 축(가치·품질·성장·추세·타이밍)의 스피어만 예측력(경량 Ablation)
- 축 상관: 축끼리 |r|>0.7이면 같은 정보를 두 번 세는 것(Double Counting)
포워드 로그가 해당 기간만큼 쌓이기 전에는 ready=false(빈 결과)가 정상.
"""
from __future__ import annotations

import datetime
import json

from fastapi import APIRouter

from api.redis_client import get_redis
from api.services import validation as val
from api.services.cache import get_or_compute
from api.services.stock_value import load_quotes
from shared.redis_keys import fwd_scores_key

router = APIRouter()

# 호라이즌 라벨 → 달력일(거래일 5/20/60 ≈ 7/28/84일)
HORIZONS = {"T+5": 7, "T+20": 28, "T+60": 84}
_SEARCH_DAYS = 4          # 목표일에 스냅샷이 없으면 과거 방향으로 며칠 더 탐색
_CACHE_TTL = 3600.0       # 하루 1회 스냅샷이 원료라 1시간 캐시로 충분


async def _snapshot(redis, days_back: int) -> tuple[str | None, dict[str, dict]]:
    """오늘−days_back 근처(과거 방향 최대 _SEARCH_DAYS일)의 스냅샷 로드."""
    base = datetime.date.today() - datetime.timedelta(days=days_back)
    for off in range(_SEARCH_DAYS):
        d = (base - datetime.timedelta(days=off)).isoformat()
        raw = await redis.hgetall(fwd_scores_key(d))
        if raw:
            snap: dict[str, dict] = {}
            for code, rec in raw.items():
                try:
                    snap[code] = json.loads(rec)
                except (json.JSONDecodeError, TypeError):
                    continue
            if snap:
                return d, snap
    return None, {}


@router.get("/calibration")
async def calibration() -> dict:
    """점수 캘리브레이션 리포트 — 데이터가 안 쌓였으면 ready=false."""
    return await get_or_compute("calibration", _CACHE_TTL, _compute)


async def _compute() -> dict:
    redis = get_redis()
    prices = {q["code"]: q["price"] for q in await load_quotes(redis)
              if q.get("code") and q.get("price")}
    out: dict = {"horizons": {}, "axis_correlation": {}, "latest": None,
                 "ready": False}
    for label, days in HORIZONS.items():
        date, snap = await _snapshot(redis, days)
        if not snap:
            continue
        pairs, rows, rets = val.forward_pairs(snap, prices)
        if len(pairs) < 8:                      # 표본 부족 — 통계 무의미
            continue
        out["horizons"][label] = {
            "date": date, "n": len(pairs),
            "buckets": val.calibration_buckets(pairs),
            "axis_ic": val.axis_ic(rows, rets),
        }
    # 중복 반영(축 상관)은 미래 수익률이 필요 없어 최신 스냅샷으로 계산
    date, snap = await _snapshot(redis, 0)
    if snap:
        _, rows, _ = val.forward_pairs(
            snap, {c: r.get("p") for c, r in snap.items()})
        out["axis_correlation"] = val.axis_correlation(rows)
        out["latest"] = {"date": date, "n": len(snap)}
    out["ready"] = bool(out["horizons"])
    return out
