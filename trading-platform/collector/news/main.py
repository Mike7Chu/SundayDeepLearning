"""DART 공시 수집 엔트리포인트 — 관심종목 공시를 빠르게 포착·알림.

최근 공시를 dart_interval_sec(기본 30초)마다 폴링, 관심종목(또는 전 종목) 신규 공시를
Redis(dart:recent)에 저장 + 텔레그램 알림. 최초 1회는 조용히 시드(백로그 폭탄 방지).
키(DART_API_KEY) 없으면 비활성. 실행: python -m collector.news.main
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

import httpx
import redis.asyncio as aioredis

from collector.news.crawl import crawl_stock_news
from collector.news.dart import DartClient, find_earnings_flash, format_disclosure
from collector.stock.kis import effective_watchlist
from api.services.news_sentiment import news_sentiment
from notifier.telegram import TelegramSender
from shared.redis_keys import (
    DART_RECENT_KEY,
    DART_SEEN_KEY,
    ENGINE_PLAN_KEY,
    NEWS_SENT_KEY,
    STOCK_MARKET_KEY,
    STOCK_QUOTE_KEY,
    TOSS_HOLDINGS_KEY,
)
from shared.settings import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("dart")

_RECENT_MAX = 200
_PRIMED_KEY = "dart:primed"


async def _merge_flash(redis: aioredis.Redis, code: str, fields: dict) -> None:
    """잠정실적 수치를 시세 레코드(quote·market 양쪽)에 병합 — 점수·AI가 즉시 사용."""
    for key in (STOCK_QUOTE_KEY, STOCK_MARKET_KEY):
        raw = await redis.hget(key, code)
        rec = {"code": code}
        if raw:
            try:
                rec = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                rec = {"code": code}
        rec.update(fields)
        await redis.hset(key, code, json.dumps(rec, ensure_ascii=False))


async def _handle_flash(redis: aioredis.Redis, dart: DartClient,
                        client: httpx.AsyncClient, sender: TelegramSender,
                        d: dict) -> None:
    """신규 잠정실적 공시 → 원문에서 YoY 수치 추출 → 레코드 병합 + 상세 알림."""
    fig = None
    try:
        fig = await dart.fetch_flash_figures(client, d["rcept_no"])
    except Exception as exc:
        logger.warning("[flash %s] 수치 추출 실패: %s", d.get("stock_code"), exc)
    if not fig:
        return
    label = f"{d.get('rcept_dt', '')[:4]}.{d.get('rcept_dt', '')[4:6]} 잠정"
    await _merge_flash(redis, d["stock_code"], {
        "flash_rev_yoy": fig.get("rev_yoy"), "flash_op_yoy": fig.get("op_yoy"),
        "flash_ni_yoy": fig.get("ni_yoy"), "flash_label": label,
        "flash_date": d.get("rcept_dt", ""), "flash_url": d.get("url", "")})

    def pct(v):
        return f"{v:+.1f}%" if v is not None else "–"
    await sender.send(
        f"📊 잠정실적 발표 — {d.get('corp_name')}({d.get('stock_code')})\n"
        f"전년 동기 대비: 매출 {pct(fig.get('rev_yoy'))} · "
        f"영업이익 {pct(fig.get('op_yoy'))} · 순이익 {pct(fig.get('ni_yoy'))}\n"
        "점수(성장 축)·AI 분석에 즉시 반영됐어요")
    logger.info("[flash] %s rev=%s op=%s ni=%s", d.get("stock_code"),
                fig.get("rev_yoy"), fig.get("op_yoy"), fig.get("ni_yoy"))


async def _stock_name(redis: aioredis.Redis, code: str) -> str:
    for key in (STOCK_QUOTE_KEY, STOCK_MARKET_KEY):
        raw = await redis.hget(key, code)
        if raw:
            try:
                nm = json.loads(raw).get("name")
            except (json.JSONDecodeError, TypeError):
                nm = None
            if nm:
                return nm
    return ""


async def _news_sentiment_pass(redis: aioredis.Redis) -> None:
    """stage1(1차분류)+관심종목의 DART 제목 + 구글뉴스 헤드라인 → 규칙기반 감성 저장.

    점수 '뉴스' 축(±5)이 이걸 읽어 평가에 반영. 국내(6자리)만, 상한·간격으로 부하 억제.
    """
    codes: list[str] = []
    try:
        raw_plan = await redis.get(ENGINE_PLAN_KEY)
        if raw_plan:
            codes += (json.loads(raw_plan).get("stage1_codes") or [])
    except (json.JSONDecodeError, TypeError):
        pass
    for w in await effective_watchlist(redis):
        if w.get("code"):
            codes.append(w["code"])
    seen: set[str] = set()
    uniq = [c for c in codes if str(c).isdigit() and not (c in seen or seen.add(c))]
    uniq = uniq[:settings.news_sent_max]
    if not uniq:
        return
    dart_titles: dict[str, list[str]] = {}
    for v in await redis.lrange(DART_RECENT_KEY, 0, 300):
        try:
            it = json.loads(v)
        except (json.JSONDecodeError, TypeError):
            continue
        sc = it.get("stock_code")
        if sc:
            dart_titles.setdefault(sc, []).append(it.get("report_nm") or "")
    done = 0
    for code in uniq:
        titles = list(dart_titles.get(code, []))
        name = await _stock_name(redis, code)
        try:
            news = await crawl_stock_news(name or code, kr=True, cap=8)
            titles += [n.get("title") or "" for n in news]
        except Exception:
            pass
        sent = news_sentiment(titles)
        if sent:
            await redis.hset(NEWS_SENT_KEY, code,
                             json.dumps({**sent, "ts": time.time()}, ensure_ascii=False))
            done += 1
        await asyncio.sleep(1)          # 크롤 간격 — 구글 뉴스 rate-limit 방지
    logger.info("[news-sent] 감성 갱신 %d종목", done)


async def _news_sentiment_loop(redis: aioredis.Redis) -> None:
    if not settings.news_sent_enabled:
        return
    await asyncio.sleep(20)             # 기동 직후 여유(플랜·시세 적재 대기)
    while True:
        try:
            await _news_sentiment_pass(redis)
        except Exception as exc:
            logger.warning("[news-sent] 패스 실패: %s", exc)
        await asyncio.sleep(settings.news_sent_interval_sec)


async def run() -> None:
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    sender = TelegramSender()
    news_task = asyncio.create_task(_news_sentiment_loop(redis))   # DART와 독립 병행
    dart = DartClient()
    if not dart.enabled:
        logger.info("DART 미설정 → 공시 수집 비활성 (.env DART_API_KEY). 뉴스 감성만 구동.")
        try:
            await news_task            # 뉴스 감성은 계속(DART 없어도)
        finally:
            await redis.aclose()
        return
    logger.info("dart start (interval=%ss, watch_all=%s, telegram=%s)",
                settings.dart_interval_sec, settings.dart_watch_all, sender.enabled)
    try:
        while True:
            try:
                # 관심종목은 UI 편집분(Redis) 우선 + 보유종목도 항상 감시.
                # (이전엔 yaml만 읽어 UI로 추가한 종목의 공시를 놓쳤음)
                watch = {w["code"] for w in await effective_watchlist(redis)}
                try:
                    raw_h = await redis.get(TOSS_HOLDINGS_KEY)
                    if raw_h:
                        watch |= {h["symbol"] for h in
                                  json.loads(raw_h).get("holdings", [])
                                  if h.get("symbol")}
                except (json.JSONDecodeError, TypeError):
                    pass
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
                    async with httpx.AsyncClient(timeout=20) as client:
                        for d in fresh:
                            await redis.lpush(DART_RECENT_KEY,
                                              json.dumps(d, ensure_ascii=False))
                            await sender.send(format_disclosure(d))
                            logger.info("DISCLOSURE %s %s",
                                        d["corp_name"], d["report_nm"])
                            # 잠정실적이면 수치까지 추출해 점수·AI에 즉시 반영
                            if find_earnings_flash([d], d.get("stock_code", "")):
                                await _handle_flash(redis, dart, client, sender, d)
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
