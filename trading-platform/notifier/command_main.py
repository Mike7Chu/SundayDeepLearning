"""텔레그램 명령 리스너 — getUpdates 롱폴링으로 봇/알림 제어.

소유자 chat_id에서 온 메시지만 처리(보안). 토큰/chat_id 없으면 비활성(로그).
실행: python -m notifier.command_main
"""
from __future__ import annotations

import asyncio
import logging

import httpx
import redis.asyncio as aioredis

from briefing.main import run_once as briefing_once
from notifier.commands import handle
from notifier.telegram import TelegramSender
from shared.settings import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
logger = logging.getLogger("commander")


async def run() -> None:
    token, chat_id = settings.telegram_bot_token, settings.telegram_chat_id
    if not token or not chat_id:
        logger.info("텔레그램 미설정 → 명령 리스너 비활성 (.env TELEGRAM_*)")
        return
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    sender = TelegramSender()
    base = f"https://api.telegram.org/bot{token}"
    offset = 0

    async def brief_fn() -> bool:
        return await briefing_once(redis, sender)

    logger.info("command listener start (chat_id=%s)", chat_id)
    try:
        async with httpx.AsyncClient(timeout=40) as client:
            while True:
                try:
                    r = await client.get(f"{base}/getUpdates",
                                         params={"offset": offset, "timeout": 30})
                    r.raise_for_status()
                    for upd in r.json().get("result", []):
                        offset = upd["update_id"] + 1
                        msg = upd.get("message") or upd.get("edited_message") or {}
                        text = msg.get("text", "")
                        frm = str(msg.get("chat", {}).get("id", ""))
                        if not text:
                            continue
                        if frm != str(chat_id):       # 소유자만
                            logger.warning("무시(타 chat=%s)", frm)
                            continue
                        reply = await handle(redis, text, brief_fn=brief_fn)
                        await sender.send(reply)
                except httpx.HTTPError as exc:
                    logger.warning("getUpdates 실패: %s", exc)
                    await asyncio.sleep(5)
    finally:
        await redis.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("command listener stopped")
