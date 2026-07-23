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
- ✅ **미국주식(미장)**: `config/us_stocks.yaml` 주요 100종목 유니버스 상시 수집(5분 시세 스윕 +
  6시간 일봉 `us_history_loop`) → 종목 탭에 항상 표시. 관심종목에 US 티커(NVDA 등) 등록 → 토스가 시세·일봉 수집(통화 USD 마킹),
  기술분석·매매가이드(센트 호가)·상세 모달·코치 점검(환율 환산 비중)까지 지원. 환율(`fx:usdkrw`) 상시 저장,
  주문은 토스 전용(텔레그램 `토스매수 NVDA 2 185.50`) — USD 금액을 환율로 원화 환산해 한도 검증.
  KIS/DART(펀더멘털·배당·빛의기둥)는 국내 전용이라 미장은 시세·기술 분석 중심.
- ✅ **토스 v1.2.2 신기능**: ①시장 지표(코스피/코스닥 지수·투자자별 순매수) `indicators_loop`(10분)
  → 홈 시장온도 + 코치 프롬프트 '[시장 지표]/[수급]' 블록 ②랭킹(국내/미국 급등·거래대금 상위)
  `rankings_loop`(10분) → 홈 급등 TOP 카드(이름 매핑) ③**OCO 조건주문**(목표가 익절+손절가 손절
  서버 감시, 매도 전용) `POST/GET /portfolio/oco`, 취소 지원 — 게이트(키+TOSS_TRADING_ENABLED),
  매도라 금액 한도 미적용. `GET /market` 신설. 재무·배당은 v1.2.2에도 없음(국내 KIS/DART 전용 유지).
- ✅ **실시간 시세 파이프라인**: ①KIS 웹소켓(`collector/stock/kis_ws.py`, H0STCNT0 체결가) — 장중(평일 08:50~15:40)
  관심∪보유 국내 41종목 등록, 체결 즉시 `stock:quote` 반영(종목당 1초 스로틀, PINGPONG 유지, 재접속)
  ②API SSE `GET /stream`(`api/routers/stream.py`) — 변경된 종목만 2초 주기 push ③대시보드 EventSource가
  가격·등락률·평가손익 셀만 덧칠(`data-lp/lc/lpnl`, 틱 플래시, 렌더 직후 LIVE 재적용) — 전체 폴링(12s)은 정합용
  ④엔진 `_guard_loop`(20s) — 목표가/손절 감시를 실시간가(`_live_price` 2분 신선도) 기준으로 고속 순회.
  끄기: `KIS_WS_ENABLED=false`. 미국 종목은 토스 REST(15s 스윕) 유지.
- ✅ **발굴 레이더(급등 전조 스크리너)**(`api/services/stock_radar.py`, `GET /stocks/radar`): 전 시장에서 '터질 종목'의
  5대 전조를 조합 — ①거래대금 급증(20일 평균 대비 배수, 30점) ②신고가 돌파/근접(25) ③당일 강도·장대양봉(20)
  ④실적·공시 촉매(잠정 YoY/DART, 15) ⑤추세 전환(정배열·골든, 10). 후보군=토스 랭킹 급등·거래대금 상위 ∪ DART
  실적 촉매 ∪ 신고가 근접(보유·동전주·미국 제외, cap 40)에만 온디맨드 캔들 조회 → 3분 캐시. `market_regime`
  (지수·외국인 수급)으로 위험선호/회피 배경 한 줄. 살까말까 탭 상단 '🚀 발굴 레이더' 카드(전조 배지 + 클릭→상세→AI).
  최소 게이트(거래대금 30억↑·당일 상승). 예측 아님·되돌림 위험 명시(면책).
- ✅ **재무 심화(딥밸류 판단용)**: DART 주요계정 1콜로 **부채비율**(부채총계/자본총계)·매출·영업이익 YoY를
  함께 추출(`parse_financials`), 전체 재무제표에서 **FCF**(영업CF−CAPEX, `parse_fcf`) 수집. 상세 모달은
  국내 종목 재무가 비었으면 DART 온디맨드 self-heal(12h 캐시)·시가총액도 토스 종목정보로 보강 →
  관심종목이 아니어도 '순이익/매출/영익 YoY·부채비율·FCF'가 채워져 AI 리서치의 '미상'이 사라짐.
  모달 회사가치 섹션·research 프롬프트·StockData에 반영(부채비율 100%↓ 우량·200%↑ 주의 색).
- ✅ **데이터 self-heal·신선도**: 상세 모달(`/stocks/{code}`)은 저장 시세가 60s↑ 오래되면 토스 재조회, 잔고
  (`/portfolio`)는 스냅샷 45s↑ 오래되면 토스 직접 재수집(20s 캐시) — 수집 루프가 멈춰도 화면이 최신값을 갖는다.
  UI에 '시세/잔고 N분 전' 나이 배지 + 오래되면 경고(장중 3분·장외 4시간 초과).
- ⏭️ **다음(로드맵)**: 자동매매 규칙화(시그널·가치·배당 DRIP → 게이트 주문), KIS 해외 재무(미국 살까말까/저평가).

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
