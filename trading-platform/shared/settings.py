"""환경설정. .env 또는 환경변수에서 로드."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # 수집 주기
    collect_interval_sec: float = 5.0
    fx_interval_sec: float = 300.0
    fx_usdkrw_fallback: float = 1380.0
    wallet_interval_sec: float = 300.0   # 입출금 상태(느린 변화)

    # 김프 이상치 가드: |김프%| 이 값 초과면 심볼 충돌/오류로 보고 제외
    premium_sanity_max_pct: float = 80.0

    # 텔레그램 (Phase 2)
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""


settings = Settings()
