"""주식(KIS) 시세 + 전략 API (시그널·가치·배당)."""
from __future__ import annotations

import datetime as _dt
import json
import time as _time

import httpx
from fastapi import APIRouter

import json as _json

from api.redis_client import get_redis
from collector.news.dart import DartClient
from collector.stock.toss import TossClient, candle_metrics
from api.services.stock_dividend import compute_dividend, dividend_view
from api.services.stock_signal import (
    adx,
    evaluate_signals,
    light_pillar,
    signals_for,
    trade_levels,
)
from api.services.cache import get_or_compute
from api.services.stock_score import compute_score
from api.services.stock_radar import market_regime, radar_pool, radar_score
from api.services.stock_value import load_quotes, value_screener
from backtest.engine import STRATEGIES, backtest
from collector.stock.kis import effective_watchlist, is_kr_code
from fastapi import HTTPException
from shared.redis_keys import (
    DART_CORP_KEY,
    DART_RECENT_KEY,
    MARKET_INDICATORS_KEY,
    MARKET_RANKINGS_KEY,
    STOCK_DIVIDEND_KEY,
    STOCK_MARKET_KEY,
    STOCK_QUOTE_KEY,
    TOSS_HOLDINGS_KEY,
    stock_ohlcv_key,
)

router = APIRouter()


@router.get("/stocks")
async def stocks() -> dict:
    raw = await get_redis().hgetall(STOCK_QUOTE_KEY)
    rows = [json.loads(v) for v in raw.values()]
    rows.sort(key=lambda r: r.get("change_pct", 0), reverse=True)
    return {"rows": rows}


@router.get("/stocks/all")
async def stocks_all() -> dict:
    """전체 시장 시세(수집된 유니버스 stock:market ∪ 관심종목 stock:quote). 등락률 정렬.

    전체 시장 파싱은 무겁고 원본은 5분 주기 갱신 → 20초 캐시(대시보드 12초 자동갱신 대응).
    """
    # 응답 슬림화: 표에 쓰는 컬럼만 전송(전 필드 3,600행은 수 MB → 직렬화·전송 지연)
    _COLS = ("code", "name", "price", "change_pct", "per", "pbr", "roe",
             "ni_growth_q_pct", "ni_growth_q_label", "high_52w", "market_cap",
             "currency")

    async def _build() -> dict:
        rows = [r for r in await load_quotes(get_redis()) if r.get("price")]
        rows.sort(key=lambda r: r.get("change_pct") or 0, reverse=True)
        slim = [{k: r.get(k) for k in _COLS if r.get(k) is not None} for r in rows]
        return {"rows": slim, "total": len(slim)}
    return await get_or_compute("stocks_all", 20, _build)


@router.get("/stocks/value")
async def stocks_value(limit: int = 200) -> dict:
    """가치투자 스크리너(마법공식 랭킹). 전체 시장 수집분(stock:market) 기준, 상위 limit."""
    return await get_or_compute(
        f"stocks_value:{limit}", 30,
        lambda: value_screener(get_redis(), limit=limit))


@router.get("/stocks/radar")
async def stocks_radar(limit: int = 12) -> dict:
    """터질 종목 발굴 레이더 — 급등 전조(거래대금·신고가·강도·실적·추세) 조합 랭킹.

    후보군(랭킹 급등∪실적 촉매∪신고가 근접)에만 온디맨드 캔들 조회 → 전조 점수.
    Pi 부하 억제를 위해 3분 캐시. 예측이 아닌 '지금 깨어나는' 종목 스캔(면책).
    """
    return await get_or_compute(f"stocks_radar:{limit}", 180,
                                lambda: _radar_build(limit))


