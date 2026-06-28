"""AI 가치투자 리서치 테스트 (키 없이 — 렌즈/데이터/idle)."""
from __future__ import annotations

import asyncio
import json

import fakeredis.aioredis

from research.analyst import Analyst
from research.data import StockData, format_for_prompt, from_quote, gather
from research.lenses import LENSES, SYSTEM_PROMPT, lens_by_key, lenses_block
from shared.redis_keys import STOCK_QUOTE_KEY
from shared.settings import settings


def test_lenses_build():
    keys = {l.key for l in LENSES}
    assert keys == {"buffett", "munger", "duan", "li_lu"}
    assert lens_by_key("buffett").name == "워런 버핏"
    assert lens_by_key("nope") is None
    block = lenses_block()
    # 4 거장 이름이 모두 system 프롬프트에 박혀야
    for name in ("워런 버핏", "찰리 멍거", "돤융핑", "리루"):
        assert name in block and name in SYSTEM_PROMPT
    assert "투자 추천" in SYSTEM_PROMPT   # 면책


def test_data_parse_and_format():
    q = {"code": "005930", "name": "삼성전자", "price": 70000,
         "change_pct": 1.2, "per": 12.3, "pbr": 1.1, "eps": None}
    d = from_quote(q)
    assert d.code == "005930" and d.per == 12.3 and d.has_fundamentals()
    text = format_for_prompt(d)
    assert "삼성전자" in text and "12.3" in text
    assert "미상" in text                # eps None → 미상


def test_data_gather_from_redis():
    async def run():
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await redis.hset(STOCK_QUOTE_KEY, "000660",
                         json.dumps({"code": "000660", "name": "SK하이닉스", "price": 200000}))
        d = await gather(redis, "000660")
        assert d is not None and d.name == "SK하이닉스"
        assert await gather(redis, "999999") is None   # 없는 종목
        await redis.aclose()

    asyncio.run(run())


def test_analyst_idle_without_key(monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    a = Analyst()
    assert a.enabled is False

    async def run():
        rep = await a.analyze(StockData(code="005930", name="삼성전자"))
        assert rep["enabled"] is False
        assert "비활성" in rep["report"]
        assert rep["code"] == "005930"

    asyncio.run(run())
