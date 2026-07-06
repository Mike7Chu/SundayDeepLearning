"""환경설정. .env 또는 환경변수에서 로드."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # 텔레그램 (브리핑/알림 발송)
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # 자산 목표(원) — 홈 대시보드 진행률 바
    target_asset_krw: float = 10_000_000_000  # 100억

    # DART 전자공시 (opendart.fss.or.kr, 무료 키). 없으면 공시 수집 비활성
    dart_api_key: str = ""
    dart_interval_sec: float = 30.0          # 공시 폴링 주기(속도)
    dart_watch_all: bool = False             # True=전 종목 공시, False=관심/유니버스만

    # 전체 시장 스크리너: 유니버스 펀더멘털 수집(배치·느린 주기, KIS 레이트리밋 대비)
    market_scan_interval_sec: float = 1800.0  # 유니버스 1바퀴 목표 주기(KIS 펀더멘털·수급 감지)
    market_batch: int = 60                     # 사이클당 조회 종목 수
    market_universe_max: int = 4000            # 유니버스 상한(코스피+코스닥 전 종목 커버)
    market_price_interval_sec: float = 300.0   # 유니버스 전체 가격 스윕(토스 200종목/콜)

    # 한국투자증권(KIS) — 키 없으면 주식 수집 비활성
    kis_app_key: str = ""
    kis_app_secret: str = ""
    kis_account: str = ""
    kis_paper: bool = True               # True=모의투자 도메인
    # 시세/일봉/배당은 조회 전용이라 실전 도메인이 완전(예탁원 배당 등은 모의도메인 미제공).
    # 이 앱은 KIS를 조회로만 쓰므로(매매=토스) 기본 True=실전 도메인 조회. 모의 앱키만
    # 있으면 False로. True면 kis_paper와 무관하게 조회를 실전 도메인으로.
    kis_quote_real: bool = True
    kis_rate_per_sec: float = 5.0        # KIS 조회 초당 요청 상한(버스트 500/연결차단 방지, 전 루프 공유)
    stock_interval_sec: float = 15.0
    stock_history_interval_sec: float = 21600.0   # 일봉/배당 수집 주기(기본 6시간)

    # 토스증권 Open API — 실보유(잔고)·매수여력·실주문. 키 없으면 포트폴리오 비활성
    toss_client_id: str = ""
    toss_client_secret: str = ""
    toss_account_seq: str = ""            # 빈값이면 /accounts로 대표계좌 자동탐색
    toss_interval_sec: float = 30.0       # 보유/잔고 수집 주기
    toss_trading_enabled: bool = False    # 실주문 하드 게이트(기본 잠금). True라야 주문 허용
    toss_max_order_krw: float = 100_000.0  # 주문당 안전 상한(소액 실전)

    # 텔레그램 일일 브리핑(주식 시세·시그널·가치·배당 요약). 키 없으면 로그만
    briefing_interval_sec: float = 86400.0        # 브리핑 주기(기본 1일)
    briefing_drip_budget: float = 0.0             # 배당 정기적립 월예산(원). 0=미사용

    # ===== 매매 엔진(멍거 리스크 실드) — 1시간 주기 검증. 실주문은 별도 게이트 =====
    engine_interval_sec: float = 600.0    # 잔고·리스크·시그널 점검 주기(기본 10분 — 준실시간 알림)
    mdd_limit_pct: float = 15.0           # 최고점 대비 -15% → BUY_LOCK(서킷 브레이커)
    max_stock_pct: float = 5.0            # 단일 종목 최대 매수금액 = 자산의 5%
    cash_floor_pct: float = 25.0          # 현금 비중 25% 미만이면 매수 시그널 무시
    buy_score_min: float = 70.0           # 2단계 필터 최종 점수 컷(이상만 매수 리스트)
    inversion_max_per_cycle: int = 5      # 사이클당 AI 역방향 분석 요청 상한(토큰 절약)
    inversion_fresh_sec: float = 604800.0  # 역방향 감점 유효기간(기본 1주 — 리서치 주기와 동일, 토큰 절약)
    # 자동매매(기본 잠금): true + 해당 브로커 실매매 플래그 둘 다 켜야 동작.
    # 브로커 분리: 자동매매=한투(KIS), 수동=토스 앱 — auto_trade_broker로 선택.
    auto_trade_enabled: bool = False
    auto_trade_broker: str = "kis"              # kis | toss
    auto_trade_cooldown_sec: float = 604800.0   # 같은 종목 자동 재매수 금지 기간(7일)
    # 한투(KIS) 주문 게이트 — kis_paper=true면 모의투자 주문(리허설), false면 실전
    kis_trading_enabled: bool = False
    kis_max_order_krw: float = 100_000.0        # 한투 주문당 안전 상한

    # AI 가치투자 리서치 (Addendum 9) — 키 없으면 비활성(idle)
    anthropic_api_key: str = ""
    research_model: str = "claude-opus-4-8"
    research_interval_sec: float = 604800.0  # 관심종목 정기 분석 주기(기본 1주 — 토큰 절약)
    # 구독(무과금) 경로: API 키 대신 Claude Code CLI(헤드리스) 사용. 키 없을 때만 적용.
    research_use_cli: bool = False           # True+claude 바이너리 존재 시 구독 CLI로 분석
    research_cli_bin: str = "claude"         # Claude Code 실행 파일명/경로


settings = Settings()
