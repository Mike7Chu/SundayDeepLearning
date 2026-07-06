# PROGRESS — 진행 요약 / 결정 로그

> 무엇을 왜 했는지의 시간순 기록. 새 세션은 여기서 "지금까지"를 빠르게 파악.

## 현재 한 줄 상태
**🔀 주식 올인 피벗(목표 100억)** — 코인 아비트라지 축 전면 제거, 주식 전용 플랫폼으로 재편.
남은 것: KIS 수집(시세/일봉/배당) · 가치(마법공식)/시그널/배당 스크리너 · 백테스트 · AI 4대 거장 리서치 · 일일 브리핑.
대시보드 7탭(홈·종목·가치·시그널·배당·리서치·설정), 홈에 100억 진행률. 테스트 23/23 통과(주식만).

### Phase 2 — 전체 시장 스크리너 + 뉴스·공시(DART) (완료, Pi 검증 필요)
- **전체 시장 스크리너**: `collector/stock/kis_master.parse_mst`/`fetch_universe`로 KIS 종목마스터→`stock:universe`, `collector.market_loop`이 배치(60/사이클)로 펀더멘털→`stock:market`. `stock_value.load_quotes`가 market∪quote 병합, `/stocks/value?limit=200` 전체시장 마법공식 랭킹.
- **뉴스·공시(DART)**: `collector/news/dart.py`(`parse_disclosure_list` 순수·`DartClient`)+`main.py`(30s 폴링, 신규 rcept_no 선별, silent prime, `dart:recent`+텔레그램). `/news`, docker `dart` 서비스. 대시보드 **공시 탭**+홈 최근공시 카드. `DART_API_KEY`(무료) 없으면 idle.
- 설정: `dart_api_key/interval/watch_all`, `market_scan_interval/batch/universe_max`. 테스트 27/27.
- ⚠️ KIS .mst 오프셋·DART 응답 스키마는 Pi에서 실제 데이터로 검증·튜닝(클라우드 403).

### 🔀 주식 피벗 (Phase 1 완료)
- 코인 삭제: `bots/·collector/exchanges/·forex·notifier(coin)·api coin routers/services·shared coin·코인 config/테스트`. `notifier/telegram.py`만 유지(리서치·브리핑 공용).
- 재작성: `collector/main.py`(주식 루프만)·`api/main.py`(stocks+research)·`docker-compose.yml`(redis/collector/api/research/briefing)·`shared/{redis_keys,settings}`(주식만)·`web/index.html`(주식 SPA 전면 재구성).
- 새 UI: 홈 대시보드(100억 진행률·시장온도·오늘의 시그널/가치/배당 하이라이트) + 종목/가치/시그널/배당/리서치/설정 탭 + 종목 상세 모달(펀더멘털·시그널·배당·백테스트·AI분석).
- 로드맵: Phase2 관심종목 UI관리·전체시장 스크리너·뉴스/공시(DART), Phase3 포트폴리오(KIS 잔고)·목표 트래킹, Phase4 자동매매 게이트.
- requirements에서 `ccxt` 제거. 코인 코드는 git 이력으로 복구 가능.

### Addendum 9 — AI 가치투자 리서치 (완료)
- `research/lenses.py`: 버핏·멍거·돤융핑·리루 4대 거장 렌즈(렌즈별 focus+체크리스트) → `SYSTEM_PROMPT`(출력형식+면책).
- `research/data.py`: Redis `stock:quote`(현재가+per/pbr/eps/bps)에서 `StockData` 수집, `format_for_prompt`(미상 처리).
- `research/analyst.py`: 백엔드 2종 — **api**(`AsyncAnthropic` 지연 import, 스트리밍+적응형 사고, 종량과금) / **cli**(`RESEARCH_USE_CLI=true`+`claude` 설치 시 `claude -p` 헤드리스 = **구독 무과금**). 모델 `claude-opus-4-8`. 둘 다 없으면 `mode=None`·비활성. `deploy/set-anthropic.sh`로 모드 설정.
- 결정: **Claude 구독 ≠ API 무료**(별도 결제). 추가과금 없이 구독 활용하려면 Claude Code CLI(`cli` 모드) 경유 — research를 호스트에서 `deploy/run-research-host.sh`로 구동(컨테이너는 호스트 로그인 못 봄). console API 키는 종량과금. 구독으로 무과금 API 키 발급은 불가.

### 아비트라지 순스프레드 (완료 — 수수료·전송 반영)
- `config/fees.yaml`(거래소 taker %) + `shared/fees.py`(로더, 미정의=default). `settings.arb_transfer_buffer_pct`(전송/슬리피지 버퍼).
- `compute_arbitrage`: `net_gap_pct = gap_pct - taker(long) - taker(short) - buffer`, `cost_pct` 포함. 대시보드 카드에 "순 X% (수수료 Y%)" + "순스프 ≥" 필터.
- Phase 2의 "수수료·전송비 반영 순스프레드" 완료. 본인 등급 수수료로 fees.yaml 수정 권장.

