"""Tests for the UploadSource adapter (filesystem inbox, watermark)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from connectors import adapters
from connectors.contract import SourceConfig


def _cfg(sid):
    return SourceConfig(source_id=sid, source_type="upload-focus",
                        display_name=sid, location="", credential_ref="demo",
                        schedule="manual")


def test_inbox_dir_created(tmp_path, monkeypatch):
    monkeypatch.setattr(adapters, "UPLOAD_ROOT", str(tmp_path))
    d = adapters.inbox_dir("src-1")
    assert os.path.isdir(d)
    assert d.endswith(os.path.join("src-1"))


def test_discover_lists_csv_files(tmp_path, monkeypatch):
    monkeypatch.setattr(adapters, "UPLOAD_ROOT", str(tmp_path))
    d = adapters.inbox_dir("src-2")
    with open(os.path.join(d, "export.csv"), "w") as f:
        f.write("ServiceCategory,BillingCurrency\nCompute,USD\n")
    src = adapters.UploadSource()
    found = src.discover(_cfg("src-2"))
    assert len(found) == 1
    assert found[0].export_id == "export.csv"


def test_registered_in_adapters():
    assert "upload-focus" in adapters.ADAPTERS
    assert adapters.ADAPTERS["upload-focus"].source_type == "upload-focus"