async def _radar_build(limit: int) -> dict:
    redis = get_redis()
    quotes = await load_quotes(redis)
    # 토스 랭킹(급등·거래대금 상위) — 이미 '움직이는' 종목
    ranking_codes: list[str] = []
    rk_raw = await redis.get(MARKET_RANKINGS_KEY)
    if rk_raw:
        try:
            rk = _json.loads(rk_raw)
            for key in ("kr_gainers", "kr_amount"):
                for r in rk.get(key) or []:
                    if r.get("symbol"):
                        ranking_codes.append(r["symbol"])
        except (ValueError, TypeError):
            pass
    # 최근 실적·공시 촉매 종목(DART)
    flash_codes: set[str] = set()
    for item in await redis.lrange(DART_RECENT_KEY, 0, 60):
        try:
            d = _json.loads(item)
        except (ValueError, TypeError):
            continue
        if d.get("stock_code"):
            flash_codes.add(d["stock_code"])
    # 보유 종목 제외(이미 들고 있으면 '발굴' 대상 아님)
    held: set[str] = set()
    h_raw = await redis.get(TOSS_HOLDINGS_KEY)
    if h_raw:
        try:
            held = {h["symbol"] for h in _json.loads(h_raw).get("holdings", [])
                    if h.get("symbol")}
        except (ValueError, TypeError):
            pass
    pool = radar_pool(quotes, ranking_codes, list(flash_codes), held)
    qmap = {q.get("code"): q for q in quotes if q.get("code")}
    rows: list[dict] = []
    async with httpx.AsyncClient(timeout=20) as tc:
        for code in pool:
            q = qmap.get(code, {"code": code})
            candles: list = []
            oraw = await redis.get(stock_ohlcv_key(code))
            if oraw:
                try:
                    candles = _json.loads(oraw)
                except (ValueError, TypeError):
                    candles = []
            if len(candles) < 20 and _toss.enabled:
                try:
                    candles = await _toss.fetch_daily_history(tc, code)
                    if candles:
                        await redis.set(stock_ohlcv_key(code),
                                        _json.dumps(candles), ex=21600)
                except Exception:
                    candles = []
            r = radar_score(q, candles, has_flash=(code in flash_codes))
            if r:
                if not r.get("name"):
                    r["name"] = q.get("name") or code
                rows.append(r)
    rows.sort(key=lambda x: x["radar"], reverse=True)
    ind_raw = await redis.get(MARKET_INDICATORS_KEY)
    try:
        regime = market_regime(_json.loads(ind_raw) if ind_raw else None)
    except (ValueError, TypeError):
        regime = market_regime(None)
    return {"rows": rows[:limit], "regime": regime, "scanned": len(pool)}


@router.get("/stocks/score")
async def stocks_score(limit: int = 200) -> dict:
    """투자 매력도 랭킹 — 가치·품질·모멘텀·타이밍 통합 0~100 + 판정. 전체시장∪관심.

    전체 시장 스코어링(3,600+종목)은 Pi에서 수 초 — 30초 캐시로 화면 지연 방지.
    """
    return await get_or_compute(f"stocks_score:{limit}", 30,
                                lambda: _score_build(limit))


async def _score_build(limit: int) -> dict:
    redis = get_redis()
    quotes = [q for q in await load_quotes(redis) if q.get("price") and q.get("code")]
    # 미국(재무 미확보): 가치·품질·성장 축이 0이라 점수가 '회피'로 왜곡 → 랭킹 제외.
    # (개별 조회는 상세 모달에서 추세·타이밍 참고용으로 표시. KIS 해외 재무 연동 시 포함 예정)
    quotes = [q for q in quotes
              if not (q.get("currency") == "USD" and q.get("eps") is None)]
    codes = [q["code"] for q in quotes]
    closes_map: dict[str, list] = {}
    if codes:
        async with redis.pipeline(transaction=False) as pipe:
            for c in codes:
                pipe.get(stock_ohlcv_key(c))
            raws = await pipe.execute()
        for c, raw in zip(codes, raws):
            if not raw:
                continue
            try:
                candles = _json.loads(raw)
                closes_map[c] = [x["close"] for x in candles
                                 if isinstance(x, dict) and x.get("close")]
            except (ValueError, TypeError):
                pass
    rows = [compute_score(q, closes_map.get(q["code"], [])) for q in quotes]
    rows.sort(key=lambda r: r["score"], reverse=True)
    return {"rows": rows[:limit], "total": len(rows)}


@router.get("/stocks/signals")
async def stocks_signals() -> dict:
    """관심종목 기술적 시그널(일봉 시계열 수집분 기준)."""
    redis = get_redis()
    rows = []
    for w in await effective_watchlist(redis):
        s = await signals_for(redis, w["code"], w.get("name", ""))
        if s:
            rows.append(s)
    order = {"buy": 0, "neutral": 1, "sell": 2}
    rows.sort(key=lambda r: (order.get(r["signal"], 1), -(r.get("score") or 0)))
    return {"rows": rows}


