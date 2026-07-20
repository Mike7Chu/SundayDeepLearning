"""실시간 시세 스트림(SSE) — 대시보드가 폴링 없이 가격 변화를 즉시 수신.

수집기(KIS 웹소켓·토스 REST)가 갱신하는 stock:quote를 짧은 주기로 감시해
'변한 종목만' 이벤트로 push한다. 대상은 관심종목∪보유(수십 개 — hget 저비용).
EventSource 재접속은 브라우저가 자동 처리. 전체 화면 데이터는 기존 12초
폴링이 계속 담당하고, 이 스트림은 가격·등락률 셀만 실시간으로 덧칠한다.
"""
from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from api.redis_client import get_redis
from collector.stock.kis import effective_watchlist
from shared.redis_keys import STOCK_QUOTE_KEY, TOSS_HOLDINGS_KEY
from shared.settings import settings

router = APIRouter()


def pick_stream_fields(rec: dict) -> tuple | None:
    """quote 레코드 → 전송 필드(순수). 가격 없으면 None."""
    p = rec.get("price")
    if p is None:
        return None
    return (p, rec.get("change_pct"), rec.get("currency", "KRW"))


async def _codes(redis) -> list[str]:
    codes = {w["code"] for w in await effective_watchlist(redis)}
    try:
        raw = await redis.get(TOSS_HOLDINGS_KEY)
        if raw:
            codes |= {h["symbol"] for h in json.loads(raw).get("holdings", [])
                      if h.get("symbol")}
    except (json.JSONDecodeError, TypeError):
        pass
    return sorted(codes)


@router.get("/stream")
async def stream(request: Request) -> StreamingResponse:
    """SSE: data 이벤트 = {code: {p:가격, c:등락률, cur:통화}} (변경분만)."""
    async def gen():
        redis = get_redis()
        last: dict[str, tuple] = {}
        last_send = time.monotonic()
        while True:
            if await request.is_disconnected():
                return
            out = {}
            try:
                for code in await _codes(redis):
                    raw = await redis.hget(STOCK_QUOTE_KEY, code)
                    if not raw:
                        continue
                    try:
                        rec = json.loads(raw)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    cur = pick_stream_fields(rec)
                    if cur is not None and last.get(code) != cur:
                        last[code] = cur
                        out[code] = {"p": cur[0], "c": cur[1], "cur": cur[2]}
            except Exception:
                out = {}                      # 일시 오류는 다음 주기에 복구
            now = time.monotonic()
            if out:
                yield f"data: {json.dumps(out, ensure_ascii=False)}\n\n"
                last_send = now
            elif now - last_send > 15:
                yield ": keepalive\n\n"        # 프록시 타임아웃 방지
                last_send = now
            await asyncio.sleep(settings.stream_interval_sec)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
        "Connection": "keep-alive"})
