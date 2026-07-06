"""Built-in source adapters.

Each wraps a PoC normalizer (`normalizer/*_to_focus.py`) behind the
SourceAdapter contract. For the PoC/pilot, `discover()` treats the
generator's local CSV as a single always-present export; in production the
same adapters gain real `discover()` bodies that list S3/blob objects newer
than a watermark — the `normalize()` half does not change, because the FOCUS
mapping is identical regardless of how the file arrived.

This is the concrete proof of the connect-and-run promise: the hard part
(the FOCUS mappings, J-1 join keys) is already done; onboarding is a registry
row + a credential, not new transform code.
"""
from __future__ import annotations

import os

from connectors.contract import DiscoveredExport, NormalizeResult, SourceConfig
from connectors.api_pull import AwsCostExplorerSource, AzureExportSource
from normalizer import aws_to_focus, azure_to_focus, oci_to_focus
from normalizer import focus_native_to_focus

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _location_within_root(location: str) -> bool:
    """A file-adapter `location` must resolve to a path INSIDE the project tree
    — no absolute paths, no '..' traversal — so it can't be an arbitrary-file
    READ primitive (SEC-2). Enforced HERE, at the read choke point, so the guard
    covers EVERY registry row (hand-edited sources.json, a scheduler, a seed),
    not only rows created through the /connect/add route."""
    if not location or os.path.isabs(location):
        return False
    resolved = os.path.realpath(os.path.join(ROOT, location))
    root = os.path.realpath(ROOT)
    return resolved == root or resolved.startswith(root + os.sep)


def _local_export(cfg: SourceConfig) -> list[DiscoveredExport]:
    """PoC discover(): the configured location is a CSV path on disk. If it
    exists AND resolves inside the project tree, it's the single export.
    Production overrides this with object-store listing; normalize() is
    unchanged."""
    if not _location_within_root(cfg.location):
        # A location outside the tree is refused here regardless of how the
        # registry row was created — the trust boundary, not just the UI route.
        raise ValueError(
            f"source {cfg.source_id!r} location {cfg.location!r} is outside the "
            "project data tree (absolute path or '..' traversal refused)")
    path = os.path.join(ROOT, cfg.location)
    if not os.path.exists(path):
        return []
    return [DiscoveredExport(source_id=cfg.source_id, export_id=os.path.basename(path), uri=path)]


UPLOAD_ROOT = os.path.join(ROOT, "out", "uploads")


def inbox_dir(source_id: str) -> str:
    """Per-source upload inbox. The upload endpoint writes validated files here;
    UploadSource.discover() lists them. Created on demand."""
    d = os.path.join(UPLOAD_ROOT, source_id)
    os.makedirs(d, exist_ok=True)
    return d


def _sha256_file(path: str) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class UploadSource:
    """A source whose exports arrive by user upload, not cloud fetch. discover()
    lists EVERY *.csv currently in the source's inbox (deduped by content hash
    WITHIN the inbox — a byte-identical file uploaded twice under different
    names is counted once); normalize() is the same native-FOCUS mapping every
    other source uses — the only difference from a future S3 source is WHERE the
    bytes came from.

    The inbox is the SOURCE OF TRUTH for an upload source's partition: a load
    replaces `focus_costs WHERE source_id=…` with the normalized union of all
    inbox files. This is deliberately NOT a cross-run watermark. A watermark
    (return only files not seen before) is wrong here because the load does a
    full partition REPLACE, not an append: after a watermark hid the earlier
    files, the next upload would rebuild the partition from only the new file
    and silently drop the earlier data — and a re-seed (which re-dispatches all
    sources) would rebuild the partition as empty. Making discover() return the
    whole inbox keeps the partition a pure function of the inbox, so repeated
    dispatch and re-seed are idempotent and every uploaded file is preserved."""
    source_type = "upload-focus"

    def discover(self, cfg: SourceConfig) -> list[DiscoveredExport]:
        d = inbox_dir(cfg.source_id)
        found = []
        seen_hashes: set[str] = set()
        for name in sorted(os.listdir(d)):
            if not name.endswith(".csv"):
                continue
            p = os.path.join(d, name)
            h = _sha256_file(p)
            if h in seen_hashes:
                continue  # same content already in this inbox (dup upload)
            seen_hashes.add(h)
            found.append(DiscoveredExport(source_id=cfg.source_id,
                                          export_id=name, uri=p))
        return found

    def normalize(self, cfg: SourceConfig, export: DiscoveredExport) -> NormalizeResult:
        rows, report = focus_native_to_focus.normalize_csv(export.uri)
        for r in rows:
            r["_source"] = "upload"
        return NormalizeResult(focus_rows=rows, report=report)


