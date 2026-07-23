"""미장 유니버스 — KIS 거래소 코드 resolver·오버라이드 파싱 테스트(순수)."""
from __future__ import annotations

from collector.stock.us_master import kis_exchange, parse_exchange_override


def test_kis_exchange_defaults():
    assert kis_exchange("NVDA") == "NASD"       # 나스닥 기본
    assert kis_exchange("AAPL") == "NASD"
    assert kis_exchange("JPM") == "NYSE"        # 뉴욕
    assert kis_exchange("KO") == "NYSE"
    assert kis_exchange("SPY") == "AMEX"        # NYSE Arca ETF
    assert kis_exchange("JEPI") == "AMEX"
    assert kis_exchange("nvda") == "NASD"       # 소문자도 처리
    assert kis_exchange("UNKNOWN_XYZ") == "NASD"  # 미상은 기본


def test_kis_exchange_override():
    ov = parse_exchange_override("PLTR:NASD, SNOW:NYSE ,BADFMT")
    assert ov == {"PLTR": "NASD", "SNOW": "NYSE"}
    # 오버라이드가 기본 매핑보다 우선
    assert kis_exchange("SNOW", ov) == "NYSE"
    assert kis_exchange("JPM", {"JPM": "NASD"}) == "NASD"   # 강제 교정
    assert parse_exchange_override("") == {}
