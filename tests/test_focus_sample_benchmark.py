"""Benchmark our synthetic FOCUS data against the FinOps Foundation's official
anonymized sample (FOCUS-Sample-Data, CC BY 4.0, vendored under fixtures/).

Purpose (FIN-3): catch realism regressions automatically. The official sample is
real-world FOCUS, so it is the ground truth for "what columns/shapes real data
has". If we shortcut a column again (like the unit-price omission, FIN-2), or if
real data carries something our pipeline can't ingest (version skew, literal
NULL strings), these tests fail. Pure-logic + filesystem; no DB needed."""
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SAMPLE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "fixtures", "focus_foundation_sample", "focus_sample_1000.csv")


def _sample_cols():
    with open(SAMPLE) as f:
        return {c.strip() for c in next(csv.reader(f))}


def test_sample_fixture_present():
    assert os.path.exists(SAMPLE), "vendored FinOps Foundation FOCUS sample missing"


def test_our_column_set_covers_the_sample_priced_columns():
    """The columns that make the sample USABLE for FinOps (cost + unit price +
    pricing basis) must ALL be in our emitted set — this is the guard that would
    have caught the FIN-2 unit-price shortcut."""
    from normalizer.focus_spec import FOCUS_COLUMNS_V1_3, DEPRECATED_COLUMN_ALIASES
    ours = set(FOCUS_COLUMNS_V1_3) | set(DEPRECATED_COLUMN_ALIASES)
    must_have = {
        "BilledCost", "EffectiveCost", "ListCost", "ContractedCost",
        "ListUnitPrice", "ContractedUnitPrice", "PricingCategory",
        "PricingQuantity", "PricingUnit", "ConsumedQuantity", "ConsumedUnit",
        "ServiceCategory", "ChargeCategory", "BillingCurrency",
        "CommitmentDiscountId", "CommitmentDiscountStatus",
    }
    sample_cols = _sample_cols()
    # only assert on columns the sample actually has (intersection), so the test
    # tracks the real reference, not an aspirational list.
    target = {c for c in must_have if c in sample_cols}
    missing = target - ours
    assert not missing, f"our FOCUS column set is missing real priced columns: {missing}"


def test_sample_validates_through_our_upload_validator():
    """The Foundation's own FOCUS sample must PASS our upload validation —
    a pipeline that rejects the reference dataset isn't 'handling real FOCUS'.
    (Exercises version-leveling: the sample is 1.0 with ProviderName.)"""
    from connectors import upload_validate as v
    with open(SAMPLE, "rb") as f:
        ok, reason = v.validate_focus_csv(f.read())
    assert ok, f"official FOCUS sample rejected by our validator: {reason}"


def test_sample_normalizes_without_dropping_rows():
    """All 1000 real rows must normalize to FOCUS v1.3 with zero fatal drops —
    proving version-leveling (ProviderName→ServiceProviderName) and the literal-
    NULL handling work on real data."""
    from normalizer import focus_native_to_focus as n
    rows, report = n.normalize_csv(SAMPLE)
    fatal = sum(1 for r in report if r["fatal"])
    assert fatal == 0, f"{fatal} real rows dropped as non-conformant"
    assert len(rows) == 1000
    # ProviderName must have leveled into ServiceProviderName
    assert all((r.get("ServiceProviderName") or "") for r in rows), \
        "ServiceProviderName empty after leveling a FOCUS 1.0 sample"


def test_sample_uses_deprecated_provider_name():
    """Document the version-skew the leveling handles: the reference sample is
    FOCUS 1.0 and carries ProviderName (not ServiceProviderName). If a future
    sample drops this, the leveling is still correct but this test reminds us
    why it exists."""
    cols = _sample_cols()
    assert "ProviderName" in cols and "ServiceProviderName" not in cols
