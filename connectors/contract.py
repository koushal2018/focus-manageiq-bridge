"""The SourceAdapter contract every connector implements.

A source adapter knows how to: discover new exports for a registered source,
fetch their raw rows (TLS-verified — never verify=False, G-6), normalize to
FOCUS v1.3, and report conformance. The dispatcher calls these in order and
never needs to know which provider it's dealing with.
"""
from __future__ import annotations

import dataclasses
from typing import Protocol, runtime_checkable


@dataclasses.dataclass(frozen=True)
class SourceConfig:
    """One registered data source. This is what 'connect a source' writes.

    No secrets inline --- credential_ref points at a secret store entry
    (AWS Secrets Manager ARN in production). location is provider-specific:
    an S3 prefix for AWS CUR, a blob URL for Azure, an object-store prefix
    for OCI, a REST base URL for ManageIQ.
    """
    source_id: str            # stable unique id, e.g. "aws-payer-9999"
    source_type: str          # "aws-cur" | "azure-export" | "oci-usage" | "manageiq"
    display_name: str         # human label for the UI
    location: str             # where the export lives (path / url / prefix)
    credential_ref: str       # secret store reference (NEVER a raw secret)
    schedule: str             # cron-ish or interval token; dispatcher honors it
    enabled: bool = True


@dataclasses.dataclass
class DiscoveredExport:
    """A single export instance the adapter found at the source location."""
    source_id: str
    export_id: str            # provider's id for this export (file key, etc.)
    uri: str                  # concrete fetchable location
    period_hint: str = ""     # billing period if the provider exposes it


@dataclasses.dataclass
class NormalizeResult:
    """Output of an adapter run: FOCUS rows + the row-level conformance report."""
    focus_rows: list[dict]
    report: list[dict]        # one entry per source row (fatal/warnings)

    @property
    def loaded(self) -> int:
        return len(self.focus_rows)

    @property
    def dropped(self) -> int:
        return sum(1 for r in self.report if r.get("fatal"))


@runtime_checkable
class SourceAdapter(Protocol):
    """Implemented once per source TYPE. Registered in registry.ADAPTERS."""

    source_type: str

    def discover(self, cfg: SourceConfig) -> list[DiscoveredExport]:
        """Find exports not yet ingested for this source. PoC adapters treat
        the local CSV as a single always-present export; cloud adapters list
        S3/blob objects newer than the last watermark."""
        ...

    def normalize(self, cfg: SourceConfig, export: DiscoveredExport) -> NormalizeResult:
        """Fetch + map to FOCUS v1.3. Wraps the PoC normalizer for this type."""
        ...
