"""공지알림봇(신규상장 감지) 엔트리포인트.

업비트/빗썸 등 감시 거래소의 거래가능 마켓을 주기적으로 비교해
신규 심볼 등장 시 텔레그램으로 알림.

실행: python -m notifier.announce_main
"""
from __future__ import annotations

import asyncio
import logging

import redis.asyncio as aioredis

from notifier.config import load_announce_config
from notifier.listings import (
    MarketLister,
    detect_new_listings,
    format_listing,
    parse_symbol,
)
from notifier.telegram import TelegramSender
from shared.settings import settings
from shared.universe import load_universe

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
logger = logging.getLogger("announcer")


def _passes_quote_filter(symbol: str, quote_filter: list[str]) -> bool:
    if not quote_filter:
        return True
    _, quote = parse_symbol(symbol)
    return quote in quote_filter


async def run() -> None:
    cfg = load_announce_config()
    universe = load_universe()
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    sender = TelegramSender()

    listers: dict[str, MarketLister] = {}
    for name in cfg.watched_exchanges:
        ex = universe.exchanges.get(name)
        if not ex:
            logger.warning("알 수 없는 거래소(유니버스에 없음): %s", name)
            continue
        try:
            listers[name] = MarketLister(name, ex.ccxt_id)
        except Exception as exc:
            logger.error("거래소 초기화 실패(건너뜀) %s(ccxt_id=%s): %s",
                         name, ex.ccxt_id, exc)

    logger.info(
        "announcer start: watch=%s quote_filter=%s interval=%ds telegram=%s",
        list(listers), cfg.quote_filter or "ALL", cfg.poll_interval_sec, sender.enabled,
    )
    try:
        while True:
            for name, lister in listers.items():
                try:
                    symbols = await lister.current_symbols()
                except Exception as exc:
                    logger.warning("[%s] 마켓 조회 실패: %s", name, exc)
                    continue
                new = await detect_new_listings(redis, name, symbols)
                new = [s for s in new if _passes_quote_filter(s, cfg.quote_filter)]
                for symbol in new:
                    sent = await sender.send(format_listing(name, symbol))
                    logger.info("NEW LISTING %s %s (sent=%s)", name, symbol, sent)
            await asyncio.sleep(cfg.poll_interval_sec)
    finally:
        await asyncio.gather(*[l.close() for l in listers.values()],
                             return_exceptions=True)
        await redis.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("announcer stopped")
