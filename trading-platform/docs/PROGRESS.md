# PROGRESS — 진행 요약 / 결정 로그

> 무엇을 왜 했는지의 시간순 기록. 새 세션은 여기서 "지금까지"를 빠르게 파악.

## 현재 한 줄 상태
Phase 0~1 + Phase 2 코어(김프 계산/REST/WS) + **텔레그램 알림봇** 완료. 테스트 6/6 통과. RPi 배포 스크립트 준비됨. **공지알림봇·펀비·대시보드 UI·봇·주식은 미착수.**

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
  - `services/premium.py` 김프식 `(국내KRW/(해외USDT×환율)−1)×100`.
  - `routers/premium.py` `/premium`, `/tickers/{ex}`, `/exchanges`, `WS /ws/premium`.
- `notifier/`: 텔레그램 알림봇. `alerts.py`(임계치 평가 순수함수)·`telegram.py`(Bot API 발송)·`main.py`(Redis 쿨다운 SET NX EX). 설정 `config/alerts.yaml`(감시 쌍·high/low 임계·쿨다운).
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
1. ✅ ~~텔레그램 알림봇~~ — 완료(김프/역프 임계치). 펀비 알림은 펀비 수집 후 추가.
2. **공지알림봇** — 업비트/빗썸 공지 폴링(`collector/announcements/`) → 신규상장 알림.
3. **펀비 수집 + 펀비 대시보드** — 무기한선물 펀딩비 추가(+ 알림봇에 펀비 규칙 연동).
4. **Next.js 대시보드** (`web/`) — 프리미엄 매트릭스 + 펀비 + (이후)봇 컨트롤 패널.
5. 이후 Phase 3(봇 페이퍼) → 4(코인 실행) → 5~6(주식).
