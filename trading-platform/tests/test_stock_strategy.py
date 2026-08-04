"""주식 전략 테스트 — 가치 스크리너·시그널·배당·브리핑(순수 함수)."""
from __future__ import annotations

import asyncio
import json

import fakeredis.aioredis

from api.services.stock_dividend import compute_dividend, drip_plan
from api.services.stock_signal import (
    bollinger_pos,
    evaluate_signals,
    momentum_pct,
    rsi,
    signals_for,
    sma,
)
from api.services.stock_value import compute_value
from briefing.compose import compose_brief, has_content
from collector.stock.kis import parse_daily, parse_dividend, parse_price
from shared.redis_keys import stock_ohlcv_key


# ---------- 가치 스크리너 ----------
def test_value_magic_formula_ranking():
    quotes = [
        {"code": "A", "name": "A", "price": 100, "per": 5, "pbr": 0.8, "eps": 20, "bps": 200},
        {"code": "B", "name": "B", "price": 100, "per": 50, "pbr": 5, "eps": 2, "bps": 50},
    ]
    rows = compute_value(quotes)["rows"]
    by = {r["code"]: r for r in rows}
    # A: 저PER·고ROE → 마법랭크 우수 → value_rank 1
    assert by["A"]["value_rank"] == 1
    assert by["A"]["roe"] == 10.0          # 20/200*100
    assert by["A"]["earnings_yield"] == 20.0  # 20/100*100
    assert by["A"]["quality"] >= 2


def test_value_missing_metrics_sink_to_bottom():
    quotes = [
        {"code": "A", "name": "A", "price": 100, "per": 8, "pbr": 1, "eps": 12, "bps": 120},
        {"code": "X", "name": "X", "price": None, "per": None, "pbr": None, "eps": None, "bps": None},
    ]
    rows = compute_value(quotes)["rows"]
    assert rows[0]["code"] == "A" and rows[-1]["code"] == "X"
    assert rows[-1]["magic_rank"] is None


# ---------- 시그널 ----------
def test_indicators():
    assert sma([1, 2, 3, 4], 2) == 3.5
    assert sma([1], 2) is None
    assert rsi([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]) == 100.0  # 전부 상승
    assert momentum_pct([100, 110], 1) == 10.0
    assert bollinger_pos([10] * 20) is None     # 표준편차 0


def test_evaluate_signals_buy():
    # 80봉 상승 추세(모멘텀60 계산엔 61봉+ 필요)
    closes = [100 + i for i in range(80)]        # 꾸준한 상승
    sig = evaluate_signals(closes)
    assert sig["bars"] == 80
    assert sig["momentum_pct"] > 0
    assert sig["signal"] in ("buy", "neutral")


def test_signals_for_redis():
    async def run():
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        candles = [{"date": f"{i:04d}", "close": 100 + i} for i in range(60)]
        await redis.set(stock_ohlcv_key("005930"), json.dumps(candles))
        s = await signals_for(redis, "005930", "삼성전자")
        assert s and s["name"] == "삼성전자" and s["bars"] == 60
        assert await signals_for(redis, "000000") is None   # 데이터 없음
        await redis.aclose()

    asyncio.run(run())


# ---------- 배당 ----------
def test_dividend_yield_and_drip():
    q = {"code": "A", "name": "A", "price": 10000}
    items = [{"date": "20990101", "per_share": 300}, {"date": "20990601", "per_share": 200}]
    d = compute_dividend(q, items)
    assert d["annual_per_share"] == 500 and d["yield_pct"] == 5.0
    assert d["next_ex_date"] == "20990101"
    plan = drip_plan([d], monthly_budget=100000)
    assert plan and plan[0]["monthly_alloc"] == 100000 and plan[0]["est_shares"] == 10


# ---------- 파서 ----------
def test_parse_daily_and_dividend():
    out2 = [
        {"stck_bsop_date": "20240102", "stck_clpr": "200", "acml_vol": "10"},
        {"stck_bsop_date": "20240101", "stck_clpr": "100", "acml_vol": "5"},
        {"stck_bsop_date": "20240103", "stck_clpr": "0"},   # 휴장(제외)
    ]
    rows = parse_daily(out2)
    assert [r["close"] for r in rows] == [100, 200]         # 오래된→최신, 0 제외
    divs = parse_dividend([{"record_date": "20240401", "per_sto_divi_amt": "361"}])
    assert divs[0]["per_share"] == 361
    p = parse_price({"stck_prpr": "70000", "prdy_ctrt": "1.5", "per": "12.3", "pbr": "1.1"})
    assert p["price"] == 70000 and p["per"] == 12.3


