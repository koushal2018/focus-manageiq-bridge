"""Pure-logic tests for the native-FOCUS normalizer's validation."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from normalizer import focus_native_to_focus as n


def _row(**over):
    base = {
        "ServiceCategory": "Compute",
        "BillingCurrency": "USD",
        "ChargeCategory": "Usage",
    }
    base.update(over)
    return base


def test_valid_charge_category_passes():
    out, warns = n.map_row(_row())
    assert out["_fatal"] is False
    assert warns == []


def test_bad_charge_category_is_fatal():
    out, warns = n.map_row(_row(ChargeCategory="Banana"))
    assert out["_fatal"] is True
    assert any("ChargeCategory" in w for w in warns)


def test_empty_charge_category_is_allowed():
    # ChargeCategory empty is not fatal here (only ServiceCategory/currency are
    # FOCUS-mandatory in our gate); empty simply isn't validated against the set.
    out, warns = n.map_row(_row(ChargeCategory=""))
    assert out["_fatal"] is False


def test_commitment_columns_pass_through():
    out, _ = n.map_row(_row(CommitmentDiscountId="sp-demo-1",
                            CommitmentDiscountStatus="Used"))
    assert out["CommitmentDiscountId"] == "sp-demo-1"
    assert out["CommitmentDiscountStatus"] == "Used"
