"""DART 공시 파서 + 전체시장 유니버스 파서 + 병합 스크리너 테스트."""
from __future__ import annotations

import asyncio
import json

import fakeredis.aioredis

from api.services.stock_value import load_quotes, value_screener
from collector.news.dart import (
    format_disclosure,
    parse_alot_matter,
    parse_corp_map,
    parse_disclosure_list,
    parse_net_income_growth,
)
from collector.stock.kis import effective_watchlist, normalize_watch_item
from collector.stock.kis_master import parse_mst
from shared.redis_keys import STOCK_MARKET_KEY, STOCK_QUOTE_KEY, WATCHLIST_KEY


# ---------- DART ----------
def test_parse_disclosure_list():
    payload = {"status": "000", "message": "정상", "list": [
        {"rcept_no": "20240101000123", "corp_name": "삼성전자", "stock_code": "005930",
         "report_nm": "주요사항보고서(유상증자결정)", "flr_nm": "삼성전자", "rcept_dt": "20240101"},
        {"rcept_no": "", "corp_name": "무시", "stock_code": "000000"},   # 접수번호 없으면 제외
    ]}
    rows = parse_disclosure_list(payload)
    assert len(rows) == 1
    assert rows[0]["stock_code"] == "005930" and "20240101000123" in rows[0]["url"]
    assert "삼성전자" in format_disclosure(rows[0])


def test_parse_disclosure_error_status():
    assert parse_disclosure_list({"status": "013", "message": "데이터 없음"}) == []
    assert parse_disclosure_list("nope") == []


# ---------- 종목마스터 ----------
def test_parse_mst_fixedwidth():
    # [단축코드 9][표준코드 12][한글명][후행 trailing]
    trailing = 5
    line = "005930   " + "KR7005930003" + "삼성전자" + "XXXXX"
    rows = parse_mst(line, trailing, "KOSPI")
    assert rows and rows[0]["code"] == "005930"
    assert rows[0]["name"] == "삼성전자" and rows[0]["market"] == "KOSPI"
    # 너무 짧은 줄은 무시
    assert parse_mst("short", trailing) == []


