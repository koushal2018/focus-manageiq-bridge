"""Upload-time FOCUS validation — reject garbage at the door, before it ever
enters the inbox or the pipeline. A file that fails here is never written.

This is deliberately a HEADER + non-empty check, not full row conformance:
row-level conformance is the normalizer's job (it reports + drops bad rows),
and the post-load conformance validator is the authoritative gate. The point
here is to fail fast on 'this isn't a FOCUS export at all'."""
from __future__ import annotations

import csv
import io

# The minimal FOCUS columns a credible export must declare. Subset of
# focus_spec.FOCUS_COLUMNS_V1_3 — the mandatory ones our pipeline depends on.
MANDATORY = ["ServiceCategory", "BillingCurrency", "BilledCost",
             "ChargePeriodStart", "ServiceProviderName"]


def validate_focus_csv(raw: bytes) -> tuple[bool, str]:
    if not raw or not raw.strip():
        return False, "file is empty"
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return False, "file is not UTF-8 text (not a CSV export)"
    try:
        reader = csv.reader(io.StringIO(text))
        header = next(reader, None)
    except csv.Error as e:
        return False, f"not parseable as CSV: {e}"
    if not header:
        return False, "no header row found"
    cols = {c.strip() for c in header}
    missing = [c for c in MANDATORY if c not in cols]
    if missing:
        return False, f"missing required FOCUS column(s): {', '.join(missing)}"
    if next(reader, None) is None:
        return False, "header present but no data rows"
    return True, ""
