"""뉴스 크롤링(구글 뉴스 RSS) + 해외뉴스 한국어 번역 — 무키·토큰 0.

DART 공시 외에 종목별 일반 뉴스를 수집. 국내=회사명, 미국=티커. 해외(영문)
헤드라인은 구글 번역(무키 엔드포인트) 한국어로 병기하고 실패 시 원문 폴백.
파서는 순수 함수(테스트 용이), 네트워크는 방어적(타임아웃·예외 흡수). 온디맨드+캐시.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from urllib.parse import quote_plus

import httpx

_UA = {"User-Agent": "Mozilla/5.0 (compatible; StockLab/1.0)"}


def google_news_url(query: str, kr: bool = True) -> str:
    """구글 뉴스 RSS 검색 URL. kr=True면 한국어/한국판, False면 영어/미국판."""
    q = quote_plus(query)
    if kr:
        return f"https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"
    return f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"


def parse_news_rss(xml_text: str, cap: int = 10) -> list[dict]:
    """구글 뉴스 RSS(XML) → [{title, source, url, published}] (순수 함수).

    구글 뉴스 title은 보통 '헤드라인 - 매체명' 형태 → 매체명을 분리한다.
    """
    rows: list[dict] = []
    try:
        root = ET.fromstring(xml_text or "")
    except ET.ParseError:
        return rows
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        src_el = item.find("source")
        source = (src_el.text or "").strip() if src_el is not None else ""
        headline = title
        if source and title.endswith(" - " + source):
            headline = title[: -(len(source) + 3)].strip()
        elif not source and " - " in title:
            headline, _, source = title.rpartition(" - ")
            headline, source = headline.strip(), source.strip()
        if headline:
            rows.append({"title": headline, "source": source,
                         "url": link, "published": pub})
        if len(rows) >= cap:
            break
    return rows


async def fetch_news(query: str, kr: bool = True, cap: int = 10,
                     timeout: float = 10.0) -> list[dict]:
    """구글 뉴스 RSS 조회 → 파싱. 실패(네트워크·차단)면 빈 리스트."""
    try:
        async with httpx.AsyncClient(timeout=timeout, headers=_UA,
                                     follow_redirects=True) as c:
            r = await c.get(google_news_url(query, kr))
            r.raise_for_status()
        return parse_news_rss(r.text, cap)
    except Exception:
        return []


async def translate_ko(text: str, timeout: float = 8.0) -> str | None:
    """영문 등 → 한국어(구글 번역 무키 엔드포인트). 실패 시 None(호출부가 원문 폴백)."""
    if not text or not text.strip():
        return None
    try:
        async with httpx.AsyncClient(timeout=timeout, headers=_UA) as c:
            r = await c.get(
                "https://translate.googleapis.com/translate_a/single",
                params={"client": "gtx", "sl": "auto", "tl": "ko", "dt": "t", "q": text})
            r.raise_for_status()
            data = r.json()
        segs = data[0] if isinstance(data, list) and data else []
        out = "".join(s[0] for s in segs if s and s[0])
        return out.strip() or None
    except Exception:
        return None


async def crawl_stock_news(query: str, kr: bool = True, cap: int = 8) -> list[dict]:
    """종목 뉴스 수집 + 해외면 한국어 번역 병기. 반환 각 항목에 title_ko(있으면)."""
    rows = await fetch_news(query, kr=kr, cap=cap)
    if not kr:                       # 미국·해외 = 영문 → 한국어 번역 병기
        for row in rows:
            ko = await translate_ko(row.get("title", ""))
            row["title_ko"] = ko
            row["lang"] = "en"
    else:
        for row in rows:
            row["lang"] = "ko"
    return rows
