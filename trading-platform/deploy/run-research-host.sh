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

# Redis는 docker compose의 것을 사용(호스트에서 localhost:6379로 접속)
export REDIS_URL="${REDIS_URL:-redis://localhost:6379/0}"
# .env의 RESEARCH_*/TELEGRAM_* 등을 그대로 사용(pydantic-settings가 .env 로드)
echo "[research] 호스트 구동 (REDIS_URL=$REDIS_URL). 중지: Ctrl+C"
exec "$VENV/bin/python" -m research.main
