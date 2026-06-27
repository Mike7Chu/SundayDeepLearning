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

확인:
```bash
curl http://localhost:8000/health          # {"status":"ok"}
curl "http://localhost:8000/premium?base=upbit&ref=binance"
```
브라우저: `http://<pi-ip>:8000/docs`

## 업데이트(코드 갱신 시)

```bash
cd SundayDeepLearning/trading-platform
git pull
sudo docker compose up -d --build
```

## 외부에서 접속 (집 밖에서)

공인 IP/포트포워딩 없이 안전하게 접속하려면 둘 중 하나:

- **Tailscale (추천, 가장 쉬움)**: Pi에 `curl -fsSL https://tailscale.com/install.sh | sh` →
  `sudo tailscale up`. 폰/랩탑에도 Tailscale 깔면 `http://<pi-tailscale-ip>:8000` 으로 접속.
- **Cloudflare Tunnel**: 도메인이 있으면 `cloudflared`로 터널 생성 → 공개 URL 발급.

## 메모

- RPi4에서 첫 `--build`는 ccxt 등 설치로 수 분 소요.
- 기본은 시세 수집(공개 데이터, API 키 불필요)만 동작. 텔레그램 알림/봇은 이후 단계에서 `.env`에 키 추가.
- 자동 재시작: compose에 `restart: unless-stopped` 적용됨(재부팅 후 자동 기동).