@router.get("/stocks/dividend")
async def stocks_dividend(monthly_budget: float = 0.0) -> dict:
    """배당수익률 랭킹 + (예산 지정 시) 정기 적립(DRIP) 제안. (전체 스캔 → 30초 캐시)"""
    return await get_or_compute(
        f"stocks_dividend:{monthly_budget}", 30,
        lambda: dividend_view(get_redis(), monthly_budget))


@router.get("/stocks/backtest/{code}")
async def stocks_backtest(code: str, strategy: str = "sma") -> dict:
    """저장된 일봉으로 전략 백테스트(sma|rsi|momentum). 룰 검증용(실매매 아님)."""
    if strategy not in STRATEGIES:
        raise HTTPException(400, f"전략은 {', '.join(STRATEGIES)} 중 하나")
    raw = await get_redis().get(stock_ohlcv_key(code))
    if not raw:
        raise HTTPException(404, "일봉 없음 — 수집 대기(KIS 키 필요)")
    candles = _json.loads(raw)
    closes = [c["close"] for c in candles if isinstance(c, dict) and c.get("close")]
    return {"code": code, **backtest(closes, strategy)}


_toss = TossClient()
_dart = DartClient()


async def _ondemand_candles(redis, code: str) -> list[dict]:
    """미수집 종목의 일봉을 토스에서 즉시 수집(6h 캐시) — 관심종목 아니어도 분석 가능."""
    if not _toss.enabled:
        return []
    try:
        async with httpx.AsyncClient(timeout=20) as tc:
            candles = await _toss.fetch_daily_history(tc, code)
        if candles:
            await redis.set(stock_ohlcv_key(code), _json.dumps(candles), ex=21600)
        return candles or []
    except Exception:
        return []


async def _ondemand_dividend(redis, code: str) -> list[dict]:
    """미수집 종목의 배당 3개년을 DART에서 즉시 수집 — 관심종목 아니어도 표시."""
    if not _dart.enabled:
        return []
    try:
        craw = await redis.get(DART_CORP_KEY)
        corp = (_json.loads(craw) if craw else {}).get(code)
        if not corp:
            return []
        year = _dt.date.today().year - 1
        async with httpx.AsyncClient(timeout=15) as dc:
            items = await _dart.fetch_dividend_years(dc, corp, year)
            if not items:
                items = await _dart.fetch_dividend_years(dc, corp, year - 1)
        if items:
            await redis.hset(STOCK_DIVIDEND_KEY, code, _json.dumps(
                {"code": code, "items": items, "src": "dart", "ts": _time.time()},
                ensure_ascii=False))
        return items or []
    except Exception:
        return []


