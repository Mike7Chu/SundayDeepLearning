#!/usr/bin/env bash
# 텔레그램 알림 설정: .env에 토큰/chat_id 기록 → 테스트 발송 → notifier 재기동.
# 사용법:
#   bash deploy/set-telegram.sh <BOT_TOKEN> <CHAT_ID>
# (토큰은 인자로만 받고 레포에 저장하지 않음. .env는 git 제외.)
set -euo pipefail
cd "$(dirname "$0")/.."   # trading-platform/

TOKEN="${1:-}"
CHAT="${2:-}"
if [ -z "$TOKEN" ] || [ -z "$CHAT" ]; then
  echo "usage: bash deploy/set-telegram.sh <BOT_TOKEN> <CHAT_ID>"
  exit 1
fi

[ -f .env ] || cp .env.example .env

set_kv() {
  local key="$1" val="$2"
  if grep -qE "^${key}=" .env; then
    sed -i "s|^${key}=.*|${key}=${val}|" .env
  else
    echo "${key}=${val}" >> .env
  fi
}
set_kv TELEGRAM_BOT_TOKEN "$TOKEN"
set_kv TELEGRAM_CHAT_ID "$CHAT"
echo "[.env] 텔레그램 설정 완료"

echo "테스트 메시지 발송..."
if curl -fsS "https://api.telegram.org/bot${TOKEN}/sendMessage" \
     -d chat_id="${CHAT}" \
     --data-urlencode text="✅ 트레이딩 플랫폼 알림봇 연결 완료" >/dev/null; then
  echo "  → 텔레그램으로 메시지 갔는지 확인하세요"
else
  echo "  → 발송 실패: 토큰/CHAT_ID 다시 확인 (BotFather 토큰, getUpdates의 chat.id)"
fi

SUDO=""; docker info >/dev/null 2>&1 || SUDO="sudo"
if docker compose version >/dev/null 2>&1; then C="docker compose"; else C="docker-compose"; fi
$SUDO $C up -d --force-recreate notifier
echo "notifier 재기동 완료. 실시간 로그: $SUDO $C logs -f notifier"
