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

    # 아비트라지: 코인별 가격점 중앙값 대비 이 배수 밖이면 이상치(충돌/dust)로 제외
    arb_outlier_factor: float = 3.0

    # 텔레그램 (Phase 2)
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # 한국투자증권(KIS) — 키 없으면 주식 수집 비활성
    kis_app_key: str = ""
    kis_app_secret: str = ""
    kis_account: str = ""
    kis_paper: bool = True               # True=모의투자 도메인
    stock_interval_sec: float = 15.0

    # AI 가치투자 리서치 (Addendum 9) — 키 없으면 비활성(idle)
    anthropic_api_key: str = ""
    research_model: str = "claude-opus-4-8"
    research_interval_sec: float = 86400.0   # 관심종목 정기 분석 주기(기본 1일)
    # 구독(무과금) 경로: API 키 대신 Claude Code CLI(헤드리스) 사용. 키 없을 때만 적용.
    research_use_cli: bool = False           # True+claude 바이너리 존재 시 구독 CLI로 분석
    research_cli_bin: str = "claude"         # Claude Code 실행 파일명/경로


settings = Settings()
