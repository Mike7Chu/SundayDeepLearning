# 라즈베리파이 배포 가이드

## 가장 빠른 방법 (Pi 터미널 / SSH)

```bash
# 1) git 설치(없으면)
sudo apt-get update && sudo apt-get install -y git

# 2) 코드 받기 (해당 브랜치)
git clone -b claude/trading-arbitrage-dashboard-plan-q9mcmu \
    https://github.com/Mike7Chu/SundayDeepLearning.git
cd SundayDeepLearning/trading-platform

# 3) 원클릭 배포 (docker 설치 → 빌드 → 기동까지 자동)
bash deploy/bootstrap.sh
```

확인 (기본 포트 8090):
```bash
curl http://localhost:8090/health          # {"status":"ok"}
curl http://localhost:8090/stocks          # 관심종목 시세(KIS 키 설정 시)
```
브라우저 대시보드: `http://<pi-ip>:8090/`  (API 문서: `/docs`)

### 자주 나는 문제
- **`address already in use` (포트 충돌)**: 다른 서비스가 그 포트를 점유 중.
  `.env`의 `API_PORT`를 빈 포트로 바꾸고 `sudo docker compose up -d` 재실행.
  점유 확인: `sudo ss -ltnp | grep :8090`
- **`memory limit ... discarded` 경고**: 무해(컨테이너 정상 동작). RPi에서 메모리
  제한을 실제 적용하려면 `/boot/cmdline.txt`(또는 `/boot/firmware/cmdline.txt`)에
  `cgroup_enable=memory cgroup_memory=1` 추가 후 재부팅. 안 해도 됨.

## 업데이트(코드 갱신 시)

```bash
cd SundayDeepLearning/trading-platform
git pull
cp -n .env.example .env          # API_PORT 등 새 항목 보강(기존 값 유지)
sudo docker compose up -d --build --remove-orphans
```
> **`--remove-orphans` 중요**: compose에서 삭제된 옛 서비스(예: 코인 시절 `notifier`·`announcer`·
> `bots`)의 컨테이너를 함께 정리한다. 없으면 옛 컨테이너가 계속 살아 **엉뚱한 텔레그램 알림(김프/펀비)**
> 을 계속 보낸다. 이미 유령이 돌고 있으면 한 번 완전 정리:
> ```bash
> sudo docker compose down --remove-orphans   # 유령 제거 + Redis 초기화(stale 데이터 소멸)
> sudo docker compose up -d --build
> sudo docker compose ps                       # notifier/announcer/bots 없어야 정상
> ```

## 외부에서 접속 (집 밖에서) — Tailscale 사설 IP

포트포워딩 없이 안전하게 접속. **공인 DDNS(`hopto.org`)가 아니라 Tailscale 주소(`100.x`)를 쓴다.**

1. Pi에 설치/기동:
   ```bash
   curl -fsSL https://tailscale.com/install.sh | sh
   sudo tailscale up
   ```
2. Pi의 Tailscale 주소 확인:
   ```bash
   tailscale ip -4      # → 100.x.y.z
   tailscale status     # 폰/랩탑이 같은 tailnet에 보이는지 확인
   ```
3. 폰/랩탑에도 Tailscale 설치 + **같은 계정** 로그인 + ON.
4. 그 주소로 접속:
   ```
   http://100.x.y.z:8090/          (대시보드)
   http://100.x.y.z:8090/docs      (API 문서)
   ```
   (MagicDNS를 켰다면 `http://<pi-host>.<tailnet>.ts.net:8090` 도 가능)

### ⚠️ 흔한 실수
- ❌ `http://mike7chu.hopto.org:8090` → 이건 **공인 IP(포트포워딩 방식)**. 포트를 안 열었으면
  `ERR_CONNECTION_ABORTED`. Tailscale과 무관하니 **사용하지 말 것.**
- ❌ 폰에서 Tailscale이 꺼져 있거나 다른 계정 → `tailscale status`에 안 보이면 연결 안 됨.
- ✅ 주소는 항상 `100.x` (또는 `.ts.net`), 포트 `:8090`.

> 도메인이 있고 공개 HTTPS 주소가 필요하면 추후 Cloudflare Tunnel/Tailscale Serve도 가능.

## 메모

