"""FastAPI 엔트리포인트.

실행: uvicorn api.main:app --port 8000
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from api.redis_client import close_redis
from api.routers import bots, premium

_WEB_DIR = Path(__file__).resolve().parent.parent / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await close_redis()


app = FastAPI(title="Trading Platform API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 로컬/내부망 전용. 공개 시 좁힐 것.
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(premium.router)
app.include_router(bots.router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
async def dashboard() -> FileResponse:
    """김프 대시보드(단일 페이지)."""
    return FileResponse(_WEB_DIR / "index.html")
