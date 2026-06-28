"""Pure-logic tests for the FOCUS spec constants (no DB)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from normalizer import focus_spec


def test_charge_categories_closed_set():
    # FOCUS v1.3 ChargeCategory closed set (spec 3.x). Case-sensitive.
    assert focus_spec.CHARGE_CATEGORIES_V1_3 == {
        "Usage", "Purchase", "Tax", "Credit", "Adjustment", "Refund",
    }


def test_commitment_columns_present():
    cols = focus_spec.FOCUS_COLUMNS_V1_3
    assert "CommitmentDiscountId" in cols
    assert "CommitmentDiscountStatus" in cols
    # Tags stays present; commitment columns come after it.
    assert cols.index("CommitmentDiscountId") > cols.index("Tags")
