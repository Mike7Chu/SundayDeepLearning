"""주식 일일 브리핑 엔트리포인트 — 스케줄 발송.

실행: python -m briefing.main
시세/시그널/가치/배당을 모아 텔레그램으로 1일 1회 요약 발송(키 없으면 로그만).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone

import redis.asyncio as aioredis

from api.services.stats import summarize
from api.services.stock_dividend import dividend_view
from api.services.stock_signal import signals_for
from api.services.stock_value import value_screener
from briefing.compose import compose_brief, has_content
from collector.stock.kis import load_watchlist
from notifier.telegram import TelegramSender, dashboard_buttons
from shared.redis_keys import (
    AGENT_LAST_KEY,
    BRIEFING_DONE_KEY,
    ENGINE_PLAN_KEY,
    ENGINE_RISK_KEY,
    JOURNAL_KEY,
    MARKET_INDICATORS_KEY,
    STOCK_MARKET_KEY,
    STOCK_QUOTE_KEY,
    STOCK_UNIVERSE_KEY,
)
from shared.settings import settings

KST = timezone(timedelta(hours=9))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
logger = logging.getLogger("briefing")


async def gather(redis: aioredis.Redis) -> tuple[list, list, list, list, list]:
    raw = await redis.hgetall(STOCK_QUOTE_KEY)
    quotes = []
    for v in raw.values():
        try:
            quotes.append(json.loads(v))
        except (json.JSONDecodeError, TypeError):
            continue
    # 이름 없는 시세(가격만 들어온 유니버스·급등주 항목)는 전 시장 유니버스 맵에서 보강.
    try:
        uni_raw = await redis.get(STOCK_UNIVERSE_KEY)
        if uni_raw:
            names = {u["code"]: u.get("name") for u in json.loads(uni_raw)
                     if u.get("code") and u.get("name")}
            for q in quotes:
                if not q.get("name") and names.get(q.get("code")):
                    q["name"] = names[q["code"]]
    except (json.JSONDecodeError, TypeError, KeyError):
        pass
    value_rows = (await value_screener(redis)).get("rows", [])
    signal_rows = []
    for w in load_watchlist():
        s = await signals_for(redis, w["code"], w.get("name", ""))
        if s:
            signal_rows.append(s)
    div = await dividend_view(redis, settings.briefing_drip_budget)
    return quotes, value_rows, signal_rows, div.get("rows", []), div.get("drip", [])


async def _jget(redis: aioredis.Redis, key: str) -> dict:
    raw = await redis.get(key)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


async def _extras(redis: aioredis.Redis) -> dict:
    """행동에 가까운 섹션(시장·플랜·자동매매·성적)을 엔진/시장 스냅샷에서 모은다."""
    journal = []
    for raw in await redis.lrange(JOURNAL_KEY, 0, -1):
        try:
            journal.append(json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            continue
    entries = [{"code": j.get("code"), "side": j.get("side"), "price": j.get("price"),
                "qty": j.get("qty"), "ts": j.get("ts")} for j in journal]
    # 미장 간밤 동향 — 수집된 미국 유니버스(stock:market)의 등락 상·하위
    us = []
    for v in (await redis.hgetall(STOCK_MARKET_KEY)).values():
        try:
            q = json.loads(v)
        except (json.JSONDecodeError, TypeError):
            continue
        if q.get("currency") == "USD" and q.get("change_pct") is not None and q.get("price"):
            us.append(q)
    us.sort(key=lambda q: q.get("change_pct") or 0, reverse=True)
    us_movers = {"gainers": us[:3], "losers": us[-3:][::-1]} if us else {}
    # 발굴 레이더 — 무겁지만 하루 1회이므로 직접 계산(Toss 캔들 온디맨드)
    try:
        from api.routers.stocks import _radar_build
        radar = await _radar_build(8)
    except Exception as exc:
        logger.info("레이더 계산 생략: %s", exc)
        radar = {}
    return {
        "market": await _jget(redis, MARKET_INDICATORS_KEY),
        "plan": await _jget(redis, ENGINE_PLAN_KEY),
        "risk": await _jget(redis, ENGINE_RISK_KEY),
        "agent": await _jget(redis, AGENT_LAST_KEY),
        "stats": summarize(entries) if entries else {},
        "us": us_movers,
        "radar": radar,
    }


async def run_once(redis: aioredis.Redis, sender: TelegramSender) -> bool:
    quotes, value_rows, signal_rows, div_rows, drip = await gather(redis)
    ex = await _extras(redis)
    has_plan = bool((ex["plan"].get("buys") or ex["plan"].get("sells")))
    if not (has_content(quotes, value_rows, signal_rows, div_rows) or has_plan):
        logger.info("브리핑 생략 — 데이터 없음(KIS 키/수집 대기)")
        return False
    msg = compose_brief(quotes, value_rows, signal_rows, div_rows, drip,
                        market=ex["market"], plan=ex["plan"], risk=ex["risk"],
                        agent=ex["agent"], stats=ex["stats"], us=ex["us"],
                        radar=ex["radar"])
    await sender.send_long(msg, buttons=dashboard_buttons())   # 길이 안전 + 열기 버튼
    logger.info("브리핑 발송(telegram=%s, %d종목)", sender.enabled, len(quotes))
    return True


async def _quotes_fresh(redis: aioredis.Redis) -> bool:
    """저장 시세 중 가장 최신 ts가 신선한가 — 재시작 직후 '지난주 값' 발송 방어.

    Redis 영속화(RDB)로 재시작해도 옛 시세가 남아, 수집기가 갱신하기 전에 브리핑이
    나가면 지난주 가격이 찍힌다. 최신 시세가 briefing_stale_hours보다 오래면 보류.
    """
    newest = 0.0
    for v in (await redis.hgetall(STOCK_QUOTE_KEY)).values():
        try:
            ts = json.loads(v).get("ts") or 0
        except (json.JSONDecodeError, TypeError):
            ts = 0
        newest = max(newest, float(ts or 0))
    return bool(newest) and (time.time() - newest) < settings.briefing_stale_hours * 3600


async def run() -> None:
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    sender = TelegramSender()
    logger.info("briefing start (발송 %d시 KST · 확인 %.0fs · telegram=%s)",
                settings.briefing_hour_kst, settings.briefing_check_sec, sender.enabled)
    try:
        while True:
            try:
                now = datetime.now(KST)
                today = now.strftime("%Y-%m-%d")
                done = await redis.get(BRIEFING_DONE_KEY)
                if done != today and now.hour >= settings.briefing_hour_kst:
                    if not await _quotes_fresh(redis):
                        logger.info("브리핑 보류 — 시세 갱신 전(재시작 직후?). 다음 확인 대기")
                    elif await run_once(redis, sender):
                        await redis.set(BRIEFING_DONE_KEY, today)   # 하루 1회 dedup
            except Exception as exc:
                logger.warning("브리핑 실패: %s", exc)
            await asyncio.sleep(settings.briefing_check_sec)
    finally:
        await redis.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("briefing stopped")
