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


def test_discover_dedupes_by_content_hash(tmp_path, monkeypatch):
    monkeypatch.setattr(adapters, "UPLOAD_ROOT", str(tmp_path))
    d = adapters.inbox_dir("src-wm")
    src = adapters.UploadSource()
    with open(os.path.join(d, "first.csv"), "w") as f:
        f.write("ServiceCategory,BillingCurrency\nCompute,USD\n")
    # first discover sees it; then mark ingested
    assert len(src.discover(_cfg("src-wm"))) == 1
    src.advance_watermark(_cfg("src-wm"))
    # already-ingested content is NOT rediscovered
    assert src.discover(_cfg("src-wm")) == []
    # a DIFFERENT-CONTENT file IS discovered (distinct hash)
    with open(os.path.join(d, "second.csv"), "w") as f:
        f.write("ServiceCategory,BillingCurrency\nStorage,USD\n")
    found = src.discover(_cfg("src-wm"))
    assert len(found) == 1 and found[0].export_id == "second.csv"


def test_discover_identical_content_is_noop(tmp_path, monkeypatch):
    # Re-uploading byte-identical content under a new name must NOT re-ingest
    # (an mtime watermark would have re-ingested it; content-hash does not).
    monkeypatch.setattr(adapters, "UPLOAD_ROOT", str(tmp_path))
    d = adapters.inbox_dir("src-dup")
    src = adapters.UploadSource()
    body = "ServiceCategory,BillingCurrency\nCompute,USD\n"
    with open(os.path.join(d, "a.csv"), "w") as f:
        f.write(body)
    assert len(src.discover(_cfg("src-dup"))) == 1
    src.advance_watermark(_cfg("src-dup"))
    with open(os.path.join(d, "b.csv"), "w") as f:  # same bytes, new name
        f.write(body)
    assert src.discover(_cfg("src-dup")) == []  # identical content → no-op
