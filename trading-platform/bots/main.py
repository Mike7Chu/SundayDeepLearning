"""봇 엔트리포인트 — 활성화된 봇을 페이퍼 모드로 구동.

실행: python -m bots.main
각 봇은 Redis 플래그(bot:enabled:{name})로 on/off, BOT_KILLSWITCH_KEY로 전체 정지.
"""
from __future__ import annotations

import asyncio
import logging

import redis.asyncio as aioredis

from bots.coin.hyeonseon import HyeonseonPaperBot
from bots.coin.loan import LoanPaperBot
from bots.coin.margin import MarginPaperBot
from bots.coin.sell import SellPaperBot
from shared.settings import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
logger = logging.getLogger("bots")


async def main() -> None:
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    bots = [HyeonseonPaperBot(redis), MarginPaperBot(redis),
            LoanPaperBot(redis), SellPaperBot(redis)]
    logger.info("bots start (paper): %s", [b.name for b in bots])
    try:
        await asyncio.gather(*[b.run_forever() for b in bots])
    finally:
        await redis.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("bots stopped")
