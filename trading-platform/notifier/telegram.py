"""텔레그램 발송 (Bot API 직접 호출, 추가 의존성 없음)."""
from __future__ import annotations

import asyncio
import logging
import re

import httpx

from shared.settings import settings

logger = logging.getLogger(__name__)

# 텔레그램 sendMessage 한도는 4096자 — 헤더/이모지 여유를 두고 분할
_CHUNK = 3500


def esc(s) -> str:
    """HTML 파스모드용 이스케이프(& < > 만 — 텔레그램 HTML 규격). None→''."""
    return (str(s) if s is not None else "").replace("&", "&amp;") \
        .replace("<", "&lt;").replace(">", "&gt;")


# ── 마크다운 → 텔레그램 HTML(부분집합) ───────────────────────────────
# 텔레그램 HTML은 <b><i><u><s><code><pre><a><blockquote>만 지원 — 표·헤딩은
# 없다. AI 리포트(리서치·스토리·코치)가 마크다운으로 오면 평문으론 지저분하니
# 지원 태그로 변환하고, 표는 '키: 값'/'· 구분' 줄로, 헤딩은 굵게 풀어낸다.
_MD_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_MD_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_MD_CODE = re.compile(r"`([^`]+)`")
_HR = re.compile(r"^\s*[-*_]{3,}\s*$")
_HEAD = re.compile(r"^\s*(#{1,6})\s+(.*\S)\s*$")
_BULLET = re.compile(r"^\s*[-*·•]\s+(.*)$")
_SEPCELL = re.compile(r"^:?-{2,}:?$")
_SECTION = re.compile(r"^\[[^\]]{1,40}\]$")     # 브리핑 등 '[제목]' 한 줄 → 굵게


def _inline_md(escaped: str) -> str:
    """이스케이프된 문자열에 인라인 마크다운(굵게·코드·링크)만 태그로 치환."""
    escaped = _MD_LINK.sub(
        lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', escaped)
    escaped = _MD_BOLD.sub(lambda m: f"<b>{m.group(1) or m.group(2)}</b>", escaped)
    escaped = _MD_CODE.sub(r"<code>\1</code>", escaped)
    return escaped


def _tg_table(block: list[str]) -> list[str]:
    """마크다운 표 블록 → 읽기 좋은 줄들. 2열이면 '키: 값', 그 이상은 '· 구분'."""
    rows: list[list[str]] = []
    for ln in block:
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if cells and all(c == "" or _SEPCELL.match(c) for c in cells):
            continue                                  # 구분선(---|---) 행은 건너뜀
        rows.append(cells)
    if not rows:
        return []
    header, body = rows[0], rows[1:]
    ncol = max(len(r) for r in rows)
    out = ["<b>" + " · ".join(_inline_md(esc(c)) for c in header) + "</b>"]
    for r in body:
        if ncol == 2:                                 # 2열 표 → '· 키: 값'
            k = _inline_md(esc(r[0])) if r else ""
            v = _inline_md(esc(r[1])) if len(r) > 1 else ""
            out.append(f"• <b>{k}</b>: {v}")
        else:
            out.append("· " + " · ".join(_inline_md(esc(c)) for c in r))
    return out


def md_to_tg(text: str) -> str:
    """마크다운 텍스트 → 텔레그램 HTML(parse_mode=HTML). 줄 경계 기준(분할 안전).

    헤딩→굵게, 표→키:값/구분 줄, 불릿→•, 수평선→─, **굵게**/`코드`/[링크](url)
    변환. 결과는 이미 이스케이프됨 — send(html=True)로 그대로 발송.
    """
    lines = (text or "").split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        raw = lines[i]
        if raw.lstrip().startswith("|") and "|" in raw.strip()[1:]:
            block = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                block.append(lines[i]); i += 1
            out.extend(_tg_table(block))
            continue
        i += 1
        stripped = raw.strip()
        if not stripped:
            out.append("")
        elif _HR.match(raw):
            out.append("──────────")
        elif _HEAD.match(raw):
            out.append("<b>" + _inline_md(esc(_HEAD.match(raw).group(2))) + "</b>")
        elif _SECTION.match(stripped):
            out.append("<b>" + esc(stripped) + "</b>")
        elif stripped.startswith(">"):
            out.append("<blockquote>"
                       + _inline_md(esc(stripped[1:].strip())) + "</blockquote>")
        elif _BULLET.match(raw):
            out.append("• " + _inline_md(esc(_BULLET.match(raw).group(1))))
        else:
            out.append(_inline_md(esc(raw.rstrip())))
    return "\n".join(out)


def dashboard_buttons(path: str = "", label: str = "📊 대시보드 열기"):
    """대시보드 '열기' 버튼(DASHBOARD_URL 설정 시). 없으면 None — 어디서나 재사용."""
    u = settings.dashboard_url
    if not u:
        return None
    return [[{"text": label, "url": u.rstrip("/") + path}]]


def stock_buttons(items, per_row: int = 2):
    """종목별 '🔎 상세' URL 버튼(대시보드가 ?stock=코드로 해당 종목 모달을 연다).

    items = [(code, name), ...]. DASHBOARD_URL 없으면 None. 최대 6개.
    """
    u = settings.dashboard_url
    if not u or not items:
        return None
    base = u.rstrip("/")
    flat = [{"text": f"🔎 {(name or code)[:12]}",
             "url": f"{base}/?stock={code}"} for code, name in items[:6]]
    return [flat[i:i + per_row] for i in range(0, len(flat), per_row)]


def reply_keyboard_markup(rows) -> dict:
    """상시 빠른명령 키보드(ReplyKeyboardMarkup). rows=[["잔고","플랜"],...]."""
    return {"keyboard": [[{"text": t} for t in row] for row in rows],
            "resize_keyboard": True, "is_persistent": True}


# 채팅 하단 상시 빠른명령 버튼(탭하면 해당 명령 텍스트 전송 → 기존 핸들러 처리)
QUICK_KEYS = [["💼 잔고", "🎯 플랜", "🛡️ 상태"], ["🚀 후보", "🧭 점검", "❓ 도움말"]]


def build_keyboard(buttons) -> dict | None:
    """버튼 스펙 → 텔레그램 inline_keyboard(순수 함수).

    buttons = [[{"text":..,"url":..} | {"text":..,"cb":콜백데이터}], ...](행의 리스트).
    URL 버튼은 한 번 눌러 열기, cb 버튼은 콜백(주문 확인/취소 등). 빈 스펙이면 None.
    """
    if not buttons:
        return None
    rows = []
    for row in buttons:
        r = []
        for b in (row or []):
            if b.get("url"):
                r.append({"text": b["text"], "url": b["url"]})
            elif b.get("cb"):
                r.append({"text": b["text"], "callback_data": b["cb"]})
        if r:
            rows.append(r)
    return {"inline_keyboard": rows} if rows else None


def split_message(text: str, limit: int = _CHUNK) -> list[str]:
    """긴 텍스트를 줄 경계 우선으로 limit 이하 조각들로 분할(순수 함수).

    한 줄이 limit을 넘으면 그 줄만 강제 분할. 내용은 잘리지 않고 전부 보존.
    """
    text = (text or "").strip()
    if not text:
        return []
    parts: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit + 1)
        if cut < limit // 2:      # 줄바꿈이 없거나 너무 앞 → 강제 분할
            cut = limit
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip("\n")
    if text:
        parts.append(text)
    return parts


