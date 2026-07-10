"""텔레그램 긴 메시지 분할(split_message) 테스트 — 잘림 없이 전부 보존."""
from __future__ import annotations

from notifier.telegram import split_message


def test_short_message_single_part():
    assert split_message("짧은 리포트") == ["짧은 리포트"]
    assert split_message("") == []
    assert split_message("   \n  ") == []


def test_long_message_splits_at_newline():
    lines = [f"{i}번째 분석 줄 — 근거와 판단" for i in range(400)]
    text = "\n".join(lines)
    parts = split_message(text, limit=1000)
    assert len(parts) > 1
    assert all(len(p) <= 1000 for p in parts)
    # 줄 경계 분할 — 내용이 하나도 안 잘리고 전부 보존
    assert "\n".join(parts).split("\n") == lines


def test_giant_single_line_hard_split():
    text = "가" * 5000   # 줄바꿈 없는 초장문
    parts = split_message(text, limit=1000)
    assert all(len(p) <= 1000 for p in parts)
    assert "".join(parts) == text


def test_exact_limit_no_split():
    text = "a" * 1000
    assert split_message(text, limit=1000) == [text]
