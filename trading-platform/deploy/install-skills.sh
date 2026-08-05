#!/usr/bin/env bash
# 클로드 코드(CLI) 스킬 설치 — zip(들)을 ~/.claude/skills/<name>/ 에 풀어 넣는다.
#
# 이 프로젝트의 호스트 리서치(run-research-host.sh)가 `claude -p` 구독 모드로 돌기 때문에,
# ~/.claude/skills 에 스킬을 넣으면 그 헤드리스 호출에서 트리거로 호출된다(수동 대화에서도).
# ⚠️ 스킬 zip은 배포자(예: 타민더마켓)의 저작물 — 개인 사용만. 레포에 커밋하지 않는다.
#
# 사용:
#   bash deploy/install-skills.sh taminskills.zip          # 번들(내부 zip 여러 개) OK
#   bash deploy/install-skills.sh a.zip b.zip c.zip        # 개별 스킬 zip 여러 개
#   CLAUDE_SKILLS_DIR=/custom/dir bash deploy/install-skills.sh a.zip   # 설치 위치 지정
set -euo pipefail

DEST="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}"
mkdir -p "$DEST"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT

if [ "$#" -eq 0 ]; then
  echo "사용법: bash deploy/install-skills.sh <스킬.zip> [zip...]"; exit 1
fi
command -v unzip >/dev/null || { echo "❌ unzip 필요(sudo apt-get install unzip)"; exit 1; }

install_skill_zip() {           # $1 = SKILL.md 를 포함한 스킬 zip
  local z="$1" d md sdir name
  d="$(mktemp -d "$tmp/sk.XXXXXX")"
  unzip -oq "$z" -d "$d" || { echo "  ⚠️ 압축 해제 실패: $z"; return; }
  md="$(find "$d" -name SKILL.md 2>/dev/null | head -1)"
  if [ -z "$md" ]; then echo "  ⚠️ SKILL.md 없음(스킬 zip 아님): $(basename "$z")"; return; fi
  sdir="$(dirname "$md")"; name="$(basename "$sdir")"
  rm -rf "${DEST:?}/$name"; mkdir -p "$DEST/$name"
  cp -a "$sdir/." "$DEST/$name/"
  echo "  ✅ $name  →  $DEST/$name"
}

for arg in "$@"; do
  [ -f "$arg" ] || { echo "건너뜀(파일 없음): $arg"; continue; }
  case "$arg" in
    *.zip) : ;;
    *) echo "건너뜀(zip 아님): $arg"; continue ;;
  esac
  # 번들(내부에 또 다른 zip)이면 각 내부 zip을 설치, 아니면 그 자체를 스킬로 설치.
  b="$(mktemp -d "$tmp/b.XXXXXX")"; unzip -oq "$arg" -d "$b" || true
  nested="$(find "$b" -maxdepth 2 -name '*.zip' 2>/dev/null || true)"
  if [ -n "$nested" ]; then
    echo "· 번들 감지: $(basename "$arg")"
    while IFS= read -r z; do install_skill_zip "$z"; done <<< "$nested"
  else
    echo "· 스킬: $(basename "$arg")"
    install_skill_zip "$arg"
  fi
done

echo
echo "설치된 스킬(${DEST}):"
ls -1 "$DEST" 2>/dev/null | sed 's/^/  · /' || echo "  (없음)"
cat <<'EOF'

다음 단계:
  1) claude 재시작(또는 새 세션). 스킬은 description으로 자동 트리거됩니다.
  2) 수동 확인:  claude -p 'DIS 분석해줘' --allowedTools WebSearch
  3) 이 프로젝트의 호스트 리서치도 같은 ~/.claude 를 쓰면 자동으로 인식합니다.

참고: 클로드 코드 버전에 따라 스킬 경로가 다를 수 있습니다. 인식이 안 되면
     CLAUDE_SKILLS_DIR 로 경로를 지정하거나 `/help`에서 스킬 위치를 확인하세요.
EOF
