"""Upload-time FOCUS validation — reject garbage at the door, before it ever
enters the inbox or the pipeline. A file that fails here is never written.

This is deliberately a HEADER + non-empty check, not full row conformance:
row-level conformance is the normalizer's job (it reports + drops bad rows),
and the post-load conformance validator is the authoritative gate. The point
here is to fail fast on 'this isn't a FOCUS export at all'."""
from __future__ import annotations

import csv
import io

# The minimal FOCUS columns a credible export must declare. This MUST stay in
# lockstep with the loader's pre-commit conformance guard (db/loader.py
# _LOAD_MANDATORY_NONNULL) and web.queries _FOCUS_MANDATORY_NONNULL — because
# the load is a destructive TRUNCATE+reload, a file that passes upload but
# fails the load gate would roll back and (correctly) preserve the old
# warehouse, but the user's upload silently achieves nothing. Rejecting the
# load-mandatory set HERE, at the door, makes that failure visible early.
# ChargePeriodEnd in particular: header-only validation let a file with
# ChargePeriodStart-but-no-ChargePeriodEnd through, and it only failed at the
# post-load gate (GOTCHA W-14). It is load-mandatory, so it is upload-mandatory.
MANDATORY = ["ServiceCategory", "BillingCurrency", "BilledCost",
             "ChargePeriodStart", "ChargePeriodEnd", "ServiceProviderName"]


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
    # Accept deprecated FOCUS column names as satisfying their current-name
    # requirement (FIN-3): the FinOps Foundation's 1.0 sample uses ProviderName
    # for ServiceProviderName etc. The normalizer levels these on load, so a
    # file carrying the older name IS valid — rejecting it would turn away real
    # reference FOCUS data. Build the set of accepted aliases for each mandatory.
    from normalizer.focus_spec import DEPRECATED_COLUMN_ALIASES
    _current_to_deprecated: dict[str, list[str]] = {}
    for old, new in DEPRECATED_COLUMN_ALIASES.items():
        _current_to_deprecated.setdefault(new, []).append(old)

    def _present(col: str) -> bool:
        return col in cols or any(d in cols for d in _current_to_deprecated.get(col, []))

    missing = [c for c in MANDATORY if not _present(c)]
    if missing:
        return False, f"missing required FOCUS column(s): {', '.join(missing)}"
    if next(reader, None) is None:
        return False, "header present but no data rows"
    return True, ""