class AwsCurAdapter:
    source_type = "aws-cur"

    def discover(self, cfg: SourceConfig) -> list[DiscoveredExport]:
        return _local_export(cfg)

    def normalize(self, cfg: SourceConfig, export: DiscoveredExport) -> NormalizeResult:
        rows, report = aws_to_focus.normalize_csv(export.uri)
        for r in rows:
            r["_source"] = "aws"
        return NormalizeResult(focus_rows=rows, report=report)


class AzureExportAdapter:
    source_type = "azure-export"

    def discover(self, cfg: SourceConfig) -> list[DiscoveredExport]:
        return _local_export(cfg)

    def normalize(self, cfg: SourceConfig, export: DiscoveredExport) -> NormalizeResult:
        rows, report = azure_to_focus.normalize_csv(export.uri)
        for r in rows:
            r["_source"] = "azure"
        return NormalizeResult(focus_rows=rows, report=report)


class OciUsageAdapter:
    source_type = "oci-usage"

    def discover(self, cfg: SourceConfig) -> list[DiscoveredExport]:
        return _local_export(cfg)

    def normalize(self, cfg: SourceConfig, export: DiscoveredExport) -> NormalizeResult:
        rows, report = oci_to_focus.normalize_csv(export.uri)
        for r in rows:
            r["_source"] = "oci"
        return NormalizeResult(focus_rows=rows, report=report)


# --- Native-FOCUS adapters (post-NF-1: providers export FOCUS directly) ---
# These are the PRODUCTION-shaped path: the export is already FOCUS, so
# normalize() is near-identity + version-leveling + gap-fill (see
# normalizer/focus_native_to_focus.py). The CUR/cost-export adapters above
# remain for HISTORICAL data predating native FOCUS exports.

class _NativeFocusAdapter:
    """Shared body; subclasses set source_type + _source tag."""
    source_type = ""
    _source = ""

    def discover(self, cfg: SourceConfig) -> list[DiscoveredExport]:
        return _local_export(cfg)

    def normalize(self, cfg: SourceConfig, export: DiscoveredExport) -> NormalizeResult:
        rows, report = focus_native_to_focus.normalize_csv(export.uri)
        for r in rows:
            r["_source"] = self._source
        return NormalizeResult(focus_rows=rows, report=report)


class AwsFocusExportAdapter(_NativeFocusAdapter):
    source_type = "aws-focus-export"
    _source = "aws"


class AzureFocusExportAdapter(_NativeFocusAdapter):
    source_type = "azure-focus-export"
    _source = "azure"


class OciFocusExportAdapter(_NativeFocusAdapter):
    source_type = "oci-focus-export"
    _source = "oci"


# The registry of source TYPES → adapter instances. Adding a new provider type
# is one line here + one normalizer module. Adding a source INSTANCE is a
# registry row (see registry.py), no code at all.
ADAPTERS: dict[str, object] = {
    a.source_type: a
    for a in (
        # native-FOCUS (current/production path)
        AwsFocusExportAdapter(), AzureFocusExportAdapter(), OciFocusExportAdapter(),
        # user-upload path (real ingestion for the MVP)
        UploadSource(),
        # API-pull (deferred; registered-but-stubbed — see api_pull.py)
        AwsCostExplorerSource(), AzureExportSource(),
        # provider-native billing formats (historical path)
        AwsCurAdapter(), AzureExportAdapter(), OciUsageAdapter(),
    )
}
