"""FastAPI 엔트리포인트.

실행: uvicorn api.main:app --port 8000
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.redis_client import close_redis
from api.routers import premium


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


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
