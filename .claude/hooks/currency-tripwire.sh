#!/usr/bin/env bash
# PostToolUse(Edit|Write) currency tripwire. The worst bugs in this project
# (B-6, B-7) were SUM/AVG-ing raw billed_cost instead of billed_cost_usd —
# twice, in different modules. This WARNS (never blocks) when a written .py
# aggregates a cost column without the _usd suffix. Heuristic by design:
# miq_onprem_cost is legitimately USD, so this is a nudge to double-check,
# not a gate. Fail-open everywhere.
set -uo pipefail

f="$(jq -r '.tool_response.filePath // .tool_input.file_path // empty' 2>/dev/null)"
[ -n "$f" ] || exit 0
case "$f" in *.py) ;; *) exit 0 ;; esac
[ -f "$f" ] || exit 0

# SUM(...) or AVG(...) over a billed_cost column that is NOT billed_cost_usd.
# \b...\b so billed_cost_usd itself doesn't match.
if grep -nEi '(sum|avg)\s*\(\s*[a-z0-9_.]*billed_cost\b' "$f" 2>/dev/null \
     | grep -viE 'billed_cost_usd' >/tmp/.cur_hits 2>/dev/null && [ -s /tmp/.cur_hits ]; then
  {
    echo "⚠ currency check ($f): aggregates a raw billed_cost column —"
    echo "  confirm this is USD-normalised (billed_cost_usd) and not mixing AED."
    echo "  Legitimate only for miq_onprem_cost (uniformly USD). See GOTCHAS B-7."
    sed 's/^/    /' /tmp/.cur_hits | head -5
  } >&2
fi
rm -f /tmp/.cur_hits 2>/dev/null
exit 0   # never block — warning only
