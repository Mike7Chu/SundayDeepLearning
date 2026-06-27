"""알림 평가 로직 + 쿨다운 스모크 테스트."""
from __future__ import annotations

import asyncio

import fakeredis.aioredis

from notifier.alerts import evaluate, format_message
from notifier.config import load_alert_config
from notifier.main import _should_send
from shared.schemas import PremiumCell


def _cell(coin: str, pct: float) -> PremiumCell:
    # 알림은 테더 기준 premium_pct 를 평가한다.
    return PremiumCell(
        coin=coin, base_exchange="upbit", ref_exchange="binance",
        base_price_krw=1.0, ref_price_krw=1.0, premium_pct=pct,
        premium_coin_pct=pct, tether_rate=1380.0, forex_rate=1380.0, ts=0.0,
    )


def test_evaluate_thresholds():
    cells = [
        _cell("BTC", 0.5),    # 무시
        _cell("ETH", 3.5),    # high (>=3.0)
        _cell("XRP", -2.0),   # low (<=-1.5)
        _cell("SOL", 3.0),    # high (경계 포함)
    ]
    events = evaluate("upbit->binance", cells, high_pct=3.0, low_pct=-1.5)
    by = {e.coin: e.side for e in events}
    assert by == {"ETH": "high", "XRP": "low", "SOL": "high"}
    assert "김프" in format_message(events[0]) or "역프" in format_message(events[0])


def test_cooldown():
    async def run():
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        ev = evaluate("upbit->binance", [_cell("ETH", 4.0)], 3.0, -1.5)[0]
        assert await _should_send(redis, ev, cooldown=600) is True   # 최초 발송
        assert await _should_send(redis, ev, cooldown=600) is False  # 쿨다운 중
        await redis.aclose()

    asyncio.run(run())


def test_alert_config_loads():
    cfg = load_alert_config()
    assert len(cfg.pairs) >= 1
    assert cfg.premium_high_pct > 0
    assert cfg.premium_low_pct < 0
