#!/usr/bin/env bash
# Stop hook: surface git work-at-risk so uncommitted/unpushed work is visible
# at the end of every turn (recurring friction this project hit — ENV-1).
# Emits a JSON systemMessage; fail-open and silent when the tree is clean.
set -uo pipefail

cd "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null || exit 0
command -v git >/dev/null 2>&1 || exit 0
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0

# Uncommitted files (exclude the design zips / session-export txt that are
# intentionally never committed, so they don't nag every turn).
uncommitted="$(git status --short 2>/dev/null | grep -vE '\.(zip|txt)$' | wc -l | tr -d ' ')"

# Unpushed commits vs the upstream, if one is configured.
unpushed=0
if git rev-parse --abbrev-ref --symbolic-full-name '@{u}' >/dev/null 2>&1; then
  unpushed="$(git log --oneline '@{u}..' 2>/dev/null | wc -l | tr -d ' ')"
fi

[ "$uncommitted" = "0" ] && [ "$unpushed" = "0" ] && exit 0

msg="git: "
[ "$uncommitted" != "0" ] && msg="${msg}${uncommitted} file(s) uncommitted"
[ "$uncommitted" != "0" ] && [ "$unpushed" != "0" ] && msg="${msg}, "
[ "$unpushed" != "0" ] && msg="${msg}${unpushed} commit(s) unpushed (push from VS Code — ENV-1)"

jq -n --arg m "$msg" '{systemMessage: $m}' 2>/dev/null || echo "{\"systemMessage\": \"$msg\"}"
exit 0
