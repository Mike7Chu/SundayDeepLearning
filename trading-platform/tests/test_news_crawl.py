"""뉴스 크롤링 RSS 파서·URL 빌더 — 순수 함수 테스트(네트워크 없음)."""
from collector.news.crawl import google_news_url, parse_news_rss

_SAMPLE = """<rss><channel>
<item><title>Samsung posts record profit - Reuters</title>
  <link>http://x/1</link><pubDate>Mon, 25 Aug 2026 21:07:00 GMT</pubDate>
  <source url="http://reuters.com">Reuters</source></item>
<item><title>Nvidia earnings beat - Bloomberg</title>
  <link>http://x/2</link><pubDate>Tue, 26 Aug 2026 01:00:00 GMT</pubDate>
  <source url="http://bloomberg.com">Bloomberg</source></item>
</channel></rss>"""


def test_parse_rss_splits_source():
    rows = parse_news_rss(_SAMPLE)
    assert len(rows) == 2
    assert rows[0] == {"title": "Samsung posts record profit", "source": "Reuters",
                       "url": "http://x/1", "published": "Mon, 25 Aug 2026 21:07:00 GMT"}
    assert rows[1]["source"] == "Bloomberg" and rows[1]["title"] == "Nvidia earnings beat"


def test_parse_rss_no_source_tag():
    xml = ("<rss><channel><item><title>헤드라인만 있음 - 한국경제</title>"
           "<link>http://y</link></item></channel></rss>")
    rows = parse_news_rss(xml)
    assert rows[0]["title"] == "헤드라인만 있음" and rows[0]["source"] == "한국경제"


def test_parse_rss_cap_and_bad_xml():
    big = "<rss><channel>" + "<item><title>a - b</title><link>u</link></item>" * 30 + "</channel></rss>"
    assert len(parse_news_rss(big, cap=5)) == 5
    assert parse_news_rss("not xml at all") == []
    assert parse_news_rss("") == []


def test_google_news_url():
    assert "news.google.com/rss/search" in google_news_url("삼성전자", True)
    assert "hl=ko&gl=KR" in google_news_url("삼성전자", True)
    assert "hl=en-US&gl=US" in google_news_url("NVDA stock", False)
