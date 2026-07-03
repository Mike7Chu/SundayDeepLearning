"""DART 공시 수집 엔트리포인트 — 관심종목 공시를 빠르게 포착·알림.

최근 공시를 dart_interval_sec(기본 30초)마다 폴링, 관심종목(또는 전 종목) 신규 공시를
Redis(dart:recent)에 저장 + 텔레그램 알림. 최초 1회는 조용히 시드(백로그 폭탄 방지).
키(DART_API_KEY) 없으면 비활성. 실행: python -m collector.news.main
"""
from __future__ import annotations

import asyncio
import json
import logging

import httpx
import redis.asyncio as aioredis

from collector.news.dart import DartClient, format_disclosure
from collector.stock.kis import load_watchlist
from notifier.telegram import TelegramSender
from shared.redis_keys import DART_RECENT_KEY, DART_SEEN_KEY
from shared.settings import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
logger = logging.getLogger("dart")

_RECENT_MAX = 200
_PRIMED_KEY = "dart:primed"


async def run() -> None:
    dart = DartClient()
    if not dart.enabled:
        logger.info("DART 미설정 → 공시 수집 비활성 (.env DART_API_KEY)")
        # 그냥 return하면 컨테이너가 exit 0 → restart 정책이 재기동 반복(크래시 루프처럼 보임).
        # idle로 살아있게 영구 대기.
        await asyncio.Event().wait()
        return
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    sender = TelegramSender()
    logger.info("dart start (interval=%ss, watch_all=%s, telegram=%s)",
                settings.dart_interval_sec, settings.dart_watch_all, sender.enabled)
    try:
        while True:
            try:
                watch = {w["code"] for w in load_watchlist()}
                async with httpx.AsyncClient(timeout=10) as client:
                    items = await dart.fetch_recent(client)
                if not settings.dart_watch_all:
                    items = [d for d in items if d["stock_code"] in watch]

                primed = bool(await redis.exists(_PRIMED_KEY))
                fresh = []
                for d in items:
                    if not await redis.sismember(DART_SEEN_KEY, d["rcept_no"]):
                        await redis.sadd(DART_SEEN_KEY, d["rcept_no"])
                        fresh.append(d)
                if not primed:
                    await redis.set(_PRIMED_KEY, "1")
                    logger.info("[dart] primed %d disclosures (silent)", len(items))
                else:
                    for d in fresh:
                        await redis.lpush(DART_RECENT_KEY, json.dumps(d, ensure_ascii=False))
                        await sender.send(format_disclosure(d))
                        logger.info("DISCLOSURE %s %s", d["corp_name"], d["report_nm"])
                    if fresh:
                        await redis.ltrim(DART_RECENT_KEY, 0, _RECENT_MAX - 1)
            except Exception as exc:
                logger.warning("공시 폴링 실패: %s", exc)
            await asyncio.sleep(settings.dart_interval_sec)
    finally:
        await redis.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("dart stopped")
