"""Realism tests for the native-FOCUS generators. Deterministic at SCALE=1."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generators import focus_native
from normalizer import focus_spec


def _rows(monkeypatch, gen, scale="1", days=2):
    monkeypatch.setenv("FOCUS_GEN_SCALE", scale)
    rows, cols = gen(days=days)
    return rows, cols


def test_full_charge_category_mix_present(monkeypatch):
    rows, _ = _rows(monkeypatch, focus_native.generate_aws)
    cats = {r.get("ChargeCategory", "") for r in rows}
    for needed in ("Usage", "Tax", "Purchase", "Credit", "Refund"):
        assert needed in cats, f"missing ChargeCategory {needed}"


def test_commitment_rows_have_cost_spread(monkeypatch):
    rows, _ = _rows(monkeypatch, focus_native.generate_aws)
    covered = [r for r in rows if str(r.get("CommitmentDiscountId", ""))]
    assert covered, "expected commitment-covered rows"
    # at least one row where EffectiveCost < BilledCost (real coverage)
    assert any(float(r["EffectiveCost"]) < float(r["BilledCost"]) for r in covered)


def test_scale_multiplies_volume(monkeypatch):
    small, _ = _rows(monkeypatch, focus_native.generate_aws, scale="1")
    big, _ = _rows(monkeypatch, focus_native.generate_aws, scale="4")
    assert len(big) > len(small)


def test_deterministic(monkeypatch):
    a, _ = _rows(monkeypatch, focus_native.generate_azure)
    b, _ = _rows(monkeypatch, focus_native.generate_azure)
    assert len(a) == len(b)
    assert [r.get("BilledCost") for r in a] == [r.get("BilledCost") for r in b]


def test_azure_keeps_mixed_currency(monkeypatch):
    rows, _ = _rows(monkeypatch, focus_native.generate_azure)
    usage = [r for r in rows if r.get("ChargeCategory") == "Usage"
             and r.get("ServiceCategory") == "Compute"]
    assert any(r.get("BillingCurrency") == "AED" for r in usage)


def test_columns_include_commitment(monkeypatch):
    _, cols = _rows(monkeypatch, focus_native.generate_aws)
    assert "CommitmentDiscountId" in cols
    assert "CommitmentDiscountStatus" in cols
