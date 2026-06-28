"""Deferred API-pull connectors — the SECOND implementation behind the same
SourceAdapter contract, defined now so the extension surface is concrete.

These are intentionally NOT implemented: a live pull needs real cloud
credentials, which this synthetic-only build never holds, and there is nothing
to verify against on synthetic data. The MVP ingestion path is upload
(UploadSource). When a credentialed deployment wants automated pull, fill in
discover()/normalize() here against:
  - AWS:   Data Exports (FOCUS) delivered to S3 → list new objects under a
           prefix; normalize() reuses focus_native_to_focus.
  - Azure: Cost Management FOCUS export to a storage container → same shape.
The dispatcher already treats a raised error as a per-source 'error' status
(fail-soft), so a registered-but-unfilled source never breaks a run."""
from __future__ import annotations

from connectors.contract import DiscoveredExport, NormalizeResult, SourceConfig

_MSG = "API-pull connector deferred to a later release — use upload for now"


class _StubApiPull:
    source_type = ""

    def discover(self, cfg: SourceConfig) -> list[DiscoveredExport]:
        raise NotImplementedError(_MSG)

    def normalize(self, cfg: SourceConfig, export: DiscoveredExport) -> NormalizeResult:
        raise NotImplementedError(_MSG)


class AwsCostExplorerSource(_StubApiPull):
    source_type = "aws-api-pull"


class AzureExportSource(_StubApiPull):
    source_type = "azure-api-pull"


# Types that are registered but NOT yet live. The Connect UI renders these
# disabled with a 'later release' label so a stub never looks operational.
API_PULL_TYPES = {"aws-api-pull", "azure-api-pull"}