# ---------- 브리핑 ----------
def test_compose_brief():
    quotes = [{"name": "삼성전자", "price": 70000, "change_pct": 1.5}]
    value_rows = [{"name": "A", "per": 5, "pbr": 0.8, "roe": 12, "magic_rank": 3}]
    signal_rows = [{"name": "B", "signal": "buy", "rsi": 28, "sma_cross": "golden"}]
    div_rows = [{"name": "C", "yield_pct": 5.0, "next_ex_date": "20240401"}]
    assert has_content(quotes, value_rows, signal_rows, div_rows)
    msg = compose_brief(quotes, value_rows, signal_rows, div_rows)
    assert "주식 브리핑" in msg and "삼성전자" in msg
    assert "🟢매수" in msg and "배당" in msg and "투자 추천이 아닙니다" in msg


def test_compose_brief_shows_regime_when_no_buys():
    # 매수 후보가 없어도 시황 국면·관망 사유가 브리핑에 노출(위험회피장 설명)
    quotes = [{"name": "삼성전자", "price": 70000, "change_pct": -1.5}]
    plan = {"buys": [], "sells": [], "regime": {
        "regime": "risk_off", "label": "위험 회피", "posture": "방어",
        "strategy_labels": ["저변동 방어"], "reasons": ["지수 200일선 아래 -4.0%"]}}
    msg = compose_brief(quotes, [], [], [], plan=plan)
    assert "시황 국면" in msg and "위험 회피" in msg and "저변동 방어" in msg
    assert "매수 후보 없음" in msg


def test_risk_discipline_top_of_brief():
    # 하드 손절선 이탈 보유는 브리핑 최상단 🚨 블록으로(손실 큰 순)
    plan = {"buys": [], "sells": [
        {"code": "001440", "name": "대한전선", "pnl_pct": -55.1, "hard": 3,
         "action": "손절 검토", "reasons": ["딥로스 하드 손절선(-20%) 이탈"]},
        {"code": "042700", "name": "한미반도체", "pnl_pct": -39.5, "hard": 3,
         "action": "손절 검토", "reasons": ["추세 이탈"]},
        {"code": "005930", "name": "삼성전자", "pnl_pct": -3.0, "hard": 0,
         "soft": 1, "action": "정리 검토", "reasons": ["약한 신호"]}]}
    msg = compose_brief([{"name": "A", "price": 100, "change_pct": 1}], [], [], [],
                        plan=plan)
    assert "손절 규율" in msg
    head = msg.split("[🎯")[0]                    # 매매 플랜보다 앞(최상단)
    assert "대한전선" in head and head.index("대한전선") < head.index("한미반도체")  # 손실 큰 순
    assert "삼성전자" not in head.split("손절 규율")[1].split("\n\n")[0]  # 하드 아님 → 제외


def test_briefing_freshness_guard():
    # 재시작 직후 '지난주 시세'로 브리핑 나가는 것 방어: 최신 ts가 오래면 보류
    import time as _t

    from briefing.main import _quotes_fresh
    from shared.redis_keys import STOCK_QUOTE_KEY

    async def _run():
        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        # 지난주 시세만 있음 → stale
        await r.hset(STOCK_QUOTE_KEY, "005930",
                     json.dumps({"price": 70000, "ts": _t.time() - 8 * 86400}))
        assert await _quotes_fresh(r) is False
        # 방금 갱신된 시세 → fresh
        await r.hset(STOCK_QUOTE_KEY, "005930",
                     json.dumps({"price": 71000, "ts": _t.time()}))
        assert await _quotes_fresh(r) is True

    asyncio.run(_run())


def test_has_content_empty():
    assert not has_content([], [{"magic_rank": None}], [], [{"yield_pct": None}])


def test_telegram_keyboard():
    from notifier.telegram import build_keyboard
    kb = build_keyboard([[{"text": "✅", "cb": "cf:1"}, {"text": "❌", "cb": "dr:1"}]])
    assert kb == {"inline_keyboard": [[{"text": "✅", "callback_data": "cf:1"},
                                       {"text": "❌", "callback_data": "dr:1"}]]}
    # URL 버튼 + 빈 스펙
    assert build_keyboard([[{"text": "열기", "url": "http://x"}]]) == {
        "inline_keyboard": [[{"text": "열기", "url": "http://x"}]]}
    assert build_keyboard(None) is None
    assert build_keyboard([[]]) is None      # 빈 행만 → None
