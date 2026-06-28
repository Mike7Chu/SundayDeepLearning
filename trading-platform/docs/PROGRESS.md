# PROGRESS — 진행 요약 / 결정 로그

> 무엇을 왜 했는지의 시간순 기록. 새 세션은 여기서 "지금까지"를 빠르게 파악.

## 현재 한 줄 상태
Phase 2 + 더따리 패리티 + 알림설정 + 봇 페이퍼 + 주식(KIS) + **AI 가치투자 리서치(Addendum 9)** 완료. 대시보드 6탭(김프·아비트라지·펀비·봇·주식·알림설정). 테스트 36/36 통과.

### Addendum 9 — AI 가치투자 리서치 (완료)
- `research/lenses.py`: 버핏·멍거·돤융핑·리루 4대 거장 렌즈(렌즈별 focus+체크리스트) → `SYSTEM_PROMPT`(출력형식+면책).
- `research/data.py`: Redis `stock:quote`(현재가+per/pbr/eps/bps)에서 `StockData` 수집, `format_for_prompt`(미상 처리).
- `research/analyst.py`: 백엔드 2종 — **api**(`AsyncAnthropic` 지연 import, 스트리밍+적응형 사고, 종량과금) / **cli**(`RESEARCH_USE_CLI=true`+`claude` 설치 시 `claude -p` 헤드리스 = **구독 무과금**). 모델 `claude-opus-4-8`. 둘 다 없으면 `mode=None`·비활성. `deploy/set-anthropic.sh`로 모드 설정.
- 결정: **Claude 구독 ≠ API 무료**(별도 결제). 추가과금 없이 구독 활용하려면 Claude Code CLI(`cli` 모드) 경유. console API 키는 종량과금. 구독으로 무과금 API 키 발급은 불가.
- `research/main.py`: 관심종목 `research_interval_sec`(기본 1일) 정기 분석 → `research:reports` 저장 + 텔레그램 브리핑. 키 없으면 idle.
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
