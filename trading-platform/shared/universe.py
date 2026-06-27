"""심볼 유니버스 로더 (config/symbols.yaml)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from shared.schemas import ExchangeConfig, Universe

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "symbols.yaml"


@lru_cache(maxsize=1)
def load_universe(path: str | None = None) -> Universe:
    cfg_path = Path(path) if path else _CONFIG_PATH
    raw = yaml.safe_load(cfg_path.read_text())
    exchanges = {
        name: ExchangeConfig(name=name, **conf)
        for name, conf in raw["exchanges"].items()
    }
    return Universe(coins=raw["coins"], exchanges=exchanges)
