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
        # provider-native billing formats (historical path)
        AwsCurAdapter(), AzureExportAdapter(), OciUsageAdapter(),
    )
}
