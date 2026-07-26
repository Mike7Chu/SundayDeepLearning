"""텔레그램 발송 (Bot API 직접 호출, 추가 의존성 없음)."""
from __future__ import annotations

import asyncio
import logging

import httpx

from shared.settings import settings

logger = logging.getLogger(__name__)

# 텔레그램 sendMessage 한도는 4096자 — 헤더/이모지 여유를 두고 분할
_CHUNK = 3500


def dashboard_buttons(path: str = "", label: str = "📊 대시보드 열기"):
    """대시보드 '열기' 버튼(DASHBOARD_URL 설정 시). 없으면 None — 어디서나 재사용."""
    u = settings.dashboard_url
    if not u:
        return None
    return [[{"text": label, "url": u.rstrip("/") + path}]]


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

    async def send(self, text: str, *, buttons=None, silent: bool = False) -> bool:
        """메시지 발송. buttons(인라인 키보드)·silent(무음 알림) 선택.

        링크 미리보기는 항상 끔(브리핑·알림에 뜨던 큰 미리보기 카드 제거).
        """
        if not self.enabled:
            logger.warning("텔레그램 미설정(TELEGRAM_BOT_TOKEN/CHAT_ID) → 로그만: %s",
                           text.replace("\n", " | "))
            return False
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": text,
                   "disable_web_page_preview": True}
        if silent:
            payload["disable_notification"] = True
        kb = build_keyboard(buttons)
        if kb:
            payload["reply_markup"] = kb
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
                        buttons=None) -> bool:
        """4096자 한도를 넘는 리포트를 잘리지 않게 여러 메시지로 나눠 발송.

        2개 이상으로 나뉘면 (i/n) 머리표를 붙이고, 연속 발송 레이트리밋을
        피하려 조각 사이 잠깐 대기. buttons는 마지막 조각에만 붙인다.
        """
        parts = split_message(text, limit)
        if not parts:
            return False
        n = len(parts)
        ok = True
        for i, p in enumerate(parts, 1):
            head = f"({i}/{n})\n" if n > 1 else ""
            ok = await self.send(head + p, buttons=buttons if i == n else None) and ok
            if i < n:
                await asyncio.sleep(0.5)
        return ok