class TelegramSender:
    def __init__(self, token: str = "", chat_id: str = ""):
        self.token = token or settings.telegram_bot_token
        self.chat_id = chat_id or settings.telegram_chat_id

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    async def send(self, text: str, *, buttons=None, reply_keyboard=None,
                   silent: bool = False, html: bool = False) -> bool:
        """메시지 발송. buttons(인라인)·reply_keyboard(상시 키보드)·html·silent 선택.

        링크 미리보기는 항상 끔. html=True면 parse_mode=HTML(호출부가 값 이스케이프 필수).
        buttons가 있으면 인라인, 없고 reply_keyboard면 상시 키보드를 붙인다.
        """
        if not self.enabled:
            logger.warning("텔레그램 미설정(TELEGRAM_BOT_TOKEN/CHAT_ID) → 로그만: %s",
                           text.replace("\n", " | "))
            return False
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": text,
                   "disable_web_page_preview": True}
        if html:
            payload["parse_mode"] = "HTML"
        if silent:
            payload["disable_notification"] = True
        kb = build_keyboard(buttons)
        if kb:
            payload["reply_markup"] = kb
        elif reply_keyboard:
            payload["reply_markup"] = reply_keyboard_markup(reply_keyboard)
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
            return True
        except Exception as exc:
            logger.error("텔레그램 발송 실패: %s", exc)
            return False

    async def answer_callback(self, callback_id: str, text: str = "") -> None:
        """콜백 버튼 로딩 스피너 종료(눌린 뒤 반드시 호출). 실패는 무시."""
        if not self.enabled:
            return
        url = f"https://api.telegram.org/bot{self.token}/answerCallbackQuery"
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                await client.post(url, json={"callback_query_id": callback_id,
                                             "text": text})
        except Exception:
            pass

    async def clear_buttons(self, message_id: int) -> None:
        """이미 처리된 메시지의 버튼 제거(중복 클릭 방지). 실패는 무시."""
        if not self.enabled or not message_id:
            return
        url = f"https://api.telegram.org/bot{self.token}/editMessageReplyMarkup"
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                await client.post(url, json={"chat_id": self.chat_id,
                                             "message_id": message_id,
                                             "reply_markup": {"inline_keyboard": []}})
        except Exception:
            pass

    async def send_long(self, text: str, limit: int = _CHUNK, *,
                        buttons=None, md: bool = False) -> bool:
        """4096자 한도를 넘는 리포트를 잘리지 않게 여러 메시지로 나눠 발송.

        2개 이상으로 나뉘면 (i/n) 머리표를 붙이고, 연속 발송 레이트리밋을
        피하려 조각 사이 잠깐 대기. buttons는 마지막 조각에만 붙인다.
        md=True면 각 조각을 마크다운→텔레그램 HTML로 변환해 예쁘게 렌더한다
        (분할은 원문 줄 경계 기준이라 태그가 조각을 가로지르지 않음).
        """
        parts = split_message(text, limit)
        if not parts:
            return False
        n = len(parts)
        ok = True
        for i, p in enumerate(parts, 1):
            head = f"({i}/{n})\n" if n > 1 else ""
            body = md_to_tg(p) if md else p
            ok = await self.send(head + body, buttons=buttons if i == n else None,
                                 html=md) and ok
            if i < n:
                await asyncio.sleep(0.5)
        return ok
