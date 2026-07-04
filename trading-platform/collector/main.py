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

from collector.news.dart import DartClient
from collector.stock.kis import KISClient, effective_watchlist, load_watchlist
from collector.stock.kis_master import fetch_universe
from collector.stock.toss import TossClient, candle_metrics
from shared.redis_keys import (
    DART_CORP_KEY,
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
# httpx는 매 요청을 INFO로 찍어 KIS 간헐 500(자동 재시도로 복구됨) 로그가 시끄러움 → WARNING만.
logging.getLogger("httpx").setLevel(logging.WARNING)
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
    # 가격 이상치 방어: 52주 범위를 크게 벗어난 현재가는 오염으로 보고 제거(다음 정상 수집 때 채움).
    p, hi, lo = rec.get("price"), rec.get("high_52w"), rec.get("low_52w")
    if p and hi and lo and hi > 0 and (p > hi * 2 or p < lo * 0.5):
        rec.pop("price", None)
        p = None
    # PER/PBR/ROE는 신뢰 가능한 현재가 + EPS/BPS로 재계산(소스 불일치·오염 방지).
    e, b = rec.get("eps"), rec.get("bps")
    if p and e:
        rec["per"] = round(p / e, 2)
    if p and b and b > 0:
        rec["pbr"] = round(p / b, 2)
    if e is not None and b and b > 0:
        rec["roe"] = round(e / b * 100, 2)
    rec["ts"] = time.time()
    await redis.hset(STOCK_QUOTE_KEY, code, json.dumps(rec, ensure_ascii=False))


async def stock_loop(redis: aioredis.Redis, kis: KISClient) -> None:
    """KIS 관심종목 현재가(+PER/PBR/EPS/BPS) 수집. 키 미설정이면 비활성."""
    if not kis.enabled:
        logger.info("KIS 미설정 → 주식 수집 비활성 (.env KIS_APP_KEY/SECRET)")
        return
    logger.info("stock collector start (paper=%s)", settings.kis_paper)
    client = kis.http()   # 공유 커넥션
    while True:
        watch = await effective_watchlist(redis)   # 매 주기 재조회(UI 편집 반영)
        n = 0
        for item in watch:
            try:
                q = await kis.fetch_price(client, item["code"])
                # KIS 현재가/PER/PBR는 부정확 사례가 있어(예: 삼성전자 5배 부풀림) 신뢰도 높은
                # Toss 가격을 쓴다. KIS에선 EPS·BPS(재무)만 취하고 PER/PBR은 merge_quote가
                # Toss 가격으로 재계산. (부재 시 KIS 값 폴백)
                fund = {"eps": q.get("eps"), "bps": q.get("bps")}
                await merge_quote(redis, item["code"], item["name"], fund)
                n += 1
            except Exception as exc:
                logger.warning("[stock %s] 실패: %s", item["code"], exc)
        if n:
            logger.info("[stock] %d종목 수집(KIS)", n)
        await asyncio.sleep(settings.stock_interval_sec)


def _latest_report_year() -> int:
    """가장 최근 '사업보고서' 연도(작년). 예: 2026년 7월 → 2025년 보고서."""
    import datetime as _dt
    return _dt.date.today().year - 1


async def _dart_corp_map(redis: aioredis.Redis, dart: DartClient,
                         client: httpx.AsyncClient) -> dict:
    """종목코드→corp_code 매핑. Redis 7일 캐시, 실패 시 빈 dict(다음 주기 재시도)."""
    raw = await redis.get(DART_CORP_KEY)
    if raw:
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            pass
    try:
        m = await dart.fetch_corp_map(client)
    except Exception as exc:
        logger.warning("[DATA_ERROR] DART corp_code 매핑 다운로드 실패: %s", exc)
        return {}
    if m:
        await redis.set(DART_CORP_KEY, json.dumps(m), ex=7 * 86400)
    return m


async def _dart_dividend(dart: DartClient, client: httpx.AsyncClient,
                         corp: str) -> list[dict]:
    """3개년 배당 조회 — 재시도 최대 2회, 실패/빈값은 [] (무한 대기·루프 금지)."""
    year = _latest_report_year()
    for attempt in range(2):
        try:
            items = await dart.fetch_dividend_years(client, corp, year)
            if not items:   # 최신 사업보고서 미공시면 한 해 전으로 폴백
                items = await dart.fetch_dividend_years(client, corp, year - 1)
            return items
        except Exception:
            if attempt == 0:
                await asyncio.sleep(1)
    return []


async def stock_history_loop(redis: aioredis.Redis,
                             dart: DartClient) -> None:
    """관심종목 배당 3개년(DART 사업보고서) — 느린 주기.

    일봉(차트)은 toss_history_loop가 담당(검증된 소스). KIS 일봉·예탁원은 이 환경에서
    연결 불가라 제거. 실패는 [DATA_ERROR] 기록 후 다음 종목 — 절대 멈추지 않는다.
    """
    if not dart.enabled:
        logger.info("[div] DART_API_KEY 미설정 → 배당 수집 비활성")
        return
    while True:
        watch = await effective_watchlist(redis)
        divs: dict[str, str] = {}
        async with httpx.AsyncClient(timeout=15) as dclient:
            cmap = await _dart_corp_map(redis, dart, dclient)
            for item in watch:
                code, name = item["code"], item.get("name", "")
                corp = cmap.get(code)
                if not corp:
                    logger.warning("[DATA_ERROR] %s 배당금 수집 실패(corp_code 없음)",
                                   name or code)
                    continue
                items = await _dart_dividend(dart, dclient, corp)
                if items:
                    divs[code] = json.dumps(
                        {"code": code, "items": items, "src": "dart",
                         "ts": time.time()}, ensure_ascii=False)
                else:
                    logger.warning("[DATA_ERROR] %s 배당금 수집 실패(응답 없음/무배당)",
                                   name or code)
                # 순이익 성장률(YoY) — 트레일링 PER 함정 보정용 성장 축 데이터.
                try:
                    g = await dart.fetch_net_income_growth(
                        dclient, corp, _latest_report_year())
                    if g is not None:
                        await merge_quote(redis, code, name, {"ni_growth_pct": g})
                except Exception:
                    logger.warning("[DATA_ERROR] %s 순이익 성장률 수집 실패", name or code)
        if divs:
            await replace_hash(redis, STOCK_DIVIDEND_KEY, divs)
        logger.info("[stock] 배당 수집 완료(%d종목 중 %d종목)", len(watch), len(divs))
        await asyncio.sleep(settings.stock_history_interval_sec if divs else 600)


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


async def market_loop(redis: aioredis.Redis, kis: KISClient,
                      toss: TossClient) -> None:
    """유니버스 펀더멘털 배치 수집 → stock:market. 가격은 Toss(신뢰), 재무는 KIS(EPS/BPS).
    PER/PBR/ROE는 Toss 가격으로 재계산. 키 없으면 비활성."""
    if not kis.enabled:
        return
    cursor = 0
    await asyncio.sleep(20)   # 유니버스 로딩 여유
    client = kis.http()   # 공유 커넥션
    while True:
        uni = await _universe_codes(redis)
        if not uni:
            await asyncio.sleep(60)
            continue
        batch = uni[cursor: cursor + settings.market_batch]
        if not batch:
            cursor = 0
            continue
        codes = [it["code"] for it in batch]
        # Toss 가격 배치 조회(신뢰). 실패 시 KIS 가격 폴백.
        tprice: dict[str, float] = {}
        if toss.enabled:
            try:
                async with httpx.AsyncClient(timeout=15) as tc:
                    for p in await toss.fetch_prices(tc, codes):
                        if p.get("price"):
                            tprice[p["symbol"]] = p["price"]
            except Exception as exc:
                logger.warning("[market] Toss 가격 실패: %s", exc)
        out: dict[str, str] = {}
        for item in batch:
            code = item["code"]
            try:
                q = await kis.fetch_price(client, code)
            except Exception:
                continue
            price = tprice.get(code) or q.get("price")
            eps, bps = q.get("eps"), q.get("bps")
            per = round(price / eps, 2) if (price and eps) else q.get("per")
            pbr = round(price / bps, 2) if (price and bps and bps > 0) else q.get("pbr")
            roe = round(eps / bps * 100, 2) if (eps is not None and bps and bps > 0) else None
            out[code] = json.dumps({
                "code": code, "name": item.get("name", ""), "ts": time.time(),
                "price": price, "per": per, "pbr": pbr, "eps": eps, "bps": bps, "roe": roe,
                "high_52w": q.get("high_52w"), "low_52w": q.get("low_52w"),
            }, ensure_ascii=False)
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
    while True:
        try:
            watch = await effective_watchlist(redis)
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
    await asyncio.sleep(10)   # 최초 일봉(prev_close) 적재 여유
    while True:
        try:
            codes = [w["code"] for w in await effective_watchlist(redis)]
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
    dart = DartClient()     # 배당 3개년(사업보고서) 소스
    try:
        await asyncio.gather(
            stock_loop(redis, kis),
            stock_history_loop(redis, dart),
            universe_loop(redis, kis),
            market_loop(redis, kis, toss),
            portfolio_loop(redis, toss),
            toss_history_loop(redis, toss),
            toss_price_loop(redis, toss),
        )
        # 여기 도달 = 모든 루프가 키 미설정으로 종료. 프로세스가 그냥 끝나면
        # restart 정책이 20초마다 재기동(크래시 루프처럼 보임) → idle로 살아있게 대기.
        logger.info("활성 수집 루프 없음(KIS/TOSS 키 미설정) — idle 대기. .env 설정 후 재기동")
        await asyncio.Event().wait()
    finally:
        await kis.aclose()
        await redis.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("collector stopped")
