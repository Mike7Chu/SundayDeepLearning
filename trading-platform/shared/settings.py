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
    dashboard_url: str = ""      # 대시보드 주소(예 http://100.x.x.x:8090) — 텔레그램 '열기' 버튼용

    # 자산 목표(원) — 홈 대시보드 진행률 바
    target_asset_krw: float = 10_000_000_000  # 100억

    # DART 전자공시 (opendart.fss.or.kr, 무료 키). 없으면 공시 수집 비활성
    dart_api_key: str = ""
    dart_interval_sec: float = 30.0          # 공시 폴링 주기(속도)
    dart_watch_all: bool = False             # True=전 종목 공시, False=관심/유니버스만
    dart_value_cap: int = 250                # 재무 수집 대상(가치 상위 top N) — 무료 한도 절약
    alert_cooldown_sec: float = 21600.0      # 보유 손절/익절 알림 종목당 최소 간격(스팸 억제, 6h)
    auto_retry_sec: float = 1800.0           # 자동매수 '실패' 후 재시도 대기(성공은 쿨다운 7일)
    entry_chase_band_pct: float = 4.0        # 추천가 대비 이 %내면 현재가 매수, 초과면 눌림목 대기(추격 방지)
    # 강세추세(공격) 국면 전용 추격 밴드 — 주도주가 SMA20까지 잘 안 눌리므로 넓혀 강세 진입 허용.
    # (다른 국면은 entry_chase_band_pct 유지 — 약세·횡보엔 추격 손실이 커 보수적으로.)
    entry_chase_band_bull_pct: float = 10.0
    # 매매 비용(성적 순손익 계산 — 모의가 실전을 속이지 않게). 단위 %.
    kr_sell_tax_pct: float = 0.18            # 국내 증권거래세+농특세(매도만)
    us_sell_fee_pct: float = 0.001           # 미국 SEC/TAF 수수료(매도만, 미미)
    brokerage_pct: float = 0.015             # 위탁수수료(매수·매도 양방향, 온라인 대략)
    slippage_pct: float = 0.10               # 슬리피지 추정(양방향) — 초단타일수록 치명적
    # 데이 스윙(Phase B) — 분~시간 보유. 기본 OFF(옵트인). 거래 잦아 net으로 검증 필수.
    day_trade_enabled: bool = False
    day_trade_interval_sec: float = 45.0     # 데이 루프 주기
    intraday_bar_sec: int = 60               # 분봉 버킷(초)
    day_max_positions: int = 3               # 동시 데이 포지션 상한
    # 단타 품질 게이트: 동전주/저가 펌핑주 진입 금지(딥노이드·썸에이지류 차단),
    # 위험회피장(regime=risk_off)이면 신규 진입 정지(가짜 돌파 되돌림 회피). 청산은 무관.
    day_min_price_krw: float = 3000.0        # 단타 최소 진입가(동전주 제외)
    day_skip_risk_off: bool = True           # 위험회피장이면 단타 신규진입 스탠드다운
    # 초단타 강등(리테일 인트라데이는 비용 후 음의 엣지 — 데이터상 72~97% 손실). 신규진입은
    # 기본 OFF(청산은 항상 작동). 되살리려면 INTRADAY_ENTRY_ENABLED=true로 명시 옵트인.
    intraday_entry_enabled: bool = False
    # 거래강도(vsurge) 필터: v는 폴링 기반 갱신횟수 프록시라 고정 주기면 상수 → 항상 False가
    # 되어 진입을 봉쇄. 실거래량 피드 붙기 전엔 OFF 유지(진입은 정배열+양봉만으로 판정).
    day_require_vsurge: bool = False
    day_trade_take_pct: float = 1.5          # 데이 익절 목표(%)
    day_trade_stop_pct: float = 1.0          # 데이 손절(%)
    # 초단타 실험(Phase C) — 실전 영구 금지·모의 전용. 기본 OFF.
    scalp_experiment: bool = False
    scalp_interval_sec: float = 10.0
    # 클로드 스윙 결정 에이전트 — 하루 N회 클로드가 최종 BUY/SELL/HOLD 판정(완전 위임).
    # 기본 OFF·모의 전용(agent_live_enabled=true라야 실계좌 주문). 매수=후보 목록/매도=보유만.
    agent_enabled: bool = False
    agent_times: str = "09:40,23:40"         # 판정 시각(KST): 09:40=국장 · 23:40=미장
    agent_live_enabled: bool = False         # true라야 실계좌 주문(기본 모의 전용 잠금)
    agent_max_buys: int = 3                  # 1회 실행당 신규 매수 상한
    agent_check_interval_sec: float = 300.0  # 스케줄 확인 주기(초)
    # 승인 모드: true면 에이전트가 매수를 '자동 체결'하지 않고, 분석 근거+진입/손절/목표를
    # 담은 '매수 제안 알림 + 승인 버튼'을 보낸다. 사용자가 ✅ 승인해야 체결(손절 등록).
    # 매도는 방어라 종전대로 자동. 완전위임이 부담스러운 사용자용(사람이 최종 방아쇠).
    agent_approval_mode: bool = False
    # 토큰 절약: 같은 후보를 이 기간 안엔 다시 제안/판정하지 않는다(중복 Claude 호출 방지).
    # 실행 가능한 후보(미보유·쿨다운밖·미제안)가 없고 보유도 없으면 decide() 자체를 생략.
    agent_propose_cooldown_sec: float = 21600.0   # 제안 재호출 억제(기본 6시간)
    # 빛의기둥 → 에이전트 검토: 빛의기둥이 뜨면 싼 1차 필터(추세·거래대금·중복)를 거쳐
    # 통과분만 Claude가 검토 → 매수 합당 판정이면 승인 제안 알림. 필터로 대부분 걸러
    # Claude 호출은 소수만(토큰 절약). 기본 OFF(옵트인).
    pillar_agent_review: bool = False
    pillar_review_min_eok: float = 50.0           # 이 거래대금(억) 이상만 Claude 검토
    pillar_review_max_per_cycle: int = 3          # 사이클당 Claude 검토 상한(토큰 바운드)
    # 플랜 매수 후보를 국내·미국 각각 최소 확보(한쪽 슬롯이 빈손이 안 되게)
    plan_kr_buys: int = 2                     # 플랜에 담을 국내 후보 수(스윙 상위)
    plan_us_buys: int = 2                     # 플랜에 담을 미국 후보 수(스윙 상위)
    # 확신 하한(0~100): 스윙 점수가 이 아래면 매수 후보에서 제외 → 슬롯 강제충전 대신
    # 현금 보유. 약세장에 '점수 39·관망'짜리를 억지로 사던 손실을 막는다(전략 리포트 P0).
    plan_min_swing: float = 50.0

    # 전체 시장 스크리너: 유니버스 펀더멘털 수집(배치·느린 주기, KIS 레이트리밋 대비)
    market_scan_interval_sec: float = 1800.0  # 유니버스 1바퀴 목표 주기(KIS 펀더멘털·수급 감지)
    market_batch: int = 60                     # 사이클당 조회 종목 수
    market_universe_max: int = 4000            # 유니버스 상한(코스피+코스닥 전 종목 커버)
    market_price_interval_sec: float = 300.0   # 유니버스 전체 가격 스윕(토스 200종목/콜)

    # 한국투자증권(KIS) — 키 없으면 주식 수집 비활성
    kis_app_key: str = ""                # 주문 계좌용 앱키(모의계좌면 모의 앱키)
    kis_app_secret: str = ""
    # 실전 조회 전용 앱키(선택) — 있으면 시세·재무·해외를 실전 도메인(안정)으로.
    # 모의 앱키로 주문 + 실전 앱키로 조회를 '둘 다' 쓰는 이상적 구성. 없으면 kis_app_key로.
    kis_real_app_key: str = ""
    kis_real_app_secret: str = ""
    kis_account: str = ""
    kis_paper: bool = True               # True=모의투자 도메인(주문)
    # 시세/일봉/배당은 조회 전용이라 실전 도메인이 완전(예탁원 배당 등은 모의도메인 미제공).
    # 이 앱은 KIS를 조회로만 쓰므로(매매=토스) 기본 True=실전 도메인 조회. 모의 앱키만
    # 있으면 False로. True면 kis_paper와 무관하게 조회를 실전 도메인으로.
    kis_quote_real: bool = True
    kis_rate_per_sec: float = 5.0        # KIS 조회 초당 요청 상한(버스트 500/연결차단 방지, 전 루프 공유)
    stock_interval_sec: float = 15.0
    # 실시간(웹소켓): 장중 체결가 즉시 반영(관심∪보유, 연결당 41종목). 끄면 REST 폴링만.
    kis_ws_enabled: bool = True
    stream_interval_sec: float = 2.0     # 대시보드 SSE 푸시 주기(변경분만 전송)
    guard_interval_sec: float = 20.0     # 엔진 고속 가드(목표가/손절 감시) 주기
    stock_history_interval_sec: float = 21600.0   # 일봉/배당 수집 주기(기본 6시간)

    # 토스증권 Open API — 실보유(잔고)·매수여력·실주문. 키 없으면 포트폴리오 비활성
    toss_client_id: str = ""
    toss_client_secret: str = ""
    toss_account_seq: str = ""            # 빈값이면 /accounts로 대표계좌 자동탐색
    toss_interval_sec: float = 30.0       # 보유/잔고 수집 주기
    toss_trading_enabled: bool = False    # 실주문 하드 게이트(기본 잠금). True라야 주문 허용
    toss_max_order_krw: float = 100_000.0  # 주문당 안전 상한(소액 실전)
    # 레이트리밋 방어(전 루프 공유): 요청 간 최소 간격 + 한도초과 시 백오프 재시도.
    # 여러 수집 루프가 동시에 토스를 두들겨 rate-limit-exceeded 나던 문제 대응.
    toss_min_interval_sec: float = 0.3    # 요청 사이 최소 간격(≈3req/s)
    toss_max_retry: int = 4               # rate-limit/invalid-token 시 재시도 횟수

    # 텔레그램 일일 브리핑(주식 시세·시그널·가치·배당 요약). 키 없으면 로그만
    briefing_interval_sec: float = 86400.0        # (구) 브리핑 주기 — 스케줄 방식으로 대체
    briefing_hour_kst: int = 16                   # 일일 브리핑 발송 시각(KST, 장마감 후 요약)
    briefing_check_sec: float = 1800.0            # 스케줄 확인 주기(초)
    briefing_stale_hours: float = 12.0            # 시세가 이보다 오래면 발송 보류(재시작 직후 방어)
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
    # 수급 확인 게이트: 외인+기관 5일 순매도가 이 값(억)을 넘으면 자동매수 보류.
    auto_supply_block_eok: float = 20.0
    # 매도 규율: 트레일링 스탑 폭(고점 대비 %) — '손실 짧게 이익 길게'.
    trail_stop_pct: float = 10.0
    # 한투(KIS) 주문 게이트 — kis_paper=true면 모의투자 주문(리허설), false면 실전
    kis_trading_enabled: bool = False
    kis_max_order_krw: float = 100_000.0        # 한투 주문당 안전 상한
    # 미장 자동매매(KIS 해외주식 주문 — 모의 지원): 기본 잠금. 국내(가치)와 별개 전략(모멘텀).
    us_auto_enabled: bool = False
    # 국장 스윙 자동매매(옵트인): 스윙 플랜 국내 후보를 매 사이클 눌림목에 자동매수.
    # 미장은 _auto_buy_us가 상시 집행하는데 국내 스윙은 하루 1회 에이전트(AGENT_TIMES)에만
    # 의존했던 공백을 메운다. 2단계 가치 자동매수(_auto_buy)와 별개 — 쿨다운이 중복 매수 방지.
    kr_swing_auto_enabled: bool = False
    # 거래소 오분류 교정 맵(모의 테스트에서 거부되면 .env로 추가): "PLTR:NASD,SNOW:NYSE"
    kis_us_exchange_map: str = ""

    # AI 가치투자 리서치 (Addendum 9) — 키 없으면 비활성(idle)
    anthropic_api_key: str = ""
    research_model: str = "claude-opus-4-8"
    research_interval_sec: float = 604800.0  # 관심종목 정기 분석 주기(기본 1주 — 토큰 절약)
    # 관심종목 정기 AI 리서치 '자동 패스' — 기본 OFF(수동 전용). 켜면 호스트가 주기적으로
    # 전 관심종목을 자동 분석(토큰 소모). 꺼도 대시보드 🧠 '다시 분석'·텔레그램 온디맨드는 동작.
    research_auto_pass_enabled: bool = False
    # 뉴스 감성 백그라운드 패스 — stage1(1차분류)·관심종목의 DART+구글뉴스 감성을
    # 주기적으로 채워 점수 '뉴스' 축에 반영. 규칙기반이라 토큰 0(외부 RSS만). 국내 전용.
    news_sent_enabled: bool = True
    news_sent_interval_sec: float = 1800.0   # 감성 패스 주기(기본 30분)
    news_sent_max: int = 30                  # 1회 크롤 상한(rate-limit·부하 방지)
    # 구독(무과금) 경로: API 키 대신 Claude Code CLI(헤드리스) 사용. 키 없을 때만 적용.
    research_use_cli: bool = False           # True+claude 바이너리 존재 시 구독 CLI로 분석
    research_cli_bin: str = "claude"         # Claude Code 실행 파일명/경로

    # ADR 괴리율 추적: "본주코드:후보티커1|후보티커2:비율(1 ADR당 본주 수)".
    # 토스 앱에서 보이는 실제 ADR 티커로 맞추면 아침 점검에 괴리율이 표시됨.
    # SK하이닉스 ADR 후보(SKHY/SKHYY)를 기본 포함 — 토스가 잡히는 티커를 자동 채택.
    # 비율은 본주가 대비 ADR$×환율이 맞도록 조정(예: ADR이 본주의 절반 가치면 0.5).
    adr_map: str = "000660:SKHY|SKHYY|SKH:1"
    adr_interval_sec: float = 1800.0         # ADR 시세 갱신 주기(30분)

    # SEC EDGAR(미국 공시 — 무료·키 불필요): 미장 분기 실적·실적발표 감지.
    # SEC 정책상 UA에 연락처 표기 권장 — 본인 이메일로 바꾸면 좋음.
    sec_user_agent: str = "StockLab/1.0 (personal research)"
    sec_interval_sec: float = 86400.0        # 미장 분기실적 수집 주기(하루 1회)

    # AI 포트폴리오 코치(아침 점검) — 실보유 비중 기준 종목별 '보유/일부매도/위험신호' 판정.
    # 매일 1회(코치 시각, KST) 텔레그램 발송 + 대시보드 홈 카드. 리서치 백엔드 공유(하루 1콜).
    coach_enabled: bool = True
    coach_hour_kst: int = 8                  # 아침 점검 시각(KST, 0~23)


settings = Settings()
