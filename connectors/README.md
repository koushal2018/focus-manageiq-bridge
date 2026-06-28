# Connector SDK — How to add a new data source

This is the extension surface: how a customer onboards a new cloud provider, a ManageIQ appliance, or any other cost source **without forking the codebase**. Adding a source is configuration (a `SourceConfig` row) plus, for a new provider type, one adapter module implementing the `SourceAdapter` Protocol.

## What a connector does

Every connector translates raw billing data into FOCUS v1.3 rows. The dispatcher (`dispatcher.py`) orchestrates this in two steps:

1. **`discover(cfg)`** — list new exports not yet ingested (idempotent; watermark pattern)
2. **`normalize(cfg, export)`** — fetch the raw export and map it to FOCUS

The dispatcher is fail-soft: if a source raises an error, that source gets status `"error"` in the summary and the run continues with other sources. One poison source never sinks the whole ingestion.

---

## The contract types

All three are defined in `connectors/contract.py`.

### `SourceConfig`

What "connect a source" writes to the registry. One row per source instance (e.g., "aws-payer-9999", "miq-prod-east").

| Field | Type | Meaning |
|-------|------|---------|
| `source_id` | `str` | Stable unique ID for this source instance |
| `source_type` | `str` | Which adapter to use (e.g., `"aws-cur"`, `"azure-export"`, `"upload-focus"`) |
| `display_name` | `str` | Human label shown in the UI |
| `location` | `str` | Where the export lives — provider-specific (S3 prefix, blob URL, file path, REST base URL) |
| `credential_ref` | `str` | Secret store reference (AWS Secrets Manager ARN). **Never an inline secret** |
| `schedule` | `str` | Cron-ish or interval token; the dispatcher honors it |
| `enabled` | `bool` | Whether the source runs (default: `True`) |

### `DiscoveredExport`

A single export instance the adapter found at the source location.

| Field | Type | Meaning |
|-------|------|---------|
| `source_id` | `str` | The parent source ID |
| `export_id` | `str` | Provider's identifier for this export (file key, billing period ID, etc.) |
| `uri` | `str` | Concrete fetchable location (local path, signed URL, etc.) |
| `period_hint` | `str` | Billing period if the provider exposes it (optional) |

### `NormalizeResult`

Output of a successful `normalize()` call: FOCUS rows + a row-level conformance report.

| Field | Type | Meaning |
|-------|------|---------|
| `focus_rows` | `list[dict]` | The mapped FOCUS rows (keys = FOCUS v1.3 column display names) |
| `report` | `list[dict]` | One entry per source row; each has `"fatal"` and/or `"warnings"` keys |

The `NormalizeResult` also exposes:
- `.loaded` — count of rows successfully mapped
- `.dropped` — count of rows that had fatal errors and were excluded

---

## The adapter contract (`SourceAdapter` Protocol)

Defined in `connectors/contract.py` as a `@runtime_checkable` Protocol. Every adapter implements two methods.

### `discover(cfg: SourceConfig) -> list[DiscoveredExport]`

**Find exports not yet ingested.** Must be idempotent — calling it twice without running a load in between returns the same list.

The PoC adapters treat the generator's local CSV as a single always-present export. In production, this becomes an object-store listing (S3/Azure Blob) filtered by a watermark. The reference implementation is **`UploadSource`** in `connectors/adapters.py`:

```python
def discover(self, cfg: SourceConfig) -> list[DiscoveredExport]:
    d = inbox_dir(cfg.source_id)
    wm_path = os.path.join(d, ".watermark")
    watermark = os.path.getmtime(wm_path) if os.path.exists(wm_path) else 0.0
    found = []
    for name in sorted(os.listdir(d)):
        if not name.endswith(".csv"):
            continue
        p = os.path.join(d, name)
        if os.path.getmtime(p) <= watermark:
            continue  # already ingested
        found.append(DiscoveredExport(source_id=cfg.source_id,
                                      export_id=name, uri=p))
    return found
```

After successfully loading an export, the adapter should advance its watermark (e.g., `UploadSource.advance_watermark(cfg)`).

### `normalize(cfg: SourceConfig, export: DiscoveredExport) -> NormalizeResult`

**Fetch and map to FOCUS v1.3.** This wraps the provider-specific normalizer (see `normalizer/*_to_focus.py`) behind the contract.

**Rules:**

1. **Row keys must be FOCUS v1.3 column display names** from `normalizer.focus_spec.FOCUS_COLUMNS_V1_3` (e.g., `"BilledCost"`, `"ServiceCategory"`, `"ChargePeriodStart"`).
2. **Set `r["_source"]`** to a short provider tag (`"aws"`, `"azure"`, `"oci"`, `"upload"`). The loader writes this to the `_source` column for origin tracking.
3. **Return a `NormalizeResult`** with `focus_rows` (the mapped rows) and `report` (the conformance report from the normalizer).

