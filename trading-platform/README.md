# Trading Platform (코인 아비트라지 + 주식)

라즈베리파이4(OMV, 24h) 위에서 도는 개인 트레이딩 플랫폼.
전체 설계/로드맵은 승인된 계획서를 따른다.

- **코인 아비트라지**: 9개 거래소(업비트·빗썸·바이낸스·바이비트·MEXC·비트겟·게이트·BingX·OKX) 김프/역프·펀비 모니터링 + 알림 + 봇.
- **주식**: 시그널/가치/배당 대시보드 + 정기 규칙 매매 (한국투자증권 OpenAPI).

> ⚠️ 현재 단계: **Phase 0~2 (스캐폴딩 + 데이터 수집 + 김프 계산/알림)**. 실주문 봇은 페이퍼 모드부터 단계적 도입.

## 구성 요소

| 서비스 | 역할 |
|--------|------|
| `collector` | ccxt로 거래소 시세·환율 수집 → Redis/DB 적재 |
| `api` | FastAPI. 김프/역프/펀비 계산, REST + WebSocket |
| `web` | (예정) Next.js 대시보드 |
| `notifier` | (예정) 텔레그램 알림/제어 봇 |
| `bots` | (예정) 현선/loan/매도 봇, 주식 전략 봇 |

## 로컬 실행 (개발)

```bash
cd trading-platform
cp .env.example .env          # 값 채우기 (없어도 기본값으로 동작)
pip install -r requirements.txt

# 1) 인프라 (redis) — docker 사용 시
docker compose up -d redis

# 2) 수집기
python -m collector.main

# 3) API (별도 터미널)
uvicorn api.main:app --reload --port 8000
```

## Docker 실행 (RPi 권장)

```bash
cd trading-platform
cp .env.example .env
docker compose up -d --build
# api: http://<rpi-ip>:8090/docs  (호스트 포트는 .env 의 API_PORT, 기본 8090)
```

## 주요 엔드포인트

- `GET /health` — 헬스체크
- `GET /premium?base=upbit&ref=binance` — 기준 거래소(국내) 대비 해외 거래소 김프 매트릭스
- `GET /tickers/{exchange}` — 거래소 최신 시세 스냅샷
- `WS /ws/premium` — 김프 실시간 스트림

## 설정

- 거래소/심볼 유니버스: `config/symbols.yaml`
- 환경변수: `.env` (`shared/settings.py` 참조)
