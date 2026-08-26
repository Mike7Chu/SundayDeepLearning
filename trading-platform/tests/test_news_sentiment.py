"""뉴스·공시 제목 규칙기반 감성 점수 — 순수 함수 테스트(토큰 0)."""
from api.services.news_sentiment import news_sentiment


def test_positive_dominant():
    r = news_sentiment(["삼성전자 자사주 소각 결정", "역대 최대 실적 발표", "대규모 수주 계약"])
    assert r and r["pos"] >= 3 and r["neg"] == 0
    assert r["score"] > 0 and r["label"] == "호재 우세"


def test_negative_dominant():
    r = news_sentiment(["유상증자 결정", "적자전환 공시", "거래정지 안내"])
    assert r and r["neg"] >= 3 and r["pos"] == 0
    assert r["score"] < 0 and r["label"] == "악재 우세"


def test_neutral_and_empty():
    r = news_sentiment(["분기보고서 제출", "정기주주총회 소집"])   # 키워드 히트 없음
    assert r and r["pos"] == 0 and r["neg"] == 0 and r["score"] == 0.0
    assert r["label"] == "중립·혼조"
    assert news_sentiment([]) is None
    assert news_sentiment(None) is None


def test_samples_and_cap():
    r = news_sentiment(["수주 계약"] * 50)                      # cap 초과
    assert r["n"] <= 25 and len(r["samples"]) <= 5
