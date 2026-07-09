#!/usr/bin/env bash
# next_id.sh FAMILY [GOTCHAS_PATH]
# Prints the next free gotcha ID for a family (e.g. `next_id.sh FIN` -> FIN-6).
# Families are the uppercase prefixes of `### <FAMILY>-<n>.` headers in GOTCHAS.md.
# With no args, lists every family with its current max, so you can pick
# the right family (or see that a new one is warranted) before allocating.
set -euo pipefail

GOTCHAS="${2:-$(git -C "$(dirname "$0")" rev-parse --show-toplevel 2>/dev/null || echo .)/GOTCHAS.md}"
[ -f "$GOTCHAS" ] || { echo "ERROR: $GOTCHAS not found" >&2; exit 1; }

if [ $# -eq 0 ]; then
  echo "Existing families (family: max id):"
  grep -oE '^### [A-Z]+-[0-9]+' "$GOTCHAS" | awk '{print $2}' \
    | awk -F- '{ if ($2+0 > max[$1]) max[$1]=$2 } END { for (f in max) printf "  %s: %s-%d\n", f, f, max[f] }' \
    | sort
  exit 0
fi

FAMILY="$1"
[[ "$FAMILY" =~ ^[A-Z]+$ ]] || { echo "ERROR: family must be uppercase letters, got '$FAMILY'" >&2; exit 1; }

MAX=$(grep -oE "^### ${FAMILY}-[0-9]+" "$GOTCHAS" | grep -oE '[0-9]+$' | sort -n | tail -1 || true)
echo "${FAMILY}-$(( ${MAX:-0} + 1 ))"
