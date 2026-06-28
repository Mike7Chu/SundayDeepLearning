# CLAUDE.md — LLM 핸드오프 / 프로젝트 컨텍스트

> 새 세션/새 레포로 넘어가도 이 문서 + `docs/`만 읽으면 전체 맥락을 복구할 수 있게 만든 인수인계 문서.

## 한 줄 요약
라즈베리파이4(OMV, 24h)에서 도는 개인 트레이딩 플랫폼. **(A) 코인 거래소 아비트라지(김프/펀비) 모니터링·알림·봇** + **(B) 주식 시그널/가치/배당 전략**. 더따리(theddari) 스타일 대시보드 지향.

## 현재 상태 (Phase)
- ✅ **Phase 0~1**: 스캐폴딩 + 코인 시세/환율 수집 파이프라인
- ✅ **Phase 2 코어**: 김프/역프 계산 + REST/WS API
- ✅ **텔레그램 알림봇**(`notifier/main.py`): 김프/역프 임계치 → 텔레그램 발송 + 쿨다운
- ✅ **공지알림봇=신규상장 감지**(`notifier/announce_main.py`): 업비트/빗썸 마켓목록 diff → 새 심볼 등장 시 알림(quote_filter=KRW). 공지 스크래핑 대신 마켓 diff(클라우드 IP 차단·더 안정적)
- ✅ **전 코인 동적화**: 고정 10개 폐기 → 거래소 마켓에서 bulk fetch로 전 코인 수집(KRW/USDT 필터, 스테이블/레버리지 제외). 김프는 "기준 거래소 코인 ∩ 비교"
- ✅ **펀비 정산주기**: 거래소·코인별 `{rate, interval_h, next_ts}` 수집 → `/funding/matrix`(코인×거래소, APY 정규화·남은시간), 대시보드 펀비 매트릭스(APY 토글·카운트다운·정산주기 뱃지)
- ✅ **아비트라지 전략 리스트**(더따리식): `/arbitrage` 코인별 현물/선물 최저·최고 다리 + 갭% + 펀비 + 입출금 상태. 대시보드 전략 카드
- ✅ **입출금 상태 수집**(`collector/exchanges/wallet.py`): `fetch_currencies`로 입금/출금 가능여부(5분 주기)
- ✅ **대시보드 3탭**(`web/index.html`, `GET /`): 김프 / 아비트라지(전략카드) / 펀비(매트릭스) + 코인 검색. 노드 빌드 X
- ✅ **선물김프 + 현선 알림**: 김프 탭에 국내현물 vs 해외선물(perp) 비교 컬럼(`premium_perp_pct`). 선물 역프 임계치(`hyeonseon_low_pct`) 이하 시 텔레그램 현선(현물매수+선물숏) 알림
- ✅ **정렬/필터**: 김프(컬럼정렬·범위) / 아비(갭·현물선물·거래소 제외) / 펀비(정산주기·정렬)
- ✅ **펀비 알림**: 과열 |APY| + 거래소간 펀비차(%p) 텔레그램(`notifier/alerts.evaluate_funding`)
- ✅ **봇 페이퍼**(`bots/`): 프레임워크+실행게이트웨이(dry-run)+현선봇. `/bots` 컨트롤(enable/disable/killswitch), 대시보드 봇 탭. 실거래 미오픈
- ✅ **주식(KIS)**(`collector/stock/kis.py`): 관심종목 현재가 수집(키 없으면 idle), `/stocks` + 대시보드 주식 탭
- ✅ **알림 설정**(`shared/alert_settings.py`, `/alerts/settings`, 대시보드 알림설정 탭): 마스터/종류 on-off·임계치·쿨다운·**최소유지(디바운스)**·제외코인을 실시간 조절(Redis 오버라이드, notifier 매주기 반영)
- ✅ **AI 가치투자 리서치**(`research/`, Addendum 9): 버핏·멍거·돤융핑·리루 4대 거장 렌즈(`lenses.py`)로 관심종목 분석 → 구조화 리포트(`analyst.py`, 모델 `claude-opus-4-8`). `research/main.py` 정기 분석 + 텔레그램 브리핑, `/research`·`/research/{code}`·`POST /research/{code}/run`, 대시보드 주식 탭 리서치 보기. 백엔드 2종: **api**(`ANTHROPIC_API_KEY`, 종량과금) / **cli**(`RESEARCH_USE_CLI=true` + Claude Code 설치 시 `claude -p` 헤드리스 = **구독 무과금**, research를 호스트에서 `deploy/run-research-host.sh`로 구동). 둘 다 없으면 idle. `deploy/set-anthropic.sh`로 설정. 추천 아님·분석 보조(면책)
- ✅ **현물 정합성·마진·펀비 보강**: 마켓 메타 주기적 reload(`markets_reload_sec`, 상폐 stale 제거) / 현물 `margin` 수집→아비 **현물숏은 마진 가능시만 표시**(대시보드 토글) / 펀비 정산주기 파싱 강화(분·시 키)+**전 거래소 시간표시** / 펀비 단건 폴백(`fetchFundingRate`)으로 **MEXC 등 bulk 미지원 거래소 펀비 수집** / 김프 탭 **거래대금(억원) 필터·컬럼**(알림은 기존 `min_volume_eokwon`)
- ✅ **주식 전략 3종**(Phase 5, 모니터링 전용): **가치 스크리너**(`api/services/stock_value.py`, 마법공식 이익수익률+ROE 랭킹·품질) / **시그널 엔진**(`stock_signal.py`, SMA 골든·데드크로스·RSI·모멘텀·볼린저, 일봉 `stock:ohlcv:{code}` 기반) / **배당**(`stock_dividend.py`, 배당수익률·캘린더·정기적립 DRIP). API `/stocks/value`·`/stocks/signals`·`/stocks/dividend`, 대시보드 주식탭 보기 전환(시세/가치/시그널/배당). 일봉·배당 수집(`collector.stock_history_loop`, 6h)
- ✅ **주식 일일 브리핑**(`briefing/`): 시세·시그널·가치·배당 요약을 텔레그램 1일 발송(`compose.py` 순수 조립, 키 없으면 로그). docker `briefing` 서비스
- ✅ **텔레그램 명령 제어**(`notifier/commands.py`+`command_main.py`): `/status /bots /bot start|stop /killswitch /mute /unmute /alerts /brief` — 봇·알림을 Redis 컨트롤 플레인으로(대시보드와 단일 진실원). 소유자 chat_id만 응답. docker `commander` 서비스
- ⏭️ **다음**: 봇 실거래 게이트(안전장치, 사용자 결정 필요) / 주식 실주문(gated) / TimescaleDB 영속화 / 백테스트
- ⏸️ **봇 실행(현선/loan/매도), 주식**: 페이퍼 모드부터 단계적 (Phase 3~6, 미착수)

