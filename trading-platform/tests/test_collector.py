"""수집기 순수 헬퍼 테스트 (ccxt 없이 — 펀비 정산주기 파싱·현물 마진 플래그)."""
from __future__ import annotations

from collector.exchanges.adapter import _margin_flag
from collector.exchanges.perp import _interval_hours, _next_ts


def test_interval_hours_unified():
    assert _interval_hours({"interval": "8h"}) == 8.0
    assert _interval_hours({"interval": "1h"}) == 1.0
    assert _interval_hours({"interval": 4}) == 4.0          # 숫자(시간)


def test_interval_hours_from_raw_info():
    # bybit 류: raw info에 분 단위 fundingInterval(480분=8h)
    assert _interval_hours({"info": {"fundingInterval": 480}}) == 8.0
    assert _interval_hours({"info": {"fundingInterval": 240}}) == 4.0
    # 시간 키
    assert _interval_hours({"info": {"fundingIntervalHours": 2}}) == 2.0
    # '8h' 문자열이 raw 시간키에 온 경우
    assert _interval_hours({"info": {"fundingRateInterval": "8h"}}) == 8.0


def test_interval_hours_unknown():
    assert _interval_hours({}) is None
    assert _interval_hours({"info": {"foo": "bar"}}) is None


def test_next_ts():
    assert _next_ts({"nextFundingTimestamp": 1700000000000}) == 1700000000000
    assert _next_ts({"fundingTimestamp": 123}) == 123
    assert _next_ts({}) is None


def test_margin_flag():
    assert _margin_flag({"margin": True}) is True
    assert _margin_flag({"margin": False}) is False
    assert _margin_flag({}) is None          # 미상
