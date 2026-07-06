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


def test_discover_returns_whole_inbox(tmp_path, monkeypatch):
    # The inbox is the source of truth for the partition: discover() returns
    # EVERY distinct-content file so a partition-replace load rebuilds the full
    # partition. Repeated discover() is stable (no cross-run watermark to hide
    # files), so re-dispatch and re-seed are idempotent and never lose uploads.
    monkeypatch.setattr(adapters, "UPLOAD_ROOT", str(tmp_path))
    d = adapters.inbox_dir("src-wm")
    src = adapters.UploadSource()
    with open(os.path.join(d, "first.csv"), "w") as f:
        f.write("ServiceCategory,BillingCurrency\nCompute,USD\n")
    assert len(src.discover(_cfg("src-wm"))) == 1
    # a second dispatch still sees it — the inbox, not a watermark, drives it
    assert len(src.discover(_cfg("src-wm"))) == 1
    # a DIFFERENT-CONTENT file is now ALSO present; both are returned together
    with open(os.path.join(d, "second.csv"), "w") as f:
        f.write("ServiceCategory,BillingCurrency\nStorage,USD\n")
    found = sorted(e.export_id for e in src.discover(_cfg("src-wm")))
    assert found == ["first.csv", "second.csv"]


def test_discover_dedupes_identical_content_within_inbox(tmp_path, monkeypatch):
    # Byte-identical content uploaded twice under different names counts once,
    # so a duplicate upload doesn't double the partition.
    monkeypatch.setattr(adapters, "UPLOAD_ROOT", str(tmp_path))
    d = adapters.inbox_dir("src-dup")
    src = adapters.UploadSource()
    body = "ServiceCategory,BillingCurrency\nCompute,USD\n"
    with open(os.path.join(d, "a.csv"), "w") as f:
        f.write(body)
    with open(os.path.join(d, "b.csv"), "w") as f:  # same bytes, new name
        f.write(body)
    found = src.discover(_cfg("src-dup"))
    assert len(found) == 1  # identical content deduped within the inbox