@router.get("/stocks/{code}")
async def stock_detail(code: str) -> dict:
    """단일 종목 상세 — 관심종목이 아니어도 즉시 분석.

    펀더멘털은 stock:quote ∪ stock:market. 차트(기술분석·매매가이드)와 배당이
    미수집이면 토스/DART에서 온디맨드 수집 후 계산(캐시 저장).
    """
    redis = get_redis()
    code = code if code.isdigit() else code.upper()   # 미국 티커 대문자 통일
    # 두 해시 병합: market(분기실적·실적발표 배지 등) 위에 quote(실시간가) 덮기
    quote = {"code": code}
    for key in (STOCK_MARKET_KEY, STOCK_QUOTE_KEY):
        raw = await redis.hget(key, code)
        if raw:
            try:
                quote.update({k: v for k, v in json.loads(raw).items()
                              if v is not None})
            except (ValueError, TypeError):
                pass
    # 시세 self-heal: 저장된 현재가가 없거나 오래됐으면(수집 루프가 멈춰도) 토스에서
    # 즉시 다시 불러온다 — 상세 모달이 항상 '지금 가격'을 보이게(지앤씨 +25% 미반영 fix).
    price_ts = quote.get("ts")
    stale = (not quote.get("price")) or (not price_ts) or (_time.time() - price_ts > 60)
    if is_kr_code(code) and _toss.enabled and stale:
        try:
            async with httpx.AsyncClient(timeout=12) as tc:
                fresh = await _toss.fetch_prices(tc, [code])
            fp = next((p.get("price") for p in fresh if p.get("symbol") == code), None)
            if fp:
                prev = quote.get("prev_close")
                quote["price"] = fp
                if prev:
                    quote["change_pct"] = round((fp - prev) / prev * 100, 2)
                quote["ts"] = price_ts = _time.time()
                await redis.hset(STOCK_QUOTE_KEY, code,
                                 _json.dumps(quote, ensure_ascii=False))
        except Exception:
            pass                                    # 실패해도 저장분으로 진행
    # 일봉: 저장분 → 없으면 토스 온디맨드
    candles: list = []
    oraw = await redis.get(stock_ohlcv_key(code))
    if oraw:
        try:
            candles = _json.loads(oraw)
        except (ValueError, TypeError):
            candles = []
    if len(candles) < 20:
        candles = await _ondemand_candles(redis, code)
    closes = [c["close"] for c in candles if isinstance(c, dict) and c.get("close")]
    # 장중 실시간 반영: 오늘 캔들이 아직 없으면 현재가를 오늘 종가로 덧붙이고,
    # 있으면 마지막 종가를 실시간가로 교체 → 시그널·추세·매매가이드가 장중 가격 기준.
    live = quote.get("price")
    if live and closes:
        last_date = str(candles[-1].get("date", ""))[:10]
        if last_date == _dt.date.today().isoformat():
            closes[-1] = live
        else:
            closes = closes + [live]
    # 시세가 아직 없으면 일봉으로 보강(현재가·등락률·52주)
    if not quote.get("price") and closes:
        m = candle_metrics(candles)
        quote.update({"price": m.get("last_close"), "change_pct": m.get("change_pct"),
                      "high_52w": quote.get("high_52w") or m.get("high_52w"),
                      "low_52w": quote.get("low_52w") or m.get("low_52w")})
    kr = is_kr_code(code)
    if not kr:
        quote["currency"] = "USD"   # 미국 티커 — UI 통화 표시
        # 이름·주식수 온디맨드 보강(토스) → 시총 계산 + 이후 종목 탭 검색에 뜨도록 저장
        if not quote.get("name") and _toss.enabled:
            try:
                async with httpx.AsyncClient(timeout=15) as tc:
                    meta = (await _toss.fetch_stocks(tc, [code])).get(code) or {}
                if meta.get("name"):
                    quote["name"] = meta["name"]
                if meta.get("shares"):
                    quote["shares"] = meta["shares"]
            except Exception:
                pass
        if quote.get("price") and quote.get("shares") and not quote.get("market_cap"):
            quote["market_cap"] = round(quote["price"] * quote["shares"] / 1e8, 1)
        if quote.get("price"):   # 한 번 조회한 미국 종목은 목록·검색에 재등장
            await redis.hset(STOCK_QUOTE_KEY, code,
                             _json.dumps(quote, ensure_ascii=False))
    sig = ({"code": code, "name": quote.get("name", ""), **evaluate_signals(closes)}
           if len(closes) >= 20 else None)
    if sig is not None:
        sig["adx"] = adx(candles)   # 추세 강도(고가·저가 필요 — 캔들 원본 기준)
    score = compute_score(quote, closes)
    levels = trade_levels(closes, quote.get("price"), kr=kr)
    pillar = light_pillar(candles) if kr else None   # 수급 기준(억원)은 국내 전용
    # 배당: 저장분 → 없으면 DART 온디맨드(국내만 — 미국은 DART 미커버)
    div = None
    items: list = []
    if kr:
        draw = await redis.hget(STOCK_DIVIDEND_KEY, code)
        if draw:
            try:
                items = json.loads(draw).get("items", [])
            except (json.JSONDecodeError, TypeError):
                items = []
        if not items:
            items = await _ondemand_dividend(redis, code)
    if items:
        div = compute_dividend(quote, items)
    # 실적발표 시즌 배지: 국내=DART 잠정실적 공시, 미국=SEC 8-K/10-Q(수집분)
    flash = None
    if kr:
        from collector.news.dart import find_earnings_flash
        filings = []
        for item in await redis.lrange(DART_RECENT_KEY, 0, 100):
            try:
                filings.append(_json.loads(item))
            except (ValueError, TypeError):
                continue
        flash = find_earnings_flash(filings, code)
    else:
        flash = quote.get("earnings_flash")
    wl = await effective_watchlist(redis)
    return {"quote": quote, "signal": sig, "dividend": div, "score": score,
            "levels": levels, "pillar": pillar, "earnings_flash": flash,
            "price_ts": quote.get("ts"),
            "in_watchlist": any(w.get("code") == code for w in wl)}