전체 로드맵·설계 근거는 [`docs/PLAN.md`](docs/PLAN.md), 무엇을 왜 했는지는 [`docs/PROGRESS.md`](docs/PROGRESS.md).

## 핵심 설계 결정 (요약)
- **둘 다 병행**하되 **모니터링·알림 먼저 검증 → 실행봇은 페이퍼→소액 단계적**.
- **라즈베리파이 올인** 구동 (AWS는 추후). 경량 컨테이너 스택.
- 거래소 통합은 **ccxt** 단일 인터페이스(9개: 업비트·빗썸·바이낸스·바이비트·MEXC·비트겟·게이트(gateio)·BingX·OKX).
- 주식 API는 **한국투자증권(KIS) OpenAPI** (키움 OCX는 Windows 전용이라 RPi 부적합).
- 백테스트/히스토리 저장 **2TB 예산 상한**(8TB HDD 중).

## 아키텍처 / 디렉터리
| 경로 | 역할 |
|------|------|
| `collector/` | ccxt로 9거래소 시세 + USD/KRW 환율 수집 → Redis (`collector/main.py`) |
| `collector/exchanges/adapter.py` | ccxt 거래소 어댑터(fetch_tickers/폴백) |
| `collector/forex.py` | USD/KRW 환율(open.er-api.com, 폴백값 지원) |
| `api/` | FastAPI. 김프 계산 + REST/WS |
| `api/services/premium.py` | 두 기준 동시 산출: `premium_pct`=테더(USDT/KRW) 기준→**알림용**, `premium_coin_pct`=코인/환율(USD/KRW) 기준→**화면용**. 테더가 없으면 환율 폴백 |
| `api/routers/premium.py` | `/premium`, `/tickers/{ex}`, `/exchanges`, `WS /ws/premium` |
| `api/services/cross.py` | 거래소간 가격차(`compute_cross`) + 펀비 비교/매트릭스(`compute_funding`/`compute_funding_matrix`, APY) + `all_coins` |
| `api/services/arbitrage.py` | 코인별 현물/선물 최저·최고 다리 전략(`compute_arbitrage`, 갭% + 펀비 + 입출금) |
| `collector/exchanges/perp.py` | 해외 perp 가격 + 펀비(rate/interval_h/next_ts) 전체 수집(ccxt defaultType=swap) |
| `collector/exchanges/wallet.py` | 입출금 가능여부(`fetch_currencies`) 수집(5분 주기) |
| `shared/symbols.py` | 심볼 파싱(`parse_symbol`)·레버리지토큰 필터(`is_leveraged_token`) |
| `web/index.html` | 대시보드(김프/거래소간/펀비 탭). FastAPI `GET /`로 서빙(`api/main.py`) |
| `notifier/` | 텔레그램 봇 묶음. 김프/현선/펀비 알림(`main.py`/`alerts.py`, `config/alerts.yaml`) + 신규상장감지(`announce_main.py`/`listings.py`) + 발송(`telegram.py`) |
| `bots/` | 페이퍼 봇. `framework.BotBase`(상태머신·컨트롤)·`execution_gateway`(dry-run 가상체결)·`coin/hyeonseon`(현선봇)·`main.py`. 컨트롤 API `api/routers/bots.py` |
| `collector/stock/kis.py` | 한국투자증권 현재가+밸류에이션(per/pbr/eps/bps; 키 없으면 비활성). `config/stocks.yaml` 관심종목, `/stocks` API |
| `research/` | AI 가치투자 리서치. `lenses.py`(4거장 렌즈)·`data.py`(종목데이터)·`analyst.py`(Claude API, idle if no key)·`main.py`(정기분석+텔레그램). 컨트롤 API `api/routers/research.py` |
| `shared/` | 유니버스 로더(`universe.py`)·스키마(`schemas.py`)·설정(`settings.py`)·Redis키(`redis_keys.py`) |
| `config/symbols.yaml` | 거래소 + 코인 유니버스(단일 진실원) |
| `deploy/` | RPi 원클릭 배포 스크립트·가이드 |
| `tests/test_premium.py` | 김프계산·유니버스·API 스모크 테스트 |