### 백테스트 하버스 (완료 — 룰 검증용, 실매매 아님)
- `backtest/engine.py`: 전략(sma 크로스·rsi 평균회귀·momentum)이 종가→포지션(0/1) 생성(룩어헤드 없음, closes[:i+1]만). `run_backtest`로 전략수익/매수후보유/매매수/승률/MDD 산출(수수료 1차 제외).
- `/stocks/backtest/{code}?strategy=sma|rsi|momentum`, 대시보드 시그널뷰 백테스트 버튼(3전략 동시).

### 텔레그램 명령 제어 (완료 — 컨트롤 플레인 단일 진실원)
- `notifier/commands.py`(순수 `handle`)·`command_main.py`(getUpdates 롱폴, 소유자 chat만). docker `commander`.
- `/status /bots /bot start|stop <name> /killswitch on|off /mute /unmute /alerts /brief`. 봇 enable/killswitch는 Redis 플래그(대시보드와 동일), /mute·/unmute는 alert_settings.enabled, /brief는 briefing.run_once.
- 실주문 없음(페이퍼봇 토글만). 봇 목록 단일화: `bots/registry.REGISTERED_BOTS`.

### 주식 전략 3종 + 일일 브리핑 (Phase 5, 완료 — 모니터링 전용/실주문 없음)
- **가치 스크리너** `api/services/stock_value.py`: 이익수익률(1/PER)·ROE(EPS/BPS) 마법공식 랭킹 + 간이 품질점수. `/stocks/value`.
- **시그널 엔진** `api/services/stock_signal.py`: SMA20/60 골든·데드크로스, RSI(14), 모멘텀(60), 볼린저 위치 → 종합 buy/sell/neutral. 일봉 `stock:ohlcv:{code}` 기반. `/stocks/signals`.
- **배당** `api/services/stock_dividend.py`: 배당수익률·다음 기준일·연배당 + 월예산 DRIP 균등배분. `/stocks/dividend?monthly_budget=`.
- **데이터 수집**: `collector.stock_history_loop`(6h) — KIS `inquire-daily-itemchartprice`(일봉)·`ksdinfo/dividend`(배당). 파서 `parse_daily`/`parse_dividend`. 키 없으면 비활성.
- **일일 브리핑** `briefing/`: `compose.compose_brief`(순수)로 시세 TOP·시그널·가치상위·배당상위·DRIP 요약 → 텔레그램(`briefing.main`, `briefing_interval_sec`). docker `briefing` 서비스. 면책 포함.
- 대시보드 주식탭: 보기 전환(시세/가치/시그널/배당). 테스트 52 passed.

### 정합성·마진·펀비 보강 (완료)
- **상폐 stale 근본수정**: 수집기가 장기구동이라 ccxt 마켓 캐시가 굳어 상폐 코인(bybit 등)이 잔존 → `adapter`/`perp`에 `load_markets(reload=True)` 주기 새로고침(`markets_reload_sec`=1h).
- **현물 마진**: spot `market['margin']` → `TickerSnapshot.margin` 수집. 아비 현물 다리에 `margin` 첨부. 대시보드 "현물숏 마진만"(기본 ON): 숏 다리가 현물인데 마진 불가/미상이면 제외(현물 숏=차입 필요). 다리에 마진O/X/? 뱃지.
- **펀비 정산주기**: `_interval_hours` 강화(통합 interval + raw info의 분/시 키). 프론트는 전 거래소 시간표시(미상=8H? 가정). 펀비 수집은 `funding_interval_sec`(60s) 주기로 throttle.
- **MEXC 펀비**: bulk(`fetchFundingRates`) 미지원/빈값이면 단건(`fetchFundingRate`) 폴백(동시성8·상한 `funding_single_cap`=250).
- **거래대금 필터**: 김프 탭에 거래대금(억원) 필터+컬럼 추가(`base_volume_krw`). 알림은 기존 `min_volume_eokwon` 유지.
- 테스트 43 passed(펀비주기 파싱·마진플래그·아비 마진다리 추가).
- `research/main.py`: 관심종목 `research_interval_sec`(기본 1주) 정기 분석 → `research:reports` 저장 + 텔레그램 브리핑. 키 없으면 idle.
- `api/routers/research.py`: `/research`(목록)·`/research/{code}`(전문)·`POST /research/{code}/run`(즉시). 대시보드 주식 탭 리서치 보기/분석 버튼.
- `collector/stock/kis.parse_price`: inquire-price에서 밸류에이션(per/pbr/eps/bps/시총/52주) 추가 추출(테스트 가능 순수 함수).
- docker-compose `research` 서비스, `anthropic` requirement, `.env.example`(ANTHROPIC_API_KEY/RESEARCH_MODEL/RESEARCH_INTERVAL_SEC), `tests/test_research.py`(렌즈/데이터/키없는 idle).
- 종목 추천이 아니라 분석 보조 워크플로(면책 문구 포함). LLM 호출은 키·네트워크 필요 → Pi에서 키 입력 후 동작.

