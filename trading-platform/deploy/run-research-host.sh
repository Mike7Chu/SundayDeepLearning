#!/usr/bin/env bash
# 구독 무과금(cli) 모드용: research를 '호스트'에서 직접 구동.
# (컨테이너 안에서는 호스트의 Claude Code 로그인이 안 보이므로 호스트에서 실행한다.)
#
# 전제: Claude Code 설치 + 구독 로그인 완료, .env에 RESEARCH_USE_CLI=true.
#   bash deploy/set-anthropic.sh cli    # 먼저 실행해 .env 설정 + 로그인 점검
#
# 사용법:
#   bash deploy/run-research-host.sh           # 포그라운드
#   nohup bash deploy/run-research-host.sh >/tmp/research.log 2>&1 &   # 백그라운드
set -euo pipefail
cd "$(dirname "$0")/.."   # trading-platform/

VENV=".venv-research"
if [ ! -d "$VENV" ]; then
  echo "[venv] 생성 + 의존성 설치(최초 1회)..."
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -q --upgrade pip
  "$VENV/bin/pip" install -q -r requirements.txt
fi

# root(sudo) 실행 차단 — root에는 claude 구독 로그인이 없어 CLI가 rc=129로
# 조용히 실패하고, 큐만 가로채는 좀비가 된다(실제 사고 이력).
if [ "$(id -u)" -eq 0 ]; then
  echo "❌ root(sudo)로 실행하지 마세요 — claude 로그인이 없는 계정입니다."
  echo "   일반 사용자로: nohup bash deploy/run-research-host.sh >/tmp/research.log 2>&1 &"
  exit 1
fi

# Redis는 docker compose의 것을 사용(호스트에서 localhost:6379로 접속)
export REDIS_URL="${REDIS_URL:-redis://localhost:6379/0}"
# .env의 RESEARCH_*/TELEGRAM_* 등을 그대로 사용(pydantic-settings가 .env 로드)
echo "[research] 호스트 구동 (REDIS_URL=$REDIS_URL). 중지: Ctrl+C"

# 크래시해도 10초 후 자동 재기동 — '아침 점검 무소식'의 최후 방어선.
# (정상 종료 Ctrl+C(SIGINT)/kill(SIGTERM)은 루프도 함께 끝난다.)
trap 'echo "[research] 중지됨"; exit 0' INT TERM
while true; do
  "$VENV/bin/python" -m research.main && break
  echo "[research] 비정상 종료(rc=$?) — 10초 후 재시작 $(date '+%F %T')"
  sleep 10
done
