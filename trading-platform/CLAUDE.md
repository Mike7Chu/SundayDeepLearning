# CLAUDE.md — LLM 핸드오프 / 프로젝트 컨텍스트

> 새 세션/새 레포로 넘어가도 이 문서 + `docs/`만 읽으면 전체 맥락을 복구할 수 있게 만든 인수인계 문서.

## 한 줄 요약
라즈베리파이4(OMV, 24h)에서 도는 개인 트레이딩 플랫폼. **(A) 코인 거래소 아비트라지(김프/펀비) 모니터링·알림·봇** + **(B) 주식 시그널/가치/배당 전략**. 더따리(theddari) 스타일 대시보드 지향.

## 현재 상태 (Phase)
- ✅ **Phase 0~1**: 스캐폴딩 + 코인 시세/환율 수집 파이프라인
- ✅ **Phase 2 코어**: 김프/역프 계산 + REST/WS API
- ✅ **텔레그램 알림봇**(`notifier/main.py`): 김프/역프 임계치 → 텔레그램 발송 + 쿨다운
- ✅ **공지알림봇=신규상장 감지**(`notifier/announce_main.py`): 업비트/빗썸 마켓목록 diff → 새 심볼 등장 시 알림(quote_filter=KRW). 공지 스크래핑 대신 마켓 diff(클라우드 IP 차단·더 안정적)
- ⏭️ **다음**: ① 펀비 수집/대시보드 ② Next.js 대시보드
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
| `api/services/premium.py` | 김프식: `(국내KRW/(해외USDT×환율)−1)×100` |
| `api/routers/premium.py` | `/premium`, `/tickers/{ex}`, `/exchanges`, `WS /ws/premium` |
| `notifier/` | 텔레그램 봇 묶음. 김프알림(`main.py`/`alerts.py`, `config/alerts.yaml`) + 신규상장감지(`announce_main.py`/`listings.py`, `config/announcements.yaml`) + 발송(`telegram.py`) |
| `shared/` | 유니버스 로더(`universe.py`)·스키마(`schemas.py`)·설정(`settings.py`)·Redis키(`redis_keys.py`) |
| `config/symbols.yaml` | 거래소 + 코인 유니버스(단일 진실원) |
| `deploy/` | RPi 원클릭 배포 스크립트·가이드 |
| `tests/test_premium.py` | 김프계산·유니버스·API 스모크 테스트 |

데이터 흐름: `collector → Redis(ticker:{ex} 해시, fx:USDKRW) → api 계산 → REST/WS → (예정)web/notifier`.

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