## 완료된 것
### 1. 계획 수립 (승인됨)
- 더따리(theddari) 메뉴 벤치마킹(김프/프리미엄 대시보드, 펀비 아비트라지, 공지/알림 등).
- 사용자 결정: 코인+주식 병행 / 모니터링·알림 먼저 / RPi 올인 / 스택은 추천 위임 / 백테스트 저장 2TB 상한.
- 전체 계획: `docs/PLAN.md`.

### 2. 코드 (브랜치 `claude/trading-arbitrage-dashboard-plan-q9mcmu`)
- `collector/`: ccxt로 9거래소 현물 ticker + USD/KRW 환율 수집 → Redis.
  - `exchanges/adapter.py` fetch_tickers 일괄 조회, 미지원 시 코인별 폴백.
  - `forex.py` open.er-api.com + 폴백값.
- `api/`: FastAPI.
  - `services/premium.py` 두 기준 동시: `premium_pct`(테더 기준)=**알림용**, `premium_coin_pct`(코인/환율 기준)=**화면 표출용**. 테더가 없으면 환율 폴백. 대시보드는 코인 기준 표시, 알림은 테더 기준 평가.
  - `routers/premium.py` `/premium`, `/tickers/{ex}`, `/exchanges`, `WS /ws/premium`.
- `notifier/`: 텔레그램 봇 묶음.
  - 김프 알림: `alerts.py`(임계치 평가 순수함수)·`telegram.py`(Bot API 발송)·`main.py`(Redis 쿨다운 SET NX EX). 설정 `config/alerts.yaml`.
  - 공지알림봇(신규상장): `listings.py`(`MarketLister` ccxt 마켓조회 + `detect_new_listings` Redis set diff, 최초 1회 조용히 시드)·`announce_main.py`. 설정 `config/announcements.yaml`(감시 거래소·quote_filter·주기).
  - docker-compose에 `notifier`(김프)·`announcer`(신규상장) 두 서비스.
- `shared/`: `universe.py`(symbols.yaml 로더), `schemas.py`, `settings.py`, `redis_keys.py`.
- `config/symbols.yaml`: 9거래소(국내 KRW 2 + 해외 USDT 7) + 코인 10종.
- `docker-compose.yml`: redis/collector/api, RPi 메모리 제한(`mem_limit`).
- `tests/test_premium.py`: 유니버스·김프계산·API 헬스 → **3 passed**.
- `deploy/`: `bootstrap.sh`(RPi 원클릭) + `README.md`(Tailscale/Cloudflare 외부접속).

### 검증 결과
- 전체 `py_compile` OK, `pytest` 3 passed(김프 0%/3.26% 케이스 수치 검증).
- ⚠️ collector 라이브 실행은 미수행: 이 클라우드 환경은 9개 거래소로의 임의 아웃바운드가 막힘(HTTPS 프록시만 허용). 실제 시세 수신은 **RPi에서 docker compose up** 시 동작.

## 주요 의사결정 로그
- **ccxt 채택**: 9거래소 개별 SDK 대신 통합 인터페이스로 구현량 대폭 감소.
- **단일 Docker 이미지 + command 분기**: collector/api 공용 → RPi 단순화.
- **KIS OpenAPI(주식)**: 키움 OCX는 Windows 전용이라 RPi 부적합 → KIS 선택.
- **모니터링 우선/실행 게이트**: 봇 실주문은 dry-run→소액 단계적, 출금권한 없는 키.

## 환경/인프라 메모
- 작업 브랜치: `claude/trading-arbitrage-dashboard-plan-q9mcmu` (`Mike7Chu/SundayDeepLearning`).
- **새 레포 `Chu-trading`(Private)로 분리 예정** — 단, 이전 세션은 GitHub 레포 생성 권한 없음(403). → 사용자가 GitHub에서 빈 레포 생성 후, 그 레포로 새 세션 열어 코드 이관.
- RPi 직접 SSH 배포는 이 환경에선 불가(SSH 포트 차단) → `deploy/bootstrap.sh`를 Pi에서 실행하는 방식.

## 다음 작업 (우선순위)
1. ✅ ~~텔레그램 알림봇~~ — 완료(김프/역프 임계치).
2. ✅ ~~공지알림봇~~ — 완료. **신규상장 감지**(업비트/빗썸 마켓목록 diff → 새 심볼 알림)로 구현.
   공지 본문 텍스트가 필요하면 추후 notice-feed 파서 추가(한국 IP인 RPi에서 엔드포인트 확인 필요).
3. ✅ ~~김프 대시보드~~ — 완료. FastAPI가 `GET /`로 `web/index.html` 서빙(경량 단일 페이지).
4. ✅ ~~펀비/거래소간~~ — 완료. 해외 perp 가격+펀비 수집(`collector/exchanges/perp.py`), `/cross`(현물·선물 거래소간 스프레드)·`/funding` API, 대시보드 3탭(김프/거래소간/펀비). 마진=현물가(오더북 공유)라 별도 미제공.
5. **펀비/스프레드 임계치 알림** — notifier에 펀비·거래소간 스프레드 규칙 추가(텔레그램).
6. 봇 컨트롤 패널 필요 시 Next.js 확장. 이후 Phase 3(봇 페이퍼) → 4(코인 실행) → 5~6(주식).
