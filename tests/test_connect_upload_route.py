"""Route test for the upload endpoint using FastAPI's TestClient."""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from web.app import app

client = TestClient(app)

HEADER = ("ServiceCategory,BillingCurrency,BilledCost,ChargePeriodStart,"
          "ChargePeriodEnd,ServiceProviderName,ResourceId\n")
_ROW = "Compute,USD,1.5,2026-06-01T00:00:00+00:00,2026-06-02T00:00:00+00:00,AWS,i-demo\n"


def test_upload_rejects_non_focus(tmp_path, monkeypatch):
    # Isolate registry to avoid writing to shared sources.json
    monkeypatch.setattr("connectors.registry.REGISTRY_PATH", str(tmp_path / "sources.json"))
    bad = io.BytesIO(b"not,a,focus\n1,2,3\n")
    r = client.post("/connect/upload",
                    data={"source_id": "test-upload-bad"},
                    files={"file": ("bad.csv", bad, "text/csv")})
    assert r.status_code == 400
    assert "missing required FOCUS column" in r.json()["error"]


def test_upload_accepts_focus(tmp_path, monkeypatch):
    from connectors import adapters
    monkeypatch.setattr(adapters, "UPLOAD_ROOT", str(tmp_path))
    # Stub the load pipeline so it doesn't touch the real DB
    monkeypatch.setattr("connectors.router._load_and_join", lambda: None)
    # Isolate the registry so it doesn't write the shared sources.json
    monkeypatch.setattr("connectors.registry.REGISTRY_PATH", str(tmp_path / "sources.json"))
    good = io.BytesIO((HEADER + _ROW).encode())
    r = client.post("/connect/upload",
                    data={"source_id": "test-upload-ok"},
                    files={"file": ("good.csv", good, "text/csv")})
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_upload_rejects_traversal_source_id(tmp_path, monkeypatch):
    # SEC-1: a source_id with path separators / traversal must be rejected
    # BEFORE any file is written — it would otherwise escape the inbox dir.
    from connectors import adapters
    monkeypatch.setattr(adapters, "UPLOAD_ROOT", str(tmp_path))
    monkeypatch.setattr("connectors.router._load_and_join", lambda: None)
    monkeypatch.setattr("connectors.registry.REGISTRY_PATH", str(tmp_path / "sources.json"))
    good = io.BytesIO((HEADER + _ROW).encode())
    r = client.post("/connect/upload",
                    data={"source_id": "../../etc/evil"},
                    files={"file": ("good.csv", good, "text/csv")})
    assert r.status_code == 400
    assert "source_id" in r.json()["error"]
    # nothing escaped the sandbox tmp_path
    import os
    assert not os.path.exists(os.path.join(str(tmp_path), "..", "etc"))


def test_upload_rejects_oversize(tmp_path, monkeypatch):
    # The synchronous path caps upload size to protect the worker (W-15).
    from connectors import adapters, router
    monkeypatch.setattr(adapters, "UPLOAD_ROOT", str(tmp_path))
    monkeypatch.setattr("connectors.router._load_and_join", lambda: None)
    monkeypatch.setattr("connectors.registry.REGISTRY_PATH", str(tmp_path / "sources.json"))
    monkeypatch.setattr(router, "_MAX_UPLOAD_BYTES", 1024)  # tiny cap for the test
    big = io.BytesIO((HEADER + _ROW * 5000).encode())  # > 1 KiB
    r = client.post("/connect/upload",
                    data={"source_id": "test-upload-big"},
                    files={"file": ("big.csv", big, "text/csv")})
    assert r.status_code == 413
    assert "limit" in r.json()["error"].lower()
