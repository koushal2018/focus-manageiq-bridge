"""Route test for the upload endpoint using FastAPI's TestClient."""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from web.app import app

client = TestClient(app)

HEADER = ("ServiceCategory,BillingCurrency,BilledCost,ChargePeriodStart,"
          "ServiceProviderName,ResourceId\n")


def test_upload_rejects_non_focus():
    bad = io.BytesIO(b"not,a,focus\n1,2,3\n")
    r = client.post("/connect/upload",
                    data={"source_id": "test-upload-bad"},
                    files={"file": ("bad.csv", bad, "text/csv")})
    assert r.status_code == 400
    assert "missing required FOCUS column" in r.json()["error"]


def test_upload_accepts_focus(tmp_path, monkeypatch):
    from connectors import adapters
    monkeypatch.setattr(adapters, "UPLOAD_ROOT", str(tmp_path))
    good = io.BytesIO((HEADER + "Compute,USD,1.5,2026-06-01T00:00:00+00:00,AWS,i-demo\n").encode())
    r = client.post("/connect/upload",
                    data={"source_id": "test-upload-ok"},
                    files={"file": ("good.csv", good, "text/csv")})
    assert r.status_code == 200
    assert r.json()["ok"] is True