Example (minimal adapter reading a local CSV, mirroring `UploadSource`):

```python
from connectors.contract import DiscoveredExport, NormalizeResult, SourceConfig
from normalizer import focus_native_to_focus

class MyCustomSource:
    source_type = "my-custom-source"
    
    def discover(self, cfg: SourceConfig) -> list[DiscoveredExport]:
        # Return the single CSV as an export if it exists
        if os.path.exists(cfg.location):
            return [DiscoveredExport(
                source_id=cfg.source_id,
                export_id=os.path.basename(cfg.location),
                uri=cfg.location
            )]
        return []
    
    def normalize(self, cfg: SourceConfig, export: DiscoveredExport) -> NormalizeResult:
        # Reuse the native-FOCUS normalizer (or write a custom one)
        rows, report = focus_native_to_focus.normalize_csv(export.uri)
        for r in rows:
            r["_source"] = "my-custom"
        return NormalizeResult(focus_rows=rows, report=report)
```

If the adapter raises an error, the dispatcher catches it, logs it, and continues with other sources. The failing source gets `{"status": "error", "error": str(e)}` in the run summary.

---

## How to register a new adapter

1. **Write the adapter class** implementing `discover()` and `normalize()`.
2. **Set `source_type`** — a unique string identifier for this provider type (e.g., `"my-custom-source"`).
3. **Add an instance to `ADAPTERS`** in `connectors/adapters.py`:

```python
from connectors.my_custom import MyCustomSource

ADAPTERS: dict[str, object] = {
    a.source_type: a
    for a in (
        # ... existing adapters ...
        MyCustomSource(),
    )
}
```

4. **Add a `SourceConfig` row** to the registry (see `connectors/registry.py`) with `source_type="my-custom-source"`.

That's it. The dispatcher will automatically discover and normalize this source on the next run.

---

## Upload vs. API-pull distinction

The PoC has two ingestion paths:

### Upload (live MVP path)

**`UploadSource`** in `connectors/adapters.py` is the working implementation. User uploads a FOCUS CSV via the web UI → the upload endpoint validates it (header-only, see `connectors/upload_validate.py`) → writes it to the source's inbox (`out/uploads/<source_id>/`) → the dispatcher's next run discovers new files via the watermark pattern → normalizes them as native-FOCUS exports.

This is the only path that actually runs in the synthetic-only build.

### API-pull (deferred, stubbed)

**`AwsCostExplorerSource`** and **`AzureExportSource`** in `connectors/api_pull.py` are registered adapters but raise `NotImplementedError` on every call. They exist to prove the contract accommodates both paths without forking.

The source types `"aws-api-pull"` and `"azure-api-pull"` are listed in `API_PULL_TYPES` and rendered disabled in the Connect UI with a "later release" label.

When a credentialed deployment wants automated pull, fill in `discover()` / `normalize()` for these adapters against real cloud APIs (AWS Data Exports for FOCUS delivered to S3, Azure Cost Management FOCUS exports). The dispatcher's fail-soft contract means a registered-but-unfilled source never breaks a run — it just appears with `status: "no-exports"` or `status: "error"`.

---

## Validation layers

Three layers, each fails safe:

1. **Upload-time validation** (`connectors/upload_validate.py`): header-only check ("does this look like a FOCUS export at all?"). Rejects garbage at the door before it enters the inbox. Deliberately NOT full row conformance — that's the normalizer's job.
2. **Normalizer conformance** (`normalizer/*_to_focus.py`): row-level validation as part of the mapping. Drops rows with fatal errors, warns on non-fatal issues, and returns a `report` per row.
3. **Post-load conformance** (`web/focus_conformance()`): the authoritative gate that runs after the load and produces the final conformance report shown in the UI.

The three layers ensure that a malformed export is caught early (fast reject), tolerated where salvageable (keep good rows, drop bad), and reported honestly (the conformance report never claims 100% if categories or rows were dropped).

---

## Key design points

- **Idempotent discovery:** `discover()` must be safe to call repeatedly. Use a watermark (mtime, a `.watermark` file, or a last-run timestamp in the registry).
- **Fail-soft dispatcher:** A raised error in one source never stops the run. Other sources continue, and the failing source gets `status: "error"` in the summary.
- **No secrets inline:** `credential_ref` points at a secret store (AWS Secrets Manager ARN). In the PoC it's unused (synthetic data only), but the contract enforces the right shape for production.
- **Provider extensions:** FOCUS allows provider-specific columns (prefixed `x_`). The native-FOCUS normalizer folds these into an `_extensions` JSON column (handled by `db/loader.py` as JSONB).

