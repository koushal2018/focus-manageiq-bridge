"""Conformance corpus: the FOCUS spec's OWN normative example datasets
(FOCUS_Spec/specification/data, vendored under fixtures/focus_spec_examples/).

Unlike the Foundation SAMPLE (real-world volume — test_focus_sample_benchmark),
these are tiny hand-built scenario files that encode specific FOCUS edge cases:
commitment-discount variants (upfront %, overage) and SaaS pricing models. If
our validator/normalizer can ingest the spec's own Cost-and-Usage examples with
no unexpected drops, that's the strongest 'we're FOCUS-conformant' evidence.

IMPORTANT dataset boundary (FIN-4): the spec's data/ dir ALSO contains
Contract Commitment and Invoice Detail examples. Those are SEPARATE FOCUS
datasets (spec §3.2), not Cost-and-Usage rows — our pipeline ingests Cost and
Usage, so we deliberately do NOT force-fit them. This test asserts the C&U
examples ingest cleanly AND documents that the others are a different dataset."""
import csv
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CORPUS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "fixtures", "focus_spec_examples")

# Cost-and-Usage-shaped examples (carry our mandatory columns). These MUST ingest.
CU_EXAMPLES = [
    "commitment/flexible_50pct.csv",
    "commitment/overage.csv",
    "commitment/reservation_nopct.csv",
    "saas/seat_based_annual.csv",
    "saas/tiered_committed_min.csv",
]

# Separate FOCUS datasets (Contract Commitment / Invoice Detail) — NOT C&U.
# We assert they are recognized as NOT-C&U, not that we ingest them.
NON_CU_EXAMPLES = [
    "contract/scenario_1.csv",
    "invoice/scenario_1.csv",
]


def test_corpus_present():
    assert os.path.isdir(CORPUS)
    assert glob.glob(os.path.join(CORPUS, "**", "*.csv"), recursive=True)


def test_cost_and_usage_examples_validate_and_normalize():
    from connectors import upload_validate as v
    from normalizer import focus_native_to_focus as n
    for rel in CU_EXAMPLES:
        fn = os.path.join(CORPUS, rel)
        assert os.path.exists(fn), f"missing corpus file {rel}"
        with open(fn, "rb") as f:
            ok, reason = v.validate_focus_csv(f.read())
        assert ok, f"{rel} failed validation: {reason}"
        rows, report = n.normalize_csv(fn)
        fatal = sum(1 for r in report if r["fatal"])
        assert fatal == 0, f"{rel}: {fatal} rows dropped as non-conformant"
        assert rows, f"{rel} normalized to zero rows"


def test_commitment_descriptive_columns_in_our_set():
    """The commitment examples carry Category/Type/Quantity/Unit, not just
    id/status — our emitted set must cover them (the FIN-4 shortcut fix)."""
    from normalizer.focus_spec import FOCUS_COLUMNS_V1_3
    for c in ("CommitmentDiscountCategory", "CommitmentDiscountType",
              "CommitmentDiscountQuantity", "CommitmentDiscountUnit",
              "ChargeClass"):
        assert c in FOCUS_COLUMNS_V1_3, f"{c} missing from our FOCUS column set"


def test_non_cost_and_usage_examples_are_a_different_dataset():
    """Document the boundary: contract-commitment / invoice-detail examples are
    NOT Cost-and-Usage and are correctly NOT ingestable by the C&U validator.
    This is honest scope, not a bug — they belong to other FOCUS datasets."""
    from connectors import upload_validate as v
    for rel in NON_CU_EXAMPLES:
        fn = os.path.join(CORPUS, rel)
        if not os.path.exists(fn):
            continue
        with open(fn, "rb") as f:
            ok, _ = v.validate_focus_csv(f.read())
        assert not ok, (f"{rel} unexpectedly passed C&U validation — if a spec "
                        "example is genuinely C&U, move it to CU_EXAMPLES")
