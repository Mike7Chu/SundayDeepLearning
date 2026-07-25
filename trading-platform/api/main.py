"""FastAPI 엔트리포인트 (주식 플랫폼).

실행: uvicorn api.main:app --port 8000
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from api.redis_client import close_redis
from api.routers import (coach, engine, journal, market, news, portfolio,
                         research, stocks, stream, validation, watchlist)

_WEB_DIR = Path(__file__).resolve().parent.parent / "web"
logger = logging.getLogger("api")


async def _cache_warmer() -> None:
    """무거운 전체스캔 엔드포인트를 백그라운드로 미리 데워, '첫 열림 지연'을 없앤다.

    캐시는 프로세스 메모리라 재배포·재시작마다 콜드 → 첫 사용자가 3,600종목 스캔을
    기다렸다. 시작 직후 + 25초마다 미리 계산해두면(SWR과 결합) 탭 첫 진입이 즉시.
    각 항목은 독립 try — 하나 실패해도 나머지 계속(레이트리밋·네트워크 방어).
    """
    await asyncio.sleep(3)                         # redis·수집 초기화 잠깐 대기
    warmers = [
        ("전체종목", lambda: stocks.stocks_all()),
        ("살까말까·저평가", lambda: stocks.stocks_value(200)),
        ("살까말까·점수", lambda: stocks.stocks_score(200)),
        ("살까말까·레이더", lambda: stocks.stocks_radar(12)),
        ("배당금", lambda: stocks.stocks_dividend(0.0)),
        ("내종목", lambda: portfolio.portfolio()),
        ("모의투자잔고", lambda: portfolio.paper_account()),
    ]
    while True:
        for label, fn in warmers:
            try:
                await fn()
            except Exception as exc:               # noqa: BLE001 — 워머는 절대 안 죽어야
                logger.info("[warm] %s 예열 실패(무시): %s", label, exc)
        await asyncio.sleep(25)                     # 캐시 TTL(12~30s)보다 촘촘히 유지


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_cache_warmer())    # 시작 시 캐시 예열 루프
    yield
    task.cancel()
    await close_redis()


app = FastAPI(title="Stock Platform API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 로컬/내부망 전용. 공개 시 좁힐 것.
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(stocks.router)
app.include_router(research.router)
app.include_router(news.router)
app.include_router(portfolio.router)
app.include_router(watchlist.router)
app.include_router(engine.router)
app.include_router(coach.router)
app.include_router(market.router)
app.include_router(validation.router)
app.include_router(stream.router)
app.include_router(journal.router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
async def dashboard() -> FileResponse:
    """주식 대시보드(단일 페이지)."""
    return FileResponse(_WEB_DIR / "index.html")
