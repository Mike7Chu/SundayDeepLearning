# CLAUDE.md — LLM 핸드오프 / 프로젝트 컨텍스트

> 새 세션이 이 문서 + `docs/`만 읽으면 전체 맥락을 복구하도록 만든 인수인계 문서.

## 한 줄 요약
라즈베리파이4(OMV, 24h)에서 도는 **개인 주식 투자 플랫폼**. 한국투자증권(KIS) 관심종목의
시세·펀더멘털·시그널·배당을 수집·분석하고, **AI 4대 거장(버핏·멍거·돤융핑·리루) 리서치**·백테스트·
일일 텔레그램 브리핑을 제공. **궁극 목표: 자산 100억.**

> ⚠️ **피벗 이력**: 원래 코인 아비트라지(김프/펀비/봇) 플랫폼이었으나 사용자가 **주식 올인**으로
> 전환. 코인 코드는 전부 제거(git 이력에 남음 — 브랜치 `claude/trading-arbitrage-dashboard-plan-q9mcmu`
> 의 이전 커밋들). 현재는 주식 전용.

## 현재 상태 (Phase 1 완료 — 주식 전용 골격)
- ✅ **코인 전면 제거**: 수집기 코인 루프·김프/아비/펀비 서비스·페이퍼봇·notifier 알림·공지·코인 대시보드 삭제.
- ✅ **수집(KIS)**(`collector/main.py`·`collector/stock/kis.py`): 관심종목 현재가+밸류에이션(PER/PBR/EPS/BPS/시총/52주),
  일봉(시그널용 `stock:ohlcv:{code}`), 배당(`stock:dividend`). 키 없으면 idle.
- ✅ **가치 스크리너**(`api/services/stock_value.py`): 마법공식(이익수익률+ROE) 랭킹 + 품질점수. **전체 시장(`stock:market`) ∪ 관심종목 병합** 스캔. `/stocks/value?limit=`
- ✅ **전체 시장 유니버스**(`collector/stock/kis_master.py`): KIS 종목마스터(.mst) 다운로드·파싱 → `stock:universe`, collector `market_loop`이 배치로 펀더멘털 수집 → `stock:market`(스크리너가 스캔). 오프셋 Pi 검증 권장.
- ✅ **뉴스·공시(DART)**(`collector/news/`): opendart 전자공시 실시간 폴링(`dart_interval_sec`), 관심종목 신규 공시 → `dart:recent` + 텔레그램 알림. `/news`. docker `dart` 서비스. **무료 키(`DART_API_KEY`) 없으면 idle**
- ✅ **시그널 엔진**(`stock_signal.py`): SMA20/60 골든·데드크로스·RSI·모멘텀·볼린저 → buy/sell/neutral. `/stocks/signals`
- ✅ **배당**(`stock_dividend.py`): 배당수익률·캘린더·DRIP. `/stocks/dividend`
- ✅ **백테스트**(`backtest/engine.py`): sma/rsi/momentum 룰 검증(전략수익/승률/MDD, 룩어헤드 없음). `/stocks/backtest/{code}`
- ✅ **AI 리서치**(`research/`): 4대 거장 렌즈 → Claude(모델 `claude-opus-4-8`). 백엔드 2종 — API 키 or 구독 CLI(`RESEARCH_USE_CLI`).
  `/research`, `/research/{code}`, `POST /research/{code}/run`. 키 없으면 idle.
- ✅ **일일 브리핑**(`briefing/`): 시세·시그널·가치·배당 요약 텔레그램 1일 발송(`compose.py` 순수). 키 없으면 로그.
- ✅ **AI 아침 점검(포트폴리오 코치)**(`research/coach.py`, `api/routers/coach.py`): 매일 `COACH_HOUR_KST`(기본 8시)에
  실보유 비중·손익 + 종목 정량 + 보유종목 공시 + 리스크 실드 + 사용자 목표(수익률·기한)를 모아 종목별
  '계속 보유/일부 매도/위험 신호' 판정 + 오늘의 한 줄 결론(✅/⚠️/🚨) → 텔레그램 자동 발송 + 홈 카드.
  CLI 모드는 웹검색 허용(미국 반도체 간밤 동향). `GET /coach`, `POST /coach/goal|/coach/run`. 하루 1콜.
- ✅ **토스증권 연동**(`collector/stock/toss.py`, `api/routers/portfolio.py`): 실보유·매수여력 수집(`portfolio_loop`
  → `toss:holdings`/`toss:account`) + **실매매 게이트**(`TOSS_TRADING_ENABLED`+`TOSS_MAX_ORDER_KRW` 이중 검증).
  `GET /portfolio`, `POST /portfolio/order|.../cancel`. 홈 100억 진행률이 토스 실평가액으로 자동. 키 없으면 idle.
  KIS(펀더멘털)와 상호보완 — 토스엔 PER/PBR 없음. (KIS 토큰 공유·rt_cd 에러 로깅도 이때 안정화.)
