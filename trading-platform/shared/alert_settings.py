"""알림 설정 — yaml 기본값 + Redis 오버라이드(대시보드에서 실시간 변경)."""
from __future__ import annotations

import json

import redis.asyncio as aioredis
from pydantic import BaseModel

from notifier.config import load_alert_config
from shared.redis_keys import ALERT_SETTINGS_KEY


class AlertTypes(BaseModel):
    kimp_high: bool = True       # 김프
    kimp_low: bool = True        # 역프
    hyeonseon: bool = True       # 현선(국내현물 vs 해외선물 역프)
    funding_apy: bool = True     # 펀비 과열
    funding_spread: bool = True  # 거래소간 펀비차


class AlertSettings(BaseModel):
    enabled: bool = True                 # 마스터 on/off
    types: AlertTypes = AlertTypes()
    premium_high_pct: float = 3.0
    premium_low_pct: float = -1.5
    hyeonseon_low_pct: float = -1.0
    funding_apy_pct: float = 100.0
    funding_spread_pct: float = 0.1
    cooldown_sec: int = 600
    min_hold_sec: int = 0                # 조건이 이 초 이상 지속될 때만 알림(디바운스)
    exclude_coins: list[str] = []        # 제외 코인(대문자)
    pairs: list[dict] = []               # 감시 쌍 [{base, ref}]

    def excluded(self, coin: str) -> bool:
        return coin.upper() in {c.upper() for c in self.exclude_coins}


def _defaults() -> AlertSettings:
    """config/alerts.yaml을 기본값으로."""
    cfg = load_alert_config()
    return AlertSettings(
        premium_high_pct=cfg.premium_high_pct,
        premium_low_pct=cfg.premium_low_pct,
        hyeonseon_low_pct=cfg.hyeonseon_low_pct,
        funding_apy_pct=cfg.funding_apy_pct,
        funding_spread_pct=cfg.funding_spread_pct,
        cooldown_sec=cfg.cooldown_sec,
        pairs=[{"base": p.base, "ref": p.ref} for p in cfg.pairs],
    )


async def load_settings(redis: aioredis.Redis) -> AlertSettings:
    """기본값 위에 Redis 오버라이드 머지."""
    base = _defaults().model_dump()
    raw = await redis.get(ALERT_SETTINGS_KEY)
    if raw:
        try:
            override = json.loads(raw)
            if isinstance(override, dict):
                base.update({k: v for k, v in override.items() if v is not None})
        except (json.JSONDecodeError, TypeError):
            pass
    return AlertSettings(**base)


async def save_settings(redis: aioredis.Redis, patch: dict) -> AlertSettings:
    """부분 패치를 기존 오버라이드에 머지 저장."""
    raw = await redis.get(ALERT_SETTINGS_KEY)
    current = {}
    if raw:
        try:
            current = json.loads(raw) or {}
        except (json.JSONDecodeError, TypeError):
            current = {}
    current.update(patch)
    await redis.set(ALERT_SETTINGS_KEY, json.dumps(current))
    return await load_settings(redis)
