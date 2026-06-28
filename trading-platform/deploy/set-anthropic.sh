#!/usr/bin/env bash
# AI 리서치 백엔드 설정 → .env 기록 → research 컨테이너 재기동.
#
# 핵심: Claude 구독(Pro/Max)과 Anthropic API는 별도 결제다.
#   - 구독에는 API 사용량이 포함되지 않음(추가과금 없는 API 키 발급 불가).
#   - 구독을 추가과금 없이 쓰려면 'cli'(Claude Code 헤드리스) 모드를 쓴다.
#
# 사용법:
#   bash deploy/set-anthropic.sh cli                # 구독 무과금(Claude Code 경유)
#   bash deploy/set-anthropic.sh cli /path/to/claude  # 실행파일 경로 지정(선택)
#   bash deploy/set-anthropic.sh api <ANTHROPIC_API_KEY>  # 종량과금 API 키
# (키는 인자로만 받고 레포에 저장하지 않음. .env는 git 제외.)
set -euo pipefail
cd "$(dirname "$0")/.."   # trading-platform/

MODE="${1:-}"
[ -f .env ] || cp .env.example .env

set_kv() {
  local key="$1" val="$2"
  if grep -qE "^${key}=" .env; then
    sed -i "s|^${key}=.*|${key}=${val}|" .env
  else
    echo "${key}=${val}" >> .env
  fi
}

case "$MODE" in
  cli)
    BIN="${2:-claude}"
    if ! command -v "$BIN" >/dev/null 2>&1 && [ ! -x "$BIN" ]; then
      echo "✗ '$BIN' 를 찾을 수 없음. 먼저 Claude Code 설치 + 로그인하세요:"
      echo "    npm i -g @anthropic-ai/claude-code   # 또는 공식 설치 방법"
      echo "    claude            # 로그인(구독 계정)"
      exit 1
    fi
    set_kv RESEARCH_USE_CLI true
    set_kv RESEARCH_CLI_BIN "$BIN"
    set_kv ANTHROPIC_API_KEY ""   # 키 모드 비우기(키 있으면 그게 우선이라)
    echo "[.env] 구독 무과금 모드(cli) 설정 — 백엔드='$BIN'"
    echo "  ⚠️ 컨테이너 안에서 claude를 쓰려면 호스트의 인증/실행파일이 컨테이너에"
    echo "     보여야 합니다. 가장 단순한 방법은 research를 호스트에서 직접 실행:"
    echo "       python -m research.main"
    ;;
  api)
    KEY="${2:-}"
    if [ -z "$KEY" ]; then echo "usage: bash deploy/set-anthropic.sh api <ANTHROPIC_API_KEY>"; exit 1; fi
    set_kv ANTHROPIC_API_KEY "$KEY"
    echo "[.env] API 종량과금 모드 설정 (구독과 별도 결제됨)"
    ;;
  *)
    echo "usage:"
    echo "  bash deploy/set-anthropic.sh cli [claude경로]        # 구독 무과금"
    echo "  bash deploy/set-anthropic.sh api <ANTHROPIC_API_KEY>  # 종량과금"
    exit 1
    ;;
esac

SUDO=""; docker info >/dev/null 2>&1 || SUDO="sudo"
if docker compose version >/dev/null 2>&1; then C="docker compose"; else C="docker-compose"; fi
$SUDO $C up -d --force-recreate research 2>/dev/null || true
echo "research 재기동 시도 완료. 로그: $SUDO $C logs -f research"
