"""알림 설정 로더 (config/alerts.yaml)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "alerts.yaml"
_ANNOUNCE_PATH = Path(__file__).resolve().parent.parent / "config" / "announcements.yaml"


class Pair(BaseModel):
    base: str
    ref: str

    @property
    def key(self) -> str:
        return f"{self.base}->{self.ref}"


class AlertConfig(BaseModel):
    pairs: list[Pair]
    premium_high_pct: float = 3.0
    premium_low_pct: float = -1.5
    cooldown_sec: int = 600
    poll_interval_sec: float = 10.0


@lru_cache(maxsize=1)
def load_alert_config(path: str | None = None) -> AlertConfig:
    cfg_path = Path(path) if path else _CONFIG_PATH
    raw = yaml.safe_load(cfg_path.read_text())
    return AlertConfig(**raw)


class AnnounceConfig(BaseModel):
    watched_exchanges: list[str]
    quote_filter: list[str] = []
    poll_interval_sec: float = 60.0


@lru_cache(maxsize=1)
def load_announce_config(path: str | None = None) -> AnnounceConfig:
    cfg_path = Path(path) if path else _ANNOUNCE_PATH
    raw = yaml.safe_load(cfg_path.read_text())
    return AnnounceConfig(**raw)
