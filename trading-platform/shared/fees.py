"""거래소 taker 수수료 로더 (config/fees.yaml). 아비트라지 순스프레드용."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

_FEES = Path(__file__).resolve().parent.parent / "config" / "fees.yaml"


@lru_cache(maxsize=1)
def _raw() -> dict:
    try:
        return yaml.safe_load(_FEES.read_text()) or {}
    except OSError:
        return {}


def taker_pct(exchange: str) -> float:
    """거래소 taker 수수료(%) — 미정의면 default."""
    cfg = _raw()
    return float(cfg.get("taker", {}).get(exchange, cfg.get("default", 0.1)))
