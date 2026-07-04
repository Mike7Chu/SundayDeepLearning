"""텔레그램 발송 (Bot API 직접 호출, 추가 의존성 없음)."""
from __future__ import annotations

import logging

import httpx

from shared.settings import settings

logger = logging.getLogger(__name__)


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
