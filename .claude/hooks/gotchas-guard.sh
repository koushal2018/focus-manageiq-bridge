#!/usr/bin/env bash
# PostToolUse(Edit|Write) GOTCHAS.md integrity guard. GOTCHAS.md is the
# PoC's primary deliverable: 100+ entries with `### <FAMILY>-<n>.` IDs that
# other entries and EBA-BACKLOG.md cross-reference. A duplicate ID or a
# malformed header (easy across compacted/parallel sessions) silently
# corrupts it. WARNS (never blocks), matching currency-tripwire. Fail-open.
set -uo pipefail

f="$(jq -r '.tool_response.filePath // .tool_input.file_path // empty' 2>/dev/null)"
[ -n "$f" ] || exit 0
case "$f" in *GOTCHAS.md) ;; *) exit 0 ;; esac
[ -f "$f" ] || exit 0

warn=""

# Duplicate IDs (IDs may carry a lowercase suffix, e.g. B-1b).
dups="$(grep -oE '^### [A-Z]+-[0-9]+[a-z]?' "$f" 2>/dev/null | sort | uniq -d)"
[ -n "$dups" ] && warn="${warn}  duplicate IDs: $(echo "$dups" | tr '\n' ' ')\n"

# Malformed entry headers: '### ' lines not matching '### FAMILY-n. Title'.
# (Backlog-style sub-numbering like B-0.5 lives in EBA-BACKLOG.md, not here.)
bad="$(grep -nE '^### ' "$f" 2>/dev/null | grep -vE '^[0-9]+:### [A-Z]+-[0-9]+[a-z]?\. ' | head -3)"
[ -n "$bad" ] && warn="${warn}  malformed headers:\n$(echo "$bad" | sed 's/^/    /')\n"

if [ -n "$warn" ]; then
  {
    echo "⚠ GOTCHAS.md integrity ($f):"
    printf '%b' "$warn"
    echo "  Fix now — cross-references and the EBA handoff depend on clean IDs."
    echo "  Next free ID per family: bash .claude/skills/gotcha/scripts/next_id.sh"
  } >&2
fi
exit 0   # never block — warning only
