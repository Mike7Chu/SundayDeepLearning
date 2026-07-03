"""DART 공시 파서 + 전체시장 유니버스 파서 + 병합 스크리너 테스트."""
from __future__ import annotations

import asyncio
import json

import fakeredis.aioredis

from api.services.stock_value import load_quotes, value_screener
from collector.news.dart import format_disclosure, parse_disclosure_list
from collector.stock.kis_master import parse_mst
from shared.redis_keys import STOCK_MARKET_KEY, STOCK_QUOTE_KEY


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
