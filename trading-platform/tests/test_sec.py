"""SEC EDGAR(미국판 DART) 파서 테스트 — 분기 YoY·실적발표 감지(순수 함수)."""
from __future__ import annotations

import datetime as dt

from collector.news.sec import (
    find_us_earnings_flash,
    parse_quarterly_net_income,
    parse_ticker_map,
)


def test_parse_ticker_map():
    payload = {"0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
               "1": {"cik_str": 320193, "ticker": "aapl", "title": "Apple Inc."},
               "2": {"cik_str": None, "ticker": "BAD"}}
    m = parse_ticker_map(payload)
    assert m == {"NVDA": 1045810, "AAPL": 320193}   # 대문자 통일·불량 제외
    assert parse_ticker_map("nope") == {}


def test_parse_quarterly_net_income_frames_yoy():
    facts = {"facts": {"us-gaap": {"NetIncomeLoss": {"units": {"USD": [
        {"frame": "CY2025Q1", "val": 10_000_000_000},
        {"frame": "CY2025", "val": 60_000_000_000},      # 연간 프레임은 제외
        {"frame": "CY2026Q1", "val": 22_000_000_000},
        {"end": "2026-03-31", "val": 999},                # frame 없는 중복치 제외
    ]}}}}}
    g = parse_quarterly_net_income(facts)
    assert g == {"growth": 120.0, "label": "2026.1Q"}    # 100→220억달러 = +120%
    # 전년 동기 없으면 None(비교 불가)
    only_cur = {"facts": {"us-gaap": {"NetIncomeLoss": {"units": {"USD": [
        {"frame": "CY2026Q1", "val": 1}]}}}}}
    assert parse_quarterly_net_income(only_cur) is None
    assert parse_quarterly_net_income({}) is None


def test_find_us_earnings_flash():
    subs = {"cik": 1045810, "filings": {"recent": {
        "form": ["8-K", "4", "8-K", "10-Q"],
        "filingDate": ["2026-07-12", "2026-07-11", "2026-07-01", "2026-05-28"],
        "items": ["2.02,9.01", "", "8.01", ""],
        "accessionNumber": ["0001045810-26-000123", "a", "b", "c"],
    }}}
    today = dt.date(2026, 7, 15)
    f = find_us_earnings_flash(subs, today=today)
    assert f and "실적 발표(8-K" in f["title"] and f["date"] == "2026-07-12"
    assert "104581026000123" in f["url"]
    # 최근 10일 내 실적성 공시가 없으면 None(8.01 단순공시·오래된 10-Q 제외)
    subs2 = {"cik": 1, "filings": {"recent": {
        "form": ["8-K", "10-Q"], "filingDate": ["2026-07-12", "2026-05-01"],
        "items": ["8.01", ""], "accessionNumber": ["x", "y"]}}}
    assert find_us_earnings_flash(subs2, today=today) is None
    assert find_us_earnings_flash({}, today=today) is None
