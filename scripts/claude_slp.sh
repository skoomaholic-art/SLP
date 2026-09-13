#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
TOOLS_ROOT="${XDG_CACHE_HOME:-$HOME/.cache}/slp-claude-code"
TOOLS_REPO="https://github.com/skoomaholic-art/claude-code.git"
TOOLS_REF="${SLP_CLAUDE_TOOLS_REF:-main}"
PERMISSION_MODE="${SLP_CLAUDE_PERMISSION_MODE:-auto}"

# Avoid an extra LLM security review after every turn by default. Pattern rules
# and the commit-time review stay enabled; users can opt back in explicitly.
export ENABLE_STOP_REVIEW="${ENABLE_STOP_REVIEW:-0}"

cd "$ROOT"

if ! command -v claude >/dev/null 2>&1; then
  echo "Claude Code is not installed." >&2
  echo "Install it with: curl -fsSL https://claude.ai/install.sh | bash" >&2
  echo "Then run: claude doctor && claude auth login" >&2
  exit 127
fi

if [[ -d "$TOOLS_ROOT/.git" ]]; then
  git -C "$TOOLS_ROOT" fetch --depth=1 origin "$TOOLS_REF"
  git -C "$TOOLS_ROOT" reset --hard FETCH_HEAD >/dev/null
else
  rm -rf "$TOOLS_ROOT"
  git clone --depth=1 --branch "$TOOLS_REF" "$TOOLS_REPO" "$TOOLS_ROOT"
fi

plugins=(
  feature-dev
  code-review
  pr-review-toolkit
  security-guidance
  commit-commands
  ralph-wiggum
)

plugin_args=()
for plugin in "${plugins[@]}"; do
  path="$TOOLS_ROOT/plugins/$plugin"
  manifest="$path/.claude-plugin/plugin.json"
  if [[ ! -f "$manifest" ]]; then
    echo "Missing Claude plugin manifest: $manifest" >&2
    exit 2
  fi
  plugin_args+=(--plugin-dir "$path")
done

echo "SLP Claude tools ref: $(git -C "$TOOLS_ROOT" rev-parse --short HEAD)"
echo "Permission mode: $PERMISSION_MODE"
echo "Security stop review: $ENABLE_STOP_REVIEW"
echo "Workspace: $ROOT"

exec claude \
  --name "SLP" \
  --worktree \
  --permission-mode "$PERMISSION_MODE" \
  "${plugin_args[@]}" \
  "$@"
