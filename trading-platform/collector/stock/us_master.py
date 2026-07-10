"""미국 주요 종목 유니버스 — config/us_stocks.yaml 로드.

국내(KIS 종목마스터)와 달리 미국은 전 종목 마스터가 없으므로 주요 종목을
큐레이션 목록으로 관리(사용자 편집 가능). 토스 US 티커로 시세·일봉 수집.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

_US = Path(__file__).resolve().parent.parent.parent / "config" / "us_stocks.yaml"


@lru_cache(maxsize=1)
def load_us_universe() -> list[dict]:
    """[{code, name, market:"US"}] — 파일 없거나 손상이면 빈 리스트(안전)."""
    try:
        items = yaml.safe_load(_US.read_text()).get("us_stocks", [])
    except Exception:
        return []
    out: list[dict] = []
    for it in items or []:
        code = str((it or {}).get("code", "")).strip().upper()
        if code:
            out.append({"code": code, "name": (it.get("name") or "").strip(),
                        "market": "US"})
    return out
