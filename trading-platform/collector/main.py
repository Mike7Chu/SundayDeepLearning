"""수집기 엔트리포인트 (주식 전용).

한국투자증권(KIS) 관심종목의 현재가(+밸류에이션)와 일봉·배당을 주기적으로 Redis에 적재.
키 미설정이면 비활성(idle). 실행: python -m collector.main
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

import httpx
import redis.asyncio as aioredis

from collector.stock.kis import KISClient, load_watchlist
from collector.stock.kis_master import fetch_universe
from collector.stock.toss import TossClient, candle_metrics
from shared.redis_keys import (
    STOCK_DIVIDEND_KEY,
    STOCK_MARKET_KEY,
    STOCK_QUOTE_KEY,
    STOCK_UNIVERSE_KEY,
    TOSS_ACCOUNT_KEY,
    TOSS_HOLDINGS_KEY,
    stock_ohlcv_key,
)
from shared.redis_store import replace_hash
from shared.settings import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
logger = logging.getLogger("collector")


async def merge_quote(redis: aioredis.Redis, code: str, name: str,
                      fields: dict) -> None:
    """stock:quote[code]를 읽어 넘어온 필드(비-None)만 갱신 후 저장.

    KIS(펀더멘털)와 Toss(시세·52주)가 서로의 필드를 지우지 않도록 병합 기록.
    """
    raw = await redis.hget(STOCK_QUOTE_KEY, code)
    rec: dict = {}
    if raw:
        try:
            rec = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            rec = {}
    rec["code"] = code
    if name:
        rec["name"] = name
    for k, v in fields.items():
        if v is not None:
            rec[k] = v
    rec["ts"] = time.time()
    await redis.hset(STOCK_QUOTE_KEY, code, json.dumps(rec, ensure_ascii=False))


async def stock_loop(redis: aioredis.Redis, kis: KISClient) -> None:
    """KIS 관심종목 현재가(+PER/PBR/EPS/BPS) 수집. 키 미설정이면 비활성."""
    if not kis.enabled:
        logger.info("KIS 미설정 → 주식 수집 비활성 (.env KIS_APP_KEY/SECRET)")
        return
    watch = load_watchlist()
    logger.info("stock collector start: %d종목 (paper=%s)", len(watch), settings.kis_paper)
    while True:
        n = 0
        async with httpx.AsyncClient(timeout=10) as client:
            for item in watch:
                try:
                    q = await kis.fetch_price(client, item["code"])
                    # 병합 기록(Toss 시세 필드 보존). KIS 현재가가 0이면 price는 덮지 않음.
                    if not q.get("price"):
                        q.pop("price", None)
                    await merge_quote(redis, item["code"], item["name"], q)
                    n += 1
                except Exception as exc:
                    logger.warning("[stock %s] 실패: %s", item["code"], exc)
        if n:
            logger.info("[stock] %d종목 수집(KIS)", n)
        await asyncio.sleep(settings.stock_interval_sec)


async def stock_history_loop(redis: aioredis.Redis, kis: KISClient) -> None:
    """관심종목 일봉(시그널용) + 배당(배당주용) — 느린 주기. 키 없으면 비활성."""
    if not kis.enabled:
        return
    watch = load_watchlist()
    while True:
        async with httpx.AsyncClient(timeout=15) as client:
            divs: dict[str, str] = {}
            for item in watch:
                code = item["code"]
                try:
                    candles = await kis.fetch_daily(client, code)
                    if candles:
                        await redis.set(stock_ohlcv_key(code), json.dumps(candles))
                except Exception as exc:
                    logger.warning("[stock daily %s] 실패: %s", code, exc)
                try:
                    dv = await kis.fetch_dividend(client, code)
                    if dv.get("items"):
                        divs[code] = json.dumps({**dv, "ts": time.time()})
                except Exception as exc:
                    logger.warning("[stock div %s] 실패: %s", code, exc)
            if divs:
                await replace_hash(redis, STOCK_DIVIDEND_KEY, divs)
        logger.info("[stock] 일봉/배당 수집 완료(%d종목)", len(watch))
        await asyncio.sleep(settings.stock_history_interval_sec)


async def universe_loop(redis: aioredis.Redis, kis: KISClient) -> None:
    """전체 시장 유니버스(코스피/코스닥 종목마스터) — 하루 1회 갱신. 실패 시 관심종목 폴백."""
    if not kis.enabled:
        return
    while True:
        try:
            async with httpx.AsyncClient(timeout=40) as client:
                uni = await fetch_universe(client)
            uni = uni[: settings.market_universe_max]
            if uni:
                await redis.set(STOCK_UNIVERSE_KEY, json.dumps(uni, ensure_ascii=False))
                logger.info("[universe] %d종목 저장", len(uni))
        except Exception as exc:
            logger.warning("[universe] 실패: %s", exc)
        await asyncio.sleep(86400)


async def _universe_codes(redis: aioredis.Redis) -> list[dict]:
    raw = await redis.get(STOCK_UNIVERSE_KEY)
    if raw:
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            pass
    return load_watchlist()   # 폴백


async def market_loop(redis: aioredis.Redis, kis: KISClient) -> None:
    """유니버스 펀더멘털을 배치로 순회 수집 → stock:market (전체 시장 스크리너). 키 없으면 비활성."""
    if not kis.enabled:
        return
    cursor = 0
    await asyncio.sleep(20)   # 유니버스 로딩 여유
    while True:
        uni = await _universe_codes(redis)
        if not uni:
            await asyncio.sleep(60)
            continue
        batch = uni[cursor: cursor + settings.market_batch]
        if not batch:
            cursor = 0
            continue
        out: dict[str, str] = {}
        async with httpx.AsyncClient(timeout=10) as client:
            for item in batch:
                code = item["code"]
                try:
                    q = await kis.fetch_price(client, code)
                    out[code] = json.dumps({"code": code, "name": item.get("name", ""),
                                            "ts": time.time(), **q}, ensure_ascii=False)
                except Exception:
                    continue
        if out:
            await redis.hset(STOCK_MARKET_KEY, mapping=out)
        cursor += settings.market_batch
        n = max(1, (len(uni) + settings.market_batch - 1) // settings.market_batch)
        await asyncio.sleep(max(10, settings.market_scan_interval_sec / n))


async def portfolio_loop(redis: aioredis.Redis, toss: TossClient) -> None:
    """토스증권 실보유(잔고)·매수여력 수집 → toss:holdings / toss:account. 키 없으면 비활성."""
    if not toss.enabled:
        logger.info("토스 미설정 → 포트폴리오 수집 비활성 (.env TOSS_CLIENT_ID/SECRET)")
        return
    account: str | None = None
    while True:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                if account is None:
                    account = await toss.resolve_account_seq(client)
                    if account is None:
                        logger.warning("[toss] 계좌 조회 실패 — 재시도 대기")
                        await asyncio.sleep(60)
                        continue
                    logger.info("[toss] account=%s", account)
                snap = await toss.fetch_holdings(client, account)
                # 100억은 원화 기준: KRW 평가액 + (USD 평가액 × 환율). USD 보유 없으면 환율 조회 생략.
                total_eval = snap.get("total_eval_krw") or 0.0
                usd = snap.get("total_eval_usd")
                if usd:
                    try:
                        fx = await toss.fetch_exchange_rate(client)  # USD→KRW
                        rate = float(fx.get("rate") or 0)
                        total_eval += usd * rate
                    except Exception as exc:
                        logger.warning("[toss] 환율 조회 실패(USD 미반영): %s", exc)
                snap["total_eval"] = round(total_eval, 2)
                # 매수여력은 별도 try — 실패해도 보유/평가는 저장(한 엔드포인트가 전체를 막지 않게).
                try:
                    bp = await toss.fetch_buying_power(client, account, "KRW")
                except Exception as exc:
                    logger.warning("[toss] 매수여력 조회 실패: %s", exc)
                    bp = {"buying_power": None}
            await redis.set(TOSS_HOLDINGS_KEY, json.dumps(snap, ensure_ascii=False))
            await redis.set(TOSS_ACCOUNT_KEY, json.dumps(
                {"accountSeq": account, "buying_power": bp.get("buying_power"),
                 "ts": time.time()}, ensure_ascii=False))
            logger.info("[toss] %d보유 · 평가 %.0f원", len(snap.get("holdings", [])),
                        snap.get("total_eval", 0) or 0)
        except Exception as exc:
            logger.warning("[toss] 포트폴리오 수집 실패: %s", exc)
        await asyncio.sleep(settings.toss_interval_sec)


async def toss_history_loop(redis: aioredis.Redis, toss: TossClient) -> None:
    """토스 일봉·종목정보 → stock:ohlcv(시그널/백테스트) + stock:quote 52주/시총/이름.

    KIS가 시세를 못 채워도 대시보드·리서치가 데이터를 갖도록 하는 주 소스.
    """
    if not toss.enabled:
        return
    watch = load_watchlist()
    while True:
        try:
            codes = [w["code"] for w in watch]
            async with httpx.AsyncClient(timeout=20) as client:
                info = await toss.fetch_stocks(client, codes)
                for w in watch:
                    code = w["code"]
                    try:
                        candles = await toss.fetch_daily_history(client, code)
                    except Exception as exc:
                        logger.warning("[toss hist %s] 실패: %s", code, exc)
                        continue
                    if not candles:
                        continue
                    await redis.set(stock_ohlcv_key(code),
                                    json.dumps(candles, ensure_ascii=False))
                    m = candle_metrics(candles)
                    meta = info.get(code, {})
                    shares = meta.get("shares")
                    last = m.get("last_close")
                    mktcap = round(last * shares / 1e8, 1) if (last and shares) else None
                    await merge_quote(redis, code, meta.get("name") or w.get("name", ""), {
                        "price": last, "change_pct": m["change_pct"],
                        "high_52w": m["high_52w"], "low_52w": m["low_52w"],
                        "prev_close": m["prev_close"], "shares": shares,
                        "market_cap": mktcap,
                    })
            logger.info("[toss] 일봉/종목정보 수집 %d종목", len(watch))
        except Exception as exc:
            logger.warning("[toss] 일봉 수집 실패: %s", exc)
        await asyncio.sleep(settings.stock_history_interval_sec)


async def toss_price_loop(redis: aioredis.Redis, toss: TossClient) -> None:
    """토스 현재가(다건) → stock:quote 실시간 갱신. 전일종가 기준 등락률 재계산."""
    if not toss.enabled:
        return
    watch = load_watchlist()
    codes = [w["code"] for w in watch]
    await asyncio.sleep(10)   # 최초 일봉(prev_close) 적재 여유
    while True:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                prices = await toss.fetch_prices(client, codes)
            n = 0
            for p in prices:
                price = p.get("price")
                if price is None:
                    continue
                raw = await redis.hget(STOCK_QUOTE_KEY, p["symbol"])
                rec = json.loads(raw) if raw else {}
                prev = rec.get("prev_close")
                shares = rec.get("shares")
                fields = {"price": price}
                if prev:
                    fields["change_pct"] = round((price - prev) / prev * 100, 2)
                if shares:
                    fields["market_cap"] = round(price * shares / 1e8, 1)
                await merge_quote(redis, p["symbol"], "", fields)
                n += 1
            if n:
                logger.info("[toss] 시세 %d종목", n)
        except Exception as exc:
            logger.warning("[toss] 시세 수집 실패: %s", exc)
        await asyncio.sleep(settings.stock_interval_sec)


async def main() -> None:
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    logger.info("collector start (stock-only)")
    kis = KISClient()       # 토큰 1회 발급·공유(4개 루프 주입 — 과발급 방지)
    toss = TossClient()
    try:
        await asyncio.gather(
            stock_loop(redis, kis),
            stock_history_loop(redis, kis),
            universe_loop(redis, kis),
            market_loop(redis, kis),
            portfolio_loop(redis, toss),
            toss_history_loop(redis, toss),
            toss_price_loop(redis, toss),
        )
        # 여기 도달 = 모든 루프가 키 미설정으로 종료. 프로세스가 그냥 끝나면
        # restart 정책이 20초마다 재기동(크래시 루프처럼 보임) → idle로 살아있게 대기.
        logger.info("활성 수집 루프 없음(KIS/TOSS 키 미설정) — idle 대기. .env 설정 후 재기동")
        await asyncio.Event().wait()
    finally:
        await redis.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("collector stopped")
