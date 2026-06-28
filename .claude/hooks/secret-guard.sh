#!/usr/bin/env bash
# PreToolUse(Bash) secret-leak guard. Fires only on git commit/push (the hook's
# `if` filter already scopes it). Scans STAGED content for credential patterns;
# blocks with exit 2 + a message on stdin-less stderr if anything matches.
# Fail-OPEN on its own errors (never block a commit because the hook broke),
# but fail-CLOSED on a positive match (that's the whole point).
set -uo pipefail

cd "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null || exit 0
command -v git >/dev/null 2>&1 || exit 0

# Only inspect what's actually staged for this commit.
staged="$(git diff --cached 2>/dev/null)" || exit 0
[ -n "$staged" ] || exit 0

# Credential signatures. Kept deliberately tight to avoid false positives:
#   - AWS access key IDs (AKIA/ASIA + 16 base32)
#   - a populated Basic-auth header ("Basic <base64>")
#   - an assigned Basic-auth password env (BASIC_AUTH_PASS=<nonempty>)
#   - PEM private-key headers
patterns='(AKIA|ASIA)[A-Z0-9]{16}|Basic [A-Za-z0-9+/]{16,}={0,2}|BASIC_AUTH_PASS=[^[:space:]"'"'"']+|-----BEGIN [A-Z ]*PRIVATE KEY-----'

# Only consider ADDED lines (leading '+'), not context/removed.
hits="$(printf '%s\n' "$staged" | grep -E '^\+' | grep -nE "$patterns" 2>/dev/null)" || true

if [ -n "$hits" ]; then
  {
    echo "BLOCKED: staged changes look like they contain a secret."
    echo "Matched lines:"
    printf '%s\n' "$hits" | sed 's/^/  /' | head -8
    echo
    echo "Redact the credential (template/placeholder), unstage it, or store it"
    echo "in a git-ignored file (.env) / Secrets Manager. See GOTCHAS CX-6 / G-1."
  } >&2
  exit 2   # exit 2 = block the tool call
fi
exit 0
