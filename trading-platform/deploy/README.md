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
curl "http://localhost:8090/premium?base=upbit&ref=binance"
```
브라우저: `http://<pi-ip>:8090/docs`

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
sudo docker compose up -d --build
```

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
   http://100.x.y.z:8090/docs
   http://100.x.y.z:8090/premium?base=upbit&ref=binance
   ```
   (MagicDNS를 켰다면 `http://<pi-host>.<tailnet>.ts.net:8090` 도 가능)

### ⚠️ 흔한 실수
- ❌ `http://mike7chu.hopto.org:8090` → 이건 **공인 IP(포트포워딩 방식)**. 포트를 안 열었으면
  `ERR_CONNECTION_ABORTED`. Tailscale과 무관하니 **사용하지 말 것.**
- ❌ 폰에서 Tailscale이 꺼져 있거나 다른 계정 → `tailscale status`에 안 보이면 연결 안 됨.
- ✅ 주소는 항상 `100.x` (또는 `.ts.net`), 포트 `:8090`.

> 도메인이 있고 공개 HTTPS 주소가 필요하면 추후 Cloudflare Tunnel/Tailscale Serve도 가능.

## 메모

- RPi4에서 첫 `--build`는 ccxt 등 설치로 수 분 소요.
- 기본은 시세 수집(공개 데이터, API 키 불필요)만 동작. 텔레그램 알림/봇은 이후 단계에서 `.env`에 키 추가.
- 자동 재시작: compose에 `restart: unless-stopped` 적용됨(재부팅 후 자동 기동).