- 주식 플랫폼(피벗 완료) — 서비스: `redis, collector, api, dart, research, briefing`. 코인 기능은 제거됨.
- KIS 키가 없으면 수집은 idle(대시보드 셸만). `.env`에 `KIS_APP_KEY/SECRET` 넣으면 시세·일봉·배당 채워짐.
- 토스 키(`TOSS_CLIENT_ID/SECRET`)를 넣으면 **포트폴리오 탭**에 실보유·평가액·수익률이 채워지고 홈 100억 진행률이 자동. (아래 토스 연동 참고.)
- DART 공시 알림은 `DART_API_KEY`(무료, opendart.fss.or.kr), AI 리서치는 `ANTHROPIC_API_KEY` 또는 `RESEARCH_USE_CLI`.
- 자동 재시작: compose에 `restart: unless-stopped` 적용됨(재부팅 후 자동 기동).
- **재배포 시 항상 `--remove-orphans`** — 옛 서비스 컨테이너 잔재로 인한 엉뚱한 알림 방지.

## AI 가치투자 리서치 연결 (research)

버핏·멍거·돤융핑·리루 4대 거장 렌즈로 관심종목을 분석한다. 백엔드 2종 중 택1:

### (A) 구독 무과금 — Claude Code 경유 (추천: 우선 체험)
> Claude Pro/Max **구독**과 Anthropic **API**는 별도 결제다. 구독엔 API 사용량이
> 포함되지 않으므로, 추가과금 없이 쓰려면 Claude Code(`claude -p`)를 경유한다.
1. Pi에 Claude Code 설치 + **구독 계정으로 로그인**:
   ```
   npm i -g @anthropic-ai/claude-code   # (또는 공식 설치 방법)
   claude                                # 로그인 후 /exit
   ```
2. `.env` 설정 + 로그인 자가진단:
   ```
   bash deploy/set-anthropic.sh cli
   ```
3. research를 **호스트에서** 구동(컨테이너는 호스트 로그인을 못 봄):
   ```
   nohup bash deploy/run-research-host.sh >/tmp/research.log 2>&1 &
   tail -f /tmp/research.log
   ```
   ⚠️ 자동 백엔드 용도는 인터랙티브 사용 의도와 다르고 **구독 사용량 한도·약관**에 유의.

### (B) 종량과금 — Anthropic API 키
1. console.anthropic.com에서 키 발급(구독과 별도 결제).
2. ```
   bash deploy/set-anthropic.sh api <ANTHROPIC_API_KEY>
   ```
   → docker compose `research` 컨테이너가 자동 기동.

### 확인
- 대시보드 **주식 탭** → 종목 옆 `🧠 분석` 클릭 → 리포트 생성, `📄 보기`로 재열람.
- API: `POST /research/005930/run`, `GET /research/005930`.
- 키/로그인 둘 다 없으면 안전하게 idle(안내 리포트만).

## 토스증권 연동 (포트폴리오·실매매)

토스가 KIS엔 없는 **실보유(잔고)·매수여력·실주문**을 제공. KIS(시세·펀더멘털)와 상호보완이라 둘 다 켜두면 좋다.
발급: [developers.tossinvest.com](https://developers.tossinvest.com/docs) → OpenAPI 앱 등록 → `client_id`/`client_secret`.

### (1) 읽기전용 — 포트폴리오/100억 자동 트래킹 (안전, 추천 우선)
```bash
# .env
TOSS_CLIENT_ID=...
TOSS_CLIENT_SECRET=...
# TOSS_ACCOUNT_SEQ=      # 비우면 대표계좌 자동탐색
```
```bash
sudo docker compose up -d --build --remove-orphans
sudo docker compose logs -f collector | grep toss     # [toss] N보유 · 평가 …원 이면 정상
```
- 대시보드 **포트폴리오 탭**: 보유종목·평단·현재가·평가액·수익률 + 총평가/현금/매수여력.
- **홈 100억 진행률**이 토스 실평가액으로 자동(수동 입력 대체).
- API: `GET /portfolio`, `GET /portfolio/orders`.

### (2) 실매매 — 게이트 (⚠️ 실제 돈이 나감)
기본 잠금(`TOSS_TRADING_ENABLED=false`)이라 주문 API는 403, 대시보드 매수/매도 버튼도 숨김. 켜려면:
```bash
# .env  — 소액·페이퍼 검증 후에만
TOSS_TRADING_ENABLED=true
TOSS_MAX_ORDER_KRW=100000     # 주문당 상한(예상금액 초과 시 403)
```
- 포트폴리오 탭에 **주문 패널** 노출(지정가만) → 확인 다이얼로그 후 접수. `POST /portfolio/order`(BUY/SELL, symbol/quantity/price), `POST /portfolio/order/{id}/cancel`.
- **이중 게이트**: `TOSS_TRADING_ENABLED=true` **AND** `예상금액 ≤ TOSS_MAX_ORDER_KRW`. 하나라도 실패면 403.
- 권장: 먼저 **1주 소액**으로 주문·취소를 검증한 뒤 한도를 올린다. (스크리너·시그널은 판단 보조 — 매매 신호 아님, 면책.)