- ✅ **주식 대시보드**(`web/index.html`, `GET /`): 탭 = 홈(100억 진행률·시장온도·하이라이트) / **포트폴리오** / 종목 / 가치 /
  시그널 / 배당 / 공시 / 리서치 / 설정. 종목명 클릭 → **종목 상세 모달**(펀더멘털·시그널·배당·백테스트·AI분석). 노드 빌드 X.
- ⏭️ **다음(로드맵)**: 자동매매 규칙화(시그널·가치·배당 DRIP → 게이트 주문), 미국주식(토스 US 티커)·환율 대시보드.

전체 계획은 승인된 플랜(`~/.claude/plans/toasty-wobbling-truffle.md`), 진행 기록은 `docs/PROGRESS.md`.

## 아키텍처 / 디렉터리
| 경로 | 역할 |
|------|------|
| `collector/main.py` | KIS 관심종목 현재가(15s) + 일봉·배당(6h) 수집 → Redis |
| `collector/stock/kis.py` | KIS 클라이언트(`fetch_price`/`fetch_daily`/`fetch_dividend`, `parse_*`). `config/stocks.yaml` 관심종목 |
| `collector/stock/toss.py` | 토스 클라이언트(OAuth2·`fetch_holdings`/`fetch_buying_power`/`place_order`, `parse_*`) — 실보유·실매매 |
| `api/routers/portfolio.py` | 포트폴리오·매수여력·(게이트)주문 REST |
| `api/services/stock_value.py` | 마법공식 가치 스크리너 |
| `api/services/stock_signal.py` | 기술적 시그널(SMA/RSI/모멘텀/볼린저) |
| `api/services/stock_dividend.py` | 배당수익률·캘린더·DRIP |
| `backtest/engine.py` | 전략 백테스트(sma/rsi/momentum) |
| `research/` | AI 4대 거장 리서치(`lenses`·`data`·`analyst`·`main`) |
| `briefing/` | 일일 텔레그램 브리핑(`compose`·`main`) |
| `notifier/telegram.py` | 텔레그램 발송(브리핑·리서치 공용) |
| `api/routers/{stocks,research}.py` | REST 엔드포인트 |
| `api/main.py` | FastAPI. `GET /`로 `web/index.html` 서빙 |
| `web/index.html` | 주식 대시보드(단일 페이지) |
| `shared/{redis_keys,redis_store,settings}.py` | Redis 키·헬퍼·설정 |
| `config/stocks.yaml` | 관심종목(종목코드) |
| `deploy/` | RPi 배포 스크립트·가이드 |

데이터 흐름: `collector(KIS) → Redis(stock:quote / stock:ohlcv:{code} / stock:dividend) → api(스크리너/시그널/배당/백테스트) + research + briefing → 대시보드 / 텔레그램`.

## 실행 / 검증
```bash
pip install -r requirements.txt
docker compose up -d redis
python -m collector.main            # KIS 수집(키 필요)
uvicorn api.main:app --port 8000    # API + 대시보드
pytest tests/ -q                    # 23 passed
```
- 확인: `GET /health`, `GET /stocks`, `GET /docs`, 대시보드 `GET /`.
- RPi 배포: `git pull && sudo docker compose up -d --build` (서비스: redis, collector, api, research, briefing).

## 연동 키 (.env, Pi)
- **KIS**: `KIS_APP_KEY/SECRET/ACCOUNT/PAPER` — 주식 시세·일봉·배당(모의투자 도메인 지원).
- **토스**: `TOSS_CLIENT_ID/SECRET`(+선택 `TOSS_ACCOUNT_SEQ`) — 실보유·매수여력. 실매매는 `TOSS_TRADING_ENABLED=true`+`TOSS_MAX_ORDER_KRW` 게이트.
- **AI 리서치**: `ANTHROPIC_API_KEY`(종량) 또는 `RESEARCH_USE_CLI=true`+Claude Code 로그인(구독 무과금, `deploy/run-research-host.sh` 호스트 구동).
- **텔레그램**: `TELEGRAM_BOT_TOKEN/CHAT_ID` — 일일 브리핑.
- **목표**: `TARGET_ASSET_KRW`(기본 100억) — 홈 진행률.

## 운영 메모
- 작업 브랜치: `claude/trading-arbitrage-dashboard-plan-q9mcmu` (레포 `Mike7Chu/SundayDeepLearning`). (브랜치명은 코인 시절 그대로 — 내용은 주식 피벗.)
- 원격접속 = **Tailscale 사설 IP**(`100.x`, 포트 `:8090`). 상세 `deploy/README.md`.
- 비밀번호/키는 코드·문서에 커밋 금지. `.env`는 git 제외.
- **면책**: 스크리너·시그널·리서치는 투자 판단 보조이며 매매 신호·수익 보장이 아님. 실매매는 페이퍼→소액 게이트(Phase 4).
