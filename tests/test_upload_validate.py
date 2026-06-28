"""Pure-logic tests for upload-time FOCUS validation (reject early)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from connectors import upload_validate as v


HEADER = "ServiceCategory,BillingCurrency,BilledCost,ChargePeriodStart,ServiceProviderName"


def test_accepts_conformant_csv():
    raw = (HEADER + "\nCompute,USD,1.23,2026-06-01T00:00:00+00:00,AWS\n").encode()
    ok, reason = v.validate_focus_csv(raw)
    assert ok and reason == ""


def test_rejects_missing_mandatory_column():
    raw = b"ServiceCategory,BillingCurrency\nCompute,USD\n"
    ok, reason = v.validate_focus_csv(raw)
    assert not ok and "BilledCost" in reason


def test_rejects_empty_file():
    ok, reason = v.validate_focus_csv(b"")
    assert not ok and "empty" in reason.lower()


def test_rejects_header_only():
    ok, reason = v.validate_focus_csv((HEADER + "\n").encode())
    assert not ok and "no data" in reason.lower()


def test_rejects_non_csv_binary():
    ok, reason = v.validate_focus_csv(b"\x00\x01\x02not a csv")
    assert not ok