데이터 흐름: `collector → Redis(ticker:{ex} 현물, perp_ticker:{ex} 선물, funding:{ex} 펀비, tether:{국내ex}, fx:USDKRW) → api(김프 테더기준 / cross / funding) → REST/WS + web 대시보드 + notifier 알림`.

## 실행 / 검증
```bash
# 로컬 개발
pip install -r requirements.txt
docker compose up -d redis
python -m collector.main           # 수집
uvicorn api.main:app --port 8000   # API

# 테스트 (3 passed)
pytest tests/ -q

# RPi 배포: deploy/README.md 참고 → bash deploy/bootstrap.sh
```
- 확인: `GET /health`, `GET /premium?base=upbit&ref=binance`, `GET /docs`

## Open Items (구현 전 확정 필요)
- loan봇 차입 메커니즘(국내 현물은 마진/대여 미지원 → 해외 마진 가정), 현선봇 헤지 구조.
- 김프 실차익거래 외환·자금이동 규제 검토(실행 전).
- 실거래 안전장치 파라미터(명목/일손실 한도·IP 화이트리스트). 거래소 API 키는 **출금권한 제외**.

## 운영 메모
- 현재 작업 브랜치: `claude/trading-arbitrage-dashboard-plan-q9mcmu` (레포 `Mike7Chu/SundayDeepLearning`).
- 별도 레포(`Chu-trading`)로 분리 예정이었으나, 이전 세션 권한으로는 레포 생성 불가(403). 새 레포는 사용자가 GitHub에서 생성 후 새 세션에서 이관.
- 비밀번호 등 자격증명은 코드/문서에 절대 커밋하지 말 것. `.env`는 git 제외.
- 원격접속 = **Tailscale 사설 IP**(`100.x`/MagicDNS, 포트 `:8090`). 공인 DDNS(`hopto.org`)·포트포워딩 미사용. 상세 `deploy/README.md`.
- API 호스트 포트는 `.env`의 `API_PORT`(기본 8090). 8000은 OMV 등과 충돌나서 회피.
