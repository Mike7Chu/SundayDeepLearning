"""봇 설정 — 봇별 파라미터를 Redis 오버라이드로 실시간 조절(대시보드/텔레그램 공용).

각 봇은 매 사이클 effective 설정을 읽어 반영한다. 기본값(DEFAULTS) 위에 Redis
오버라이드(bot:settings:{name})를 머지. FIELDS는 대시보드 폼 자동 생성용 메타.
"""
from __future__ import annotations

import json

import redis.asyncio as aioredis


def bot_settings_key(name: str) -> str:
    return f"bot:settings:{name}"


DEFAULTS: dict[str, dict] = {
    "hyeonseon": {"base": "upbit", "ref": "binance", "entry_pct": -1.0, "exit_pct": 0.0},
    "margin": {"entry_gap": 1.0, "exit_gap": 0.2, "min_volume": 0.0, "exchanges": []},
    "loan": {"entry_gap": 1.5, "exit_gap": 0.3, "borrow_cost_pct": 0.1,
             "min_volume": 0.0, "exchanges": []},
    "sell": {"base": "upbit", "ref": "binance", "buy_pct": 0.0, "sell_pct": 3.0},
}

# 대시보드 폼 자동 생성용(type: num/domestic/overseas/overseas_multi)
FIELDS: dict[str, list[dict]] = {
    "hyeonseon": [
        {"key": "base", "label": "국내 거래소", "type": "domestic"},
        {"key": "ref", "label": "해외 거래소", "type": "overseas"},
        {"key": "entry_pct", "label": "진입(선물역프 ≤ %)", "type": "num", "step": 0.1},
        {"key": "exit_pct", "label": "청산(선물김프 ≥ %)", "type": "num", "step": 0.1},
    ],
    "margin": [
        {"key": "entry_gap", "label": "진입(순스프 ≥ %)", "type": "num", "step": 0.1},
        {"key": "exit_gap", "label": "청산(갭 ≤ %)", "type": "num", "step": 0.1},
        {"key": "min_volume", "label": "최소거래대금(USDT)", "type": "num", "step": 100000},
        {"key": "exchanges", "label": "사용 거래소(비우면 전체)", "type": "overseas_multi"},
    ],
    "loan": [
        {"key": "entry_gap", "label": "진입(순스프 ≥ %)", "type": "num", "step": 0.1},
        {"key": "exit_gap", "label": "청산(갭 ≤ %)", "type": "num", "step": 0.1},
        {"key": "borrow_cost_pct", "label": "차입비용(%)", "type": "num", "step": 0.05},
        {"key": "min_volume", "label": "최소거래대금(USDT)", "type": "num", "step": 100000},
        {"key": "exchanges", "label": "사용 거래소(비우면 전체)", "type": "overseas_multi"},
    ],
    "sell": [
        {"key": "base", "label": "국내 거래소", "type": "domestic"},
        {"key": "ref", "label": "해외 거래소", "type": "overseas"},
        {"key": "buy_pct", "label": "매집(김프 ≤ %)", "type": "num", "step": 0.1},
        {"key": "sell_pct", "label": "익절(김프 ≥ %)", "type": "num", "step": 0.1},
    ],
}


def _merge(defaults: dict, override) -> dict:
    base = dict(defaults)
    if isinstance(override, dict):
        base.update({k: v for k, v in override.items() if k in base and v is not None})
    return base


async def load_bot_settings(redis: aioredis.Redis, name: str) -> dict:
    """DEFAULTS 위에 Redis 오버라이드 머지."""
    raw = await redis.get(bot_settings_key(name))
    override = None
    if raw:
        try:
            override = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            override = None
    return _merge(DEFAULTS.get(name, {}), override)


async def save_bot_settings(redis: aioredis.Redis, name: str, patch: dict) -> dict:
    """유효 키만 머지 저장."""
    allowed = set(DEFAULTS.get(name, {}).keys())
    raw = await redis.get(bot_settings_key(name))
    cur = {}
    if raw:
        try:
            cur = json.loads(raw) or {}
        except (json.JSONDecodeError, TypeError):
            cur = {}
    cur.update({k: v for k, v in patch.items() if k in allowed})
    await redis.set(bot_settings_key(name), json.dumps(cur))
    return await load_bot_settings(redis, name)
