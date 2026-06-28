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


def _local_export(cfg: SourceConfig) -> list[DiscoveredExport]:
    """PoC discover(): the configured location is a CSV path on disk. If it
    exists, it's the single export. Production overrides this with object-store
    listing; normalize() is unchanged."""
    path = cfg.location
    if not os.path.isabs(path):
        path = os.path.join(ROOT, path)
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
    lists *.csv in the source's inbox that have NOT been ingested before;
    normalize() is the same native-FOCUS mapping every other source uses — the
    only difference from a future S3 source is WHERE the bytes came from.

    Dedupe is by CONTENT HASH, not mtime: a `.ingested` sidecar in the inbox
    records the sha256 of every file already ingested. This is robust to the
    mtime pitfalls (1–2s filesystem resolution, clock skew / NTP correction,
    same-name re-uploads) that an mtime watermark silently mis-handles, and it
    means re-uploading byte-identical content is correctly a no-op while a
    CHANGED file with the same name is re-ingested (its hash differs)."""
    source_type = "upload-focus"

    def _ingested_path(self, source_id: str) -> str:
        return os.path.join(inbox_dir(source_id), ".ingested")

    def _ingested_hashes(self, source_id: str) -> set[str]:
        import json
        p = self._ingested_path(source_id)
        if not os.path.exists(p):
            return set()
        try:
            with open(p) as f:
                return set(json.load(f))
        except (ValueError, OSError):
            return set()

    def discover(self, cfg: SourceConfig) -> list[DiscoveredExport]:
        d = inbox_dir(cfg.source_id)
        seen = self._ingested_hashes(cfg.source_id)
        found = []
        for name in sorted(os.listdir(d)):
            if not name.endswith(".csv"):
                continue
            p = os.path.join(d, name)
            if _sha256_file(p) in seen:
                continue  # byte-identical content already ingested
            found.append(DiscoveredExport(source_id=cfg.source_id,
                                          export_id=name, uri=p))
        return found

    def mark_ingested(self, cfg: SourceConfig, paths: list[str]) -> None:
        """Record the content hash of ONLY the given just-ingested files, so
        identical content isn't re-ingested next dispatch. Marking only the
        specific files (not every *.csv in the inbox) avoids a race where a
        concurrent upload's not-yet-ingested file gets marked seen and silently
        dropped (review finding). Best-effort per file."""
        import json
        seen = self._ingested_hashes(cfg.source_id)
        for p in paths:
            try:
                if os.path.exists(p) and p.endswith(".csv"):
                    seen.add(_sha256_file(p))
            except OSError:
                continue
        with open(self._ingested_path(cfg.source_id), "w") as f:
            json.dump(sorted(seen), f)

    def advance_watermark(self, cfg: SourceConfig) -> None:
        """DEPRECATED (race-prone): marks EVERY *.csv in the inbox as ingested.
        Retained only for callers that ingest the whole inbox synchronously.
        Prefer mark_ingested(cfg, [paths]) with the specific dispatched files."""
        d = inbox_dir(cfg.source_id)
        self.mark_ingested(cfg, [os.path.join(d, n) for n in os.listdir(d)
                                 if n.endswith(".csv")])

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