def test_parse_corp_map():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <result>
      <list><corp_code>00126380</corp_code><corp_name>삼성전자</corp_name>
        <stock_code>005930</stock_code></list>
      <list><corp_code>00999999</corp_code><corp_name>비상장사</corp_name>
        <stock_code> </stock_code></list>
    </result>""".encode("utf-8")
    m = parse_corp_map(xml)
    assert m == {"005930": "00126380"}   # 비상장(빈 stock_code) 제외


def test_parse_alot_matter_three_years():
    payload = {"status": "000", "list": [
        {"se": "주당 현금배당금(원)", "stock_knd": "보통주",
         "thstrm": "1,444", "frmtrm": "1,444", "lwfr": "2,994"},
        {"se": "주당 현금배당금(원)", "stock_knd": "우선주",
         "thstrm": "1,445", "frmtrm": "1,445", "lwfr": "2,995"},
    ]}
    items = parse_alot_matter(payload, 2025)
    # 보통주 행 우선, 3개년(당기/전기/전전기) + 쉼표 제거
    assert items == [
        {"date": "2025", "per_share": 1444.0},
        {"date": "2024", "per_share": 1444.0},
        {"date": "2023", "per_share": 2994.0},
    ]


def test_parse_alot_matter_split_adjusted():
    # 전전기 액면 5,000 → 당기 500 (10:1 분할): 과거 배당 ÷10 환산
    payload = {"status": "000", "list": [
        {"se": "주당액면가액(원)", "thstrm": "500", "frmtrm": "500", "lwfr": "5,000"},
        {"se": "주당 현금배당금(원)", "stock_knd": "보통주",
         "thstrm": "300", "frmtrm": "280", "lwfr": "2,500"},
    ]}
    items = parse_alot_matter(payload, 2025)
    assert items[0] == {"date": "2025", "per_share": 300.0}   # 당기 그대로
    assert items[1] == {"date": "2024", "per_share": 280.0}
    assert items[2] == {"date": "2023", "per_share": 250.0}   # 2500 × (500/5000)


def test_compute_dividend_split_suspect():
    from api.services.stock_dividend import compute_dividend
    # 보고서 이후 분할 미반영 → 수익률 40% 같은 비정상치는 숨김
    q = {"code": "X", "name": "분할주", "price": 5000}
    out = compute_dividend(q, [{"date": "2025", "per_share": 2000}])   # 40%
    assert out["split_suspect"] is True and out["yield_pct"] is None
    ok = compute_dividend(q, [{"date": "2025", "per_share": 350}])     # 7%
    assert ok["split_suspect"] is False and ok["yield_pct"] == 7.0


def test_parse_alot_matter_error_or_empty():
    assert parse_alot_matter({"status": "013"}, 2025) == []            # 데이터 없음
    assert parse_alot_matter({"status": "000", "list": []}, 2025) == []
    # 무배당('-') 연도는 제외
    payload = {"status": "000", "list": [
        {"se": "주당 현금배당금(원)", "thstrm": "-", "frmtrm": "100", "lwfr": "-"}]}
    assert parse_alot_matter(payload, 2025) == [{"date": "2024", "per_share": 100.0}]


def test_quarter_candidates():
    import datetime as dt

    from collector.news.dart import quarter_candidates
    # 2026년 7월 → 2026.1Q가 최신(5월 공시 완료), 폴백은 전년 3Q
    c = quarter_candidates(dt.date(2026, 7, 4))
    assert c[0] == ("11013", 2026, "2026.1Q")
    # 12월 → 당해 3Q
    assert quarter_candidates(dt.date(2026, 12, 1))[0] == ("11014", 2026, "2026.3Q")
    # 3월 → 아직 당해 1Q 미공시 → 전년 3Q
    assert quarter_candidates(dt.date(2026, 3, 1))[0] == ("11014", 2025, "2025.3Q")


def test_parse_net_income_growth():
    payload = {"status": "000", "list": [
        {"account_nm": "당기순이익", "fs_div": "OFS",
         "thstrm_amount": "10,000", "frmtrm_amount": "20,000"},   # 별도(-50%)
        {"account_nm": "당기순이익", "fs_div": "CFS",
         "thstrm_amount": "54,000,000,000,000", "frmtrm_amount": "30,000,000,000,000"},
    ]}
    assert parse_net_income_growth(payload) == 80.0   # 연결(CFS) 우선: +80%
    assert parse_net_income_growth({"status": "013"}) is None
    # 전기 0/누락이면 계산 불가
    z = {"status": "000", "list": [{"account_nm": "당기순이익", "fs_div": "CFS",
                                    "thstrm_amount": "100", "frmtrm_amount": "-"}]}
    assert parse_net_income_growth(z) is None


def test_normalize_watch_item():
    assert normalize_watch_item("005930", "삼성전자") == {"code": "005930", "name": "삼성전자"}
    assert normalize_watch_item(" 000660 ")["code"] == "000660"
    assert normalize_watch_item("12345") is None      # 5자리
    assert normalize_watch_item("") is None
    # 미장: 미국 티커 허용(대문자 통일, 점 표기 허용)
    assert normalize_watch_item("nvda")["code"] == "NVDA"
    assert normalize_watch_item("AAPL", "애플") == {"code": "AAPL", "name": "애플"}
    assert normalize_watch_item("brk.b")["code"] == "BRK.B"
    assert normalize_watch_item("NVDA123") is None    # 혼합 형식 거부


def test_effective_watchlist_redis_override_and_fallback():
    async def run():
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        # Redis 미설정 → yaml 폴백(비어있지 않음)
        base = await effective_watchlist(redis)
        assert isinstance(base, list) and base
        # Redis 오버라이드 → 그 목록 반환
        await redis.set(WATCHLIST_KEY, json.dumps([{"code": "005930", "name": "삼성전자"}]))
        ov = await effective_watchlist(redis)
        assert ov == [{"code": "005930", "name": "삼성전자"}]
        # 빈 리스트도 존중(전부 삭제)
        await redis.set(WATCHLIST_KEY, json.dumps([]))
        assert await effective_watchlist(redis) == []
        await redis.aclose()
    asyncio.run(run())


def test_parse_mst_filters_non_stock_codes():
    # ELW·파생 등 9자리 영숫자 코드(F74701B9A)는 제외, 6자리 숫자만 유지.
    trailing = 5
    stock = "005930   " + "KR7005930003" + "삼성전자" + "XXXXX"
    elw = "F74701B9A" + "KRXELW000001" + "엘더블유" + "XXXXX"
    rows = parse_mst(stock + "\n" + elw, trailing, "KOSPI")
    assert [r["code"] for r in rows] == ["005930"]


# ---------- 병합 스크리너(전체시장 ∪ 관심) ----------
def test_load_quotes_merge_and_value():
    async def run():
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await redis.hset(STOCK_QUOTE_KEY, "005930",
                         json.dumps({"code": "005930", "name": "삼성전자", "price": 70000,
                                     "per": 12, "pbr": 1.1, "eps": 5800, "bps": 60000}))
        await redis.hset(STOCK_MARKET_KEY, "000660",
                         json.dumps({"code": "000660", "name": "SK하이닉스", "price": 180000,
                                     "per": 6, "pbr": 1.4, "eps": 30000, "bps": 128000}))
        q = await load_quotes(redis)
        assert {x["code"] for x in q} == {"005930", "000660"}
        d = await value_screener(redis, limit=1)
        assert d["total"] == 2 and len(d["rows"]) == 1
        await redis.aclose()

    asyncio.run(run())


def test_load_us_universe():
    from collector.stock.us_master import load_us_universe

    us = load_us_universe()
    assert len(us) >= 80                      # 주요 종목 커버
    codes = {u["code"] for u in us}
    assert {"NVDA", "AAPL", "TSLA", "MSFT"} <= codes
    assert all(u["market"] == "US" for u in us)
    assert all(u["code"] == u["code"].upper() for u in us)
    nvda = next(u for u in us if u["code"] == "NVDA")
    assert nvda["name"] == "엔비디아"


def test_find_earnings_flash():
    from collector.news.dart import find_earnings_flash

    filings = [
        {"stock_code": "005930", "corp_name": "삼성전자",
         "report_nm": "연결재무제표기준영업(잠정)실적(공정공시)",
         "rcept_dt": "20260708", "url": "https://dart.example/1"},
        {"stock_code": "005930", "corp_name": "삼성전자",
         "report_nm": "주요사항보고서", "rcept_dt": "20260707", "url": "u2"},
        {"stock_code": "000660", "corp_name": "SK하이닉스",
         "report_nm": "기타경영사항", "rcept_dt": "20260707", "url": "u3"},
    ]
    f = find_earnings_flash(filings, "005930")
    assert f and "잠정" in f["title"] and f["date"] == "20260708"
    assert find_earnings_flash(filings, "000660") is None   # 잠정실적 아님
    assert find_earnings_flash([], "005930") is None


def test_quarter_candidates_august_halfyear():
    import datetime as dt

    from collector.news.dart import quarter_candidates
    # 8월: 반기보고서(2Q) 마감 달 — 뜨는 즉시 반영(폴백은 1Q)
    c = quarter_candidates(dt.date(2026, 8, 5))
    assert c[0] == ("11012", 2026, "2026.2Q") and c[1][2] == "2026.1Q"
    # 11월: 3Q 마감 달
    assert quarter_candidates(dt.date(2026, 11, 20))[0][2] == "2026.3Q"
    # 5월: 1Q 마감 달
    assert quarter_candidates(dt.date(2026, 5, 20))[0][2] == "2026.1Q"
