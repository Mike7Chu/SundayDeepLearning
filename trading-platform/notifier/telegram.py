"""텔레그램 발송 (Bot API 직접 호출, 추가 의존성 없음)."""
from __future__ import annotations

import asyncio
import logging

import httpx

from shared.settings import settings

logger = logging.getLogger(__name__)

# 텔레그램 sendMessage 한도는 4096자 — 헤더/이모지 여유를 두고 분할
_CHUNK = 3500


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

    async def send(self, text: str) -> bool:
        if not self.enabled:
            logger.warning("텔레그램 미설정(TELEGRAM_BOT_TOKEN/CHAT_ID) → 로그만: %s",
                           text.replace("\n", " | "))
            return False
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    url, json={"chat_id": self.chat_id, "text": text}
                )
                resp.raise_for_status()
            return True
        except Exception as exc:
            logger.error("텔레그램 발송 실패: %s", exc)
            return False

    async def send_long(self, text: str, limit: int = _CHUNK) -> bool:
        """4096자 한도를 넘는 리포트를 잘리지 않게 여러 메시지로 나눠 발송.

        2개 이상으로 나뉘면 (i/n) 머리표를 붙이고, 연속 발송 레이트리밋을
        피하려 조각 사이 잠깐 대기.
        """
        parts = split_message(text, limit)
        if not parts:
            return False
        n = len(parts)
        ok = True
        for i, p in enumerate(parts, 1):
            head = f"({i}/{n})\n" if n > 1 else ""
            ok = await self.send(head + p) and ok
            if i < n:
                await asyncio.sleep(0.5)
        return ok
