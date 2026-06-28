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

    # 거래소 마켓 메타 재로딩 주기(초). 상폐/신규상장 반영(stale 마켓 캐시 제거)
    markets_reload_sec: float = 3600.0
    # 펀딩비 수집 주기(초). 펀비는 정산주기(시간) 단위로 변하므로 시세보다 느리게
    funding_interval_sec: float = 60.0
    # 펀비 bulk 미지원 거래소(예: MEXC) 단건 폴백 시 심볼 수 상한(부하 방지)
    funding_single_cap: int = 250

    # 텔레그램 (Phase 2)
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # 한국투자증권(KIS) — 키 없으면 주식 수집 비활성
    kis_app_key: str = ""
    kis_app_secret: str = ""
    kis_account: str = ""
    kis_paper: bool = True               # True=모의투자 도메인
    stock_interval_sec: float = 15.0
    stock_history_interval_sec: float = 21600.0   # 일봉/배당 수집 주기(기본 6시간)

    # 텔레그램 일일 브리핑(주식 시세·시그널·가치·배당 요약). 키 없으면 로그만
    briefing_interval_sec: float = 86400.0        # 브리핑 주기(기본 1일)
    briefing_drip_budget: float = 0.0             # 배당 정기적립 월예산(원). 0=미사용

    # AI 가치투자 리서치 (Addendum 9) — 키 없으면 비활성(idle)
    anthropic_api_key: str = ""
    research_model: str = "claude-opus-4-8"
    research_interval_sec: float = 86400.0   # 관심종목 정기 분석 주기(기본 1일)
    # 구독(무과금) 경로: API 키 대신 Claude Code CLI(헤드리스) 사용. 키 없을 때만 적용.
    research_use_cli: bool = False           # True+claude 바이너리 존재 시 구독 CLI로 분석
    research_cli_bin: str = "claude"         # Claude Code 실행 파일명/경로


settings = Settings()
