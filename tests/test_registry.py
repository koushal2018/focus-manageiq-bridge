"""Tests for the source registry: atomic save + locked read-modify-write."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from connectors import registry
from connectors.contract import SourceConfig


def _cfg(sid):
    return SourceConfig(source_id=sid, source_type="upload-focus",
                        display_name=sid, location="out/x", credential_ref="demo",
                        schedule="manual")


def test_add_and_remove_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "REGISTRY_PATH", str(tmp_path / "sources.json"))
    registry.save([])
    registry.add_source(_cfg("s1"))
    registry.add_source(_cfg("s2"))
    ids = {s.source_id for s in registry.load()}
    assert ids == {"s1", "s2"}
    registry.remove_source("s1")
    assert {s.source_id for s in registry.load()} == {"s2"}


def test_add_source_upserts_by_id(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "REGISTRY_PATH", str(tmp_path / "sources.json"))
    registry.save([])
    registry.add_source(_cfg("dup"))
    registry.add_source(_cfg("dup"))  # same id again
    assert sum(1 for s in registry.load() if s.source_id == "dup") == 1


def test_save_is_atomic_no_partial_file(tmp_path, monkeypatch):
    # A failed serialization must not corrupt the existing registry: the old
    # file stays intact (os.replace is atomic; temp is discarded on error).
    p = tmp_path / "sources.json"
    monkeypatch.setattr(registry, "REGISTRY_PATH", str(p))
    registry.save([_cfg("good")])
    before = p.read_text()

    class _Unserializable:
        source_id = "boom"
    try:
        registry.save([_Unserializable()])  # _to_dict will AttributeError
    except Exception:
        pass
    # original file unchanged, still valid JSON, no leftover .tmp files
    assert p.read_text() == before
    assert json.loads(p.read_text())[0]["source_id"] == "good"
    leftovers = [f for f in os.listdir(tmp_path) if f.endswith(".tmp")]
    assert leftovers == [], f"leftover temp files: {leftovers}"


def test_concurrent_add_source_no_lost_update(tmp_path, monkeypatch):
    # The flock-guarded read-modify-write must not lose updates under threads.
    import threading
    monkeypatch.setattr(registry, "REGISTRY_PATH", str(tmp_path / "sources.json"))
    registry.save([])

    def add(i):
        registry.add_source(_cfg(f"src-{i}"))

    threads = [threading.Thread(target=add, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    ids = {s.source_id for s in registry.load()}
    assert ids == {f"src-{i}" for i in range(20)}, f"lost updates: got {len(ids)}/20"
