#!/usr/bin/env bash
# 라즈베리파이4(OMV/Debian)용 원클릭 배포 스크립트.
# 사용법 (Pi 터미널에서):
#   git clone -b claude/trading-arbitrage-dashboard-plan-q9mcmu \
#       https://github.com/Mike7Chu/SundayDeepLearning.git
#   cd SundayDeepLearning/trading-platform
#   bash deploy/bootstrap.sh
#
# 하는 일: docker(+compose) 설치 확인 → .env 준비 → 빌드 & 기동 → 상태 출력.
set -euo pipefail

REPO_BRANCH="claude/trading-arbitrage-dashboard-plan-q9mcmu"
cd "$(dirname "$0")/.."   # trading-platform/

echo "==> 1/4 docker 확인"
if ! command -v docker >/dev/null 2>&1; then
  echo "    docker 미설치 → get.docker.com 으로 설치"
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER" || true
  echo "    !! docker 그룹 반영 위해 재로그인이 필요할 수 있음. 안 되면 sudo로 재실행."
fi

# compose 플러그인 vs 구버전 docker-compose 자동 감지
if docker compose version >/dev/null 2>&1; then
  COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE="docker-compose"
else
  echo "    docker compose 플러그인 설치"
  sudo apt-get update -y && sudo apt-get install -y docker-compose-plugin
  COMPOSE="docker compose"
fi
echo "    using: $COMPOSE"

echo "==> 2/4 .env 준비"
if [ ! -f .env ]; then
  cp .env.example .env
  echo "    .env 생성됨(기본값). 텔레그램/거래소 키는 나중에 채우면 됨."
else
  echo "    기존 .env 유지"
fi

echo "==> 3/4 빌드 & 기동 (RPi에선 첫 빌드가 수 분 걸릴 수 있음)"
SUDO=""
docker info >/dev/null 2>&1 || SUDO="sudo"
$SUDO $COMPOSE up -d --build

echo "==> 4/4 상태 확인"
sleep 5
$SUDO $COMPOSE ps
echo
IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo "완료. API 헬스체크:"
echo "  curl http://localhost:8000/health"
echo "  브라우저: http://${IP:-<pi-ip>}:8000/docs"
echo "  김프:    http://${IP:-<pi-ip>}:8000/premium?base=upbit&ref=binance"
echo
echo "로그 보기:   $SUDO $COMPOSE logs -f collector"
echo "중지:        $SUDO $COMPOSE down"
