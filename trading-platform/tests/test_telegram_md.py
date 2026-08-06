"""notifier.telegram.md_to_tg — 마크다운 → 텔레그램 HTML(부분집합) 변환 검증."""
from notifier.telegram import md_to_tg, split_message


def test_escape_first():
    # <,&,> 는 항상 먼저 이스케이프(HTML 인젝션 방지)
    assert md_to_tg("a < b & c > d") == "a &lt; b &amp; c &gt; d"


def test_heading_to_bold():
    assert md_to_tg("## 요약") == "<b>요약</b>"
    assert md_to_tg("# 제목 **강조**") == "<b>제목 <b>강조</b></b>"


def test_inline():
    assert md_to_tg("**굵게**") == "<b>굵게</b>"
    assert md_to_tg("__굵게__") == "<b>굵게</b>"
    assert md_to_tg("`코드`") == "<code>코드</code>"
    assert md_to_tg("[네이버](https://naver.com)") == \
        '<a href="https://naver.com">네이버</a>'


def test_bullet_and_hr_and_section():
    assert md_to_tg("- 항목1") == "• 항목1"
    assert md_to_tg("* 항목2") == "• 항목2"
    assert md_to_tg("---") == "──────────"
    assert md_to_tg("[시장 온도]") == "<b>[시장 온도]</b>"


def test_two_col_table_to_kv():
    t = "| 항목 | 값 |\n|---|---|\n| PER | 12.3 |\n| ROE | 15% |"
    assert md_to_tg(t) == "<b>항목 · 값</b>\n• <b>PER</b>: 12.3\n• <b>ROE</b>: 15%"


def test_multi_col_table():
    t = "| A | B | C |\n|---|---|---|\n| 1 | 2 | 3 |"
    assert md_to_tg(t) == "<b>A · B · C</b>\n· 1 · 2 · 3"


def test_table_cell_escaped():
    out = md_to_tg("| x | <script> |\n|---|---|\n| a | b |")
    assert "&lt;script&gt;" in out and "<script>" not in out


def test_blockquote_and_empty():
    assert md_to_tg("> 인용문") == "<blockquote>인용문</blockquote>"
    assert md_to_tg("") == ""


def test_split_keeps_tags_within_lines():
    # 분할은 줄 경계 기준 → 각 조각을 변환해도 태그가 조각을 가로지르지 않는다.
    text = "\n".join(f"**줄{i}** 내용 `code{i}`" for i in range(400))
    parts = split_message(text, 200)
    assert len(parts) > 1
    for p in parts:
        html = md_to_tg(p)
        # 열고 닫힘 개수 일치(태그 잘림 없음)
        assert html.count("<b>") == html.count("</b>")
        assert html.count("<code>") == html.count("</code>")
