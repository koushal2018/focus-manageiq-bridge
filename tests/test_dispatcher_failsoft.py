"""The dispatcher must not let one bad source sink the run, and must not
sys.exit on non-conformant data (an upload needs a returnable error)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from connectors import dispatcher, registry, adapters
from connectors.contract import SourceConfig, DiscoveredExport


class _PoisonAdapter:
    source_type = "poison-test"

    def discover(self, cfg):
        return [DiscoveredExport(source_id=cfg.source_id, export_id="x", uri="x")]

    def normalize(self, cfg, export):
        raise RuntimeError("boom")


def test_one_bad_source_does_not_abort(monkeypatch):
    # Register the poison adapter directly into the real registry of adapters,
    # and make the source registry return just the poison source.
    monkeypatch.setitem(adapters.ADAPTERS, "poison-test", _PoisonAdapter())
    sources = [SourceConfig("poison-1", "poison-test", "poison", "x", "demo", "manual")]
    monkeypatch.setattr(registry, "load", lambda: sources)

    result = dispatcher.run()  # must NOT raise SystemExit or RuntimeError

    statuses = {s["source_id"]: s["status"] for s in result["sources"]}
    assert statuses["poison-1"] == "error"
    assert "focus_rows" in result
    assert "nonconformant_categories" in result
