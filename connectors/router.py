"""FastAPI router for the 'Connect a data source' admin surface.

Mounted at /connect by the web app. This is the clickable version of the
connect-and-run promise: list registered sources, add a new one, re-run the
dispatcher to ingest.

HONESTY DISCIPLINE (per the demo-labeling decision): in this PoC a registered
source's `discover()` reads a local synthetic CSV. Registering a source here
writes a registry row and re-runs the dispatcher over the configured
locations — it does NOT yet perform a live cloud fetch with the supplied
credential. Every surface says so plainly. A control that looks live but
isn't is exactly the failure mode SPEC §0 warns against, so we label it.
"""
from __future__ import annotations

import os
import re

from fastapi import APIRouter, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from connectors import registry, dispatcher, upload_validate
from connectors.adapters import ADAPTERS, inbox_dir
from connectors.api_pull import API_PULL_TYPES
from connectors.contract import SourceConfig
from db.loader import LoadConformanceError as loader_LoadConformanceError
from web import observability as obs


def _rid(request: Request | None) -> str:
    """Best-effort request id from the observability middleware."""
    return getattr(getattr(request, "state", None), "request_id", "") if request else ""


import contextlib as _contextlib
import fcntl as _fcntl


@_contextlib.contextmanager
def _source_ingest_lock(source_id: str):
    """Serialize the write→dispatch→load→mark sequence for a given source so two
    concurrent uploads of the SAME source can't interleave (one marking the
    other's file ingested before it loads → silent data loss, review finding).
    A POSIX flock on a per-source sidecar; single-host (the deploy target is
    single-tenant — a multi-replica setup would use an advisory DB lock)."""
    d = inbox_dir(source_id)
    f = open(os.path.join(d, ".ingest.lock"), "w")
    try:
        _fcntl.flock(f, _fcntl.LOCK_EX)
        yield
    finally:
        _fcntl.flock(f, _fcntl.LOCK_UN)
        f.close()

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(os.path.dirname(THIS_DIR), "web", "templates")
templates = Jinja2Templates(directory=TEMPLATE_DIR)
# This router has its own Jinja env (separate from web/app.py's), so the
# `tenant` branding global must be installed here too — otherwise _base.html
# renders /connect with the generic defaults instead of config/tenant.json.
from web import tenant as _tenant
templates.env.globals["tenant"] = _tenant.branding()

router = APIRouter(prefix="/connect", tags=["connect"])

# source_id is used UNSANITIZED as a filesystem path component (inbox_dir joins
# it under out/uploads/). A value like "../../etc" would escape the inbox — a
# path-traversal write primitive on an endpoint with no real auth. Constrain it
# to a safe charset and reject traversal explicitly. (GOTCHA SEC-1.)
_SAFE_SOURCE_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

# Cap the in-request upload so one POST can't OOM the worker (the container is
# memory-limited). Real exports for a large org are far bigger than this — that
# is the async-ingestion story (deferred, GOTCHA W-15); this cap keeps the
# synchronous demo path honest about its limits rather than crashing.
_MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MiB


def _valid_source_id(sid: str) -> bool:
    """A source_id must be a single safe path segment — no separators, no
    traversal, no absolute paths. Mirrors the _SAFE_SOURCE_ID charset."""
    if not sid or sid in (".", "..") or "/" in sid or "\\" in sid:
        return False
    return bool(_SAFE_SOURCE_ID.match(sid))


_PROJECT_ROOT = os.path.dirname(THIS_DIR)


def _location_within_root(location: str) -> bool:
    """A source `location` (file-based adapters read it under the project root)
    must resolve to a path INSIDE the project tree — reject absolute paths and
    `..` traversal so it can't be an arbitrary-file-read primitive (SEC-2)."""
    if not location or os.path.isabs(location):
        return False
    resolved = os.path.realpath(os.path.join(_PROJECT_ROOT, location))
    root = os.path.realpath(_PROJECT_ROOT)
    return resolved == root or resolved.startswith(root + os.sep)


def _rebuild_join_and_onprem(root: str) -> None:
    """Rebuild the DERIVED resource_join_map from the FULL focus_costs table
    (the join must see every source, not just the one just loaded) and refresh
    onprem. focus_costs is dumped back to the combined-CSV shape so the existing
    file-based join builder runs unchanged over all rows."""
    from join import miq_snapshot, resource_join_map
    from db import loader

    vms_path = os.path.join(root, "out", "miq", "vms.json")
    if not os.path.exists(vms_path):
        vms_path, _ = miq_snapshot.write_snapshots()
    os.environ.setdefault("MIQ_VMS_JSON", vms_path)

    # Export the FULL table → combined CSV → rebuild join over all sources.
    combined = os.path.join(root, "out", "normalizer", "focus_combined.csv")
    loader.export_focus_costs_csv(combined)
    rows = resource_join_map.build(combined)
    resource_join_map.write_csv(
        rows, os.path.join(root, "out", "join", "resource_join_map.csv"))
    _load_join_only(rows)
    try:
        from onprem import cost_model
        cost_model.load_into_postgres()
    except Exception as e:  # onprem is supplementary; never fail the upload on it
        print(f"[upload] onprem load skipped: {e}")


def _load_join_only(join_rows) -> None:
    """Replace resource_join_map (derived) in its own transaction. Separate from
    focus_costs so the incremental cost partition-replace stays O(one source)."""
    import psycopg2
    from db import loader as _loader
    join_buf, join_cols = _loader._build_join_staging()
    conn = psycopg2.connect(**_loader._conn_kwargs())
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            cur.execute("TRUNCATE resource_join_map RESTART IDENTITY")
            _loader._copy(cur, "resource_join_map", join_cols, join_buf)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


class UploadIngestError(Exception):
    """The dispatch failed for this source (normalize/discover error). Distinct
    from an empty-but-successful dispatch so the caller can react correctly."""


class JoinRebuildError(Exception):
    """The per-source cost partition COMMITTED, but the subsequent join/onprem
    rebuild failed. The warehouse is NOT unchanged — the cost partition is
    updated while the derived join is stale — so the caller must not tell the
    user 'nothing changed'."""


def _ingest_upload(source_id: str) -> dict:
    """Incremental ingest of one uploaded source (W-15): dispatch ONLY this
    source → per-source partition load of focus_costs (no global TRUNCATE) →
    rebuild the derived join over all sources. Returns the dispatch result.

    discover() returns the WHOLE inbox for an upload source, so the dispatched
    CSV is the union of every file uploaded to this source; load_source replaces
    the partition with exactly that. There is therefore no "already ingested"
    special case — re-dispatching the same inbox reproduces the same partition
    (idempotent). We DO fail loudly if the dispatch itself errored, so a failed
    normalize is never mistaken for a successful empty load (which would let the
    partition-replace wipe good data)."""
    import os
    from db import loader

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    result = dispatcher.run(only_source_id=source_id)
    if result.get("errored"):
        errs = "; ".join(e.get("error", "unknown") for e in result["errored"])
        raise UploadIngestError(errs or "dispatch failed")
    # load_source is atomic: on LoadConformanceError it rolls back and the
    # partition is preserved (nothing changed). Once it returns, the partition
    # is COMMITTED — a later failure in the join rebuild leaves the cost
    # partition changed, so it must NOT be reported as "nothing changed".
    loader.load_source(source_id, result["out_csv"])   # may raise LoadConformanceError
    try:
        _rebuild_join_and_onprem(root)
    except Exception as e:
        raise JoinRebuildError(str(e)) from e
    return result


# The synthetic exports a PoC user can point a new source at. In production
# this list disappears — the location is an S3 prefix / blob URL the admin
# types, and discover() lists real objects.
DEMO_LOCATIONS = {
    # native-FOCUS exports (current path)
    "aws-focus-export": "out/generators/focus_aws.csv",
    "azure-focus-export": "out/generators/focus_azure.csv",
    "oci-focus-export": "out/generators/focus_oci.csv",
    # provider-native billing formats (historical path)
    "aws-cur": "out/generators/aws_cur.csv",
    "azure-export": "out/generators/azure_cost.csv",
    "oci-usage": "out/generators/oci_usage.csv",
}


def _sources_view() -> list[dict]:
    out = []
    for s in registry.load():
        out.append({
            "source_id": s.source_id,
            "source_type": s.source_type,
            "display_name": s.display_name,
            "location": s.location,
            "credential_ref": s.credential_ref,
            "schedule": s.schedule,
            "enabled": s.enabled,
        })
    return out


@router.get("/", response_class=HTMLResponse)
def connect_index(request: Request):
    return templates.TemplateResponse(
        request, "view_connect.html",
        {
            "active": "connect",
            "sources": _sources_view(),
            "types": sorted(ADAPTERS.keys()),
            "api_pull_types": sorted(API_PULL_TYPES),
            "demo_locations": DEMO_LOCATIONS,
        },
    )


@router.post("/add")
def connect_add(body: dict, request: Request = None):
    """Register a source (writes a registry row) and re-run the dispatcher.

    Validation: type must have a registered adapter; source_id must be
    non-empty and unique-ish (upsert by id). Credential is stored as a
    reference string only — never a live secret in this PoC.
    """
    source_type = (body.get("source_type") or "").strip()
    source_id = (body.get("source_id") or "").strip()
    display_name = (body.get("display_name") or source_id).strip()
    location = (body.get("location") or "").strip()
    credential_ref = (body.get("credential_ref") or "demo:synthetic").strip()

    if source_type not in ADAPTERS:
        return JSONResponse(
            {"ok": False, "error": f"unknown source_type {source_type!r}; "
             f"available: {sorted(ADAPTERS.keys())}"}, status_code=400)
    # API-pull types are registered-but-stubbed (discover() raises). The UI
    # renders them disabled; enforce the same server-side so a stub source can't
    # be registered (it would only ever fail to dispatch). Belt-and-braces with
    # the dispatcher's fail-soft, which keeps a stub from sinking a run.
    if source_type in API_PULL_TYPES:
        return JSONResponse(
            {"ok": False, "error": f"source_type {source_type!r} is not yet "
             "available (API-pull connectors are deferred — use upload)"},
            status_code=400)
    if not source_id:
        return JSONResponse({"ok": False, "error": "source_id is required"}, status_code=400)
    if not _valid_source_id(source_id):
        return JSONResponse(
            {"ok": False, "error": "source_id must be 1–128 chars of letters, "
             "digits, dot, underscore or hyphen (no path separators)"},
            status_code=400)
    if not location:
        # default to the synthetic export for this type so the demo flows
        location = DEMO_LOCATIONS.get(source_type, "")
        if not location:
            return JSONResponse({"ok": False, "error": "location is required"}, status_code=400)
    # The file-based adapters resolve `location` under the project root and read
    # it (connectors.adapters._local_export). A traversal/absolute path would be
    # an arbitrary-file-READ primitive (info disclosure), the read-side twin of
    # SEC-1. Constrain it to stay inside the project tree. (GOTCHA SEC-2.)
    if not _location_within_root(location):
        return JSONResponse(
            {"ok": False, "error": "location must be a path inside the project "
             "data tree (no absolute paths or '..' traversal)"}, status_code=400)

    registry.add_source(SourceConfig(
        source_id=source_id,
        source_type=source_type,
        display_name=display_name,
        location=location,
        credential_ref=credential_ref,
        schedule=body.get("schedule", "daily"),
    ))
    obs.audit("source_add", _rid(request), source_id=source_id,
              source_type=source_type, location=location)

    # Re-run the dispatcher so the new source's rows land immediately. The
    # join + load steps are separate (run by the operator / scheduler); we
    # report what the dispatch produced.
    result = dispatcher.run()
    return {"ok": True, "dispatch": result, "sources": _sources_view()}


@router.post("/upload")
async def connect_upload(source_id: str = Form(...), file: UploadFile = File(...),
                         request: Request = None):
    """Real upload ingestion: validate FOCUS-conformance BEFORE accepting, write
    to the source's inbox, register an upload source if new, run the dispatcher.
    A file that fails validation is never written and never ingested."""
    sid = (source_id or "").strip()
    if not sid:
        return JSONResponse({"ok": False, "error": "source_id is required"}, status_code=400)
    if not _valid_source_id(sid):
        return JSONResponse(
            {"ok": False, "error": "source_id must be 1–128 chars of letters, "
             "digits, dot, underscore or hyphen (no path separators)"},
            status_code=400)

    # Bounded read: stream in chunks and abort if it exceeds the cap, so a
    # large (or hostile) upload can't OOM the worker before validation.
    raw = b""
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        raw += chunk
        if len(raw) > _MAX_UPLOAD_BYTES:
            return JSONResponse(
                {"ok": False, "error": f"file exceeds the {_MAX_UPLOAD_BYTES // (1024*1024)} MiB "
                 "synchronous-upload limit (large exports need the async path)"},
                status_code=413)

    ok, reason = upload_validate.validate_focus_csv(raw)
    if not ok:
        return JSONResponse({"ok": False, "error": reason}, status_code=400)

    # Register an upload source for this id if it doesn't exist yet.
    existing = {s.source_id for s in registry.load()}
    if sid not in existing:
        registry.add_source(SourceConfig(
            source_id=sid, source_type="upload-focus",
            display_name=f"Upload — {sid}", location=f"out/uploads/{sid}",
            credential_ref="upload:no-credential", schedule="manual"))

    # Write the validated bytes into the inbox.
    d = inbox_dir(sid)
    basename = os.path.basename(file.filename or "upload.csv")
    if not basename.lower().endswith(".csv"):
        basename += ".csv"
    dest = os.path.join(d, basename)

    # Hold a per-source lock across write→ingest so two concurrent uploads of
    # the SAME source can't interleave. The inbox is the source of truth for the
    # partition (discover() returns the whole inbox, load replaces the partition
    # with its union), so there is no separate watermark to mark.
    with _source_ingest_lock(sid):
        with open(dest, "wb") as f:
            f.write(raw)

        # Incremental, per-source ingest (W-15): only THIS source is dispatched
        # and only its partition of focus_costs is replaced — no global
        # TRUNCATE, cost is O(this source). The per-source load is guarded by an
        # in-txn conformance gate.
        try:
            result = _ingest_upload(sid)
        except (UploadIngestError, loader_LoadConformanceError) as e:
            # PRE-load failure: the partition rolled back / never changed, so
            # nothing changed and the just-written file is poison — remove it so
            # the inbox stays in sync with the preserved partition.
            try:
                os.remove(dest)
            except OSError:
                pass
            obs.audit("upload_rejected", _rid(request), source_id=sid,
                      filename=basename, bytes=len(raw), reason=str(e))
            return JSONResponse(
                {"ok": False, "error": f"upload rejected at load: {e}. The "
                 "existing data was preserved; nothing was changed."},
                status_code=422)
        except JoinRebuildError as e:
            # POST-commit failure: the cost partition IS updated (the file stays
            # in the inbox, matching the committed partition), but the derived
            # join is stale. Do NOT claim nothing changed; re-running the ingest
            # (or a re-seed) rebuilds the join.
            obs.audit("upload_partial", _rid(request), source_id=sid,
                      filename=basename, bytes=len(raw), reason=str(e))
            return JSONResponse(
                {"ok": False, "error": f"cost data was loaded, but the join/"
                 f"on-prem rebuild failed: {e}. The cost figures are updated; "
                 "the resource-join view may be stale until the next ingest.",
                 "partition_committed": True},
                status_code=500)
    obs.audit("upload", _rid(request), source_id=sid, filename=basename,
              bytes=len(raw), focus_rows=result.get("focus_rows"))
    return {"ok": True, "dispatch": result, "sources": _sources_view()}


# The FinOps Foundation's anonymized REAL FOCUS sample (CC BY 4.0), vendored.
# Proves the pipeline handles real-world FOCUS (version skew, literal NULLs),
# not just our synthetic data. Labelled clearly as third-party sample, NOT ENBD.
_FOUNDATION_SAMPLE = os.path.join(
    _PROJECT_ROOT, "fixtures", "focus_foundation_sample", "focus_sample_1000.csv")
_FOUNDATION_SOURCE_ID = "foundation-focus-sample"


@router.post("/load-sample")
def connect_load_sample(request: Request = None):
    """One-click ingest of the FinOps Foundation's anonymized real FOCUS sample
    through the SAME upload path real uploads use. It loads as its own source
    partition (incremental, W-15) and — being real cloud data with no ManageIQ
    inventory — shows honestly as unmatched_focus_only in the join. Demonstrates
    'we handle real FOCUS', distinct from the synthetic join demo."""
    if not os.path.exists(_FOUNDATION_SAMPLE):
        return JSONResponse(
            {"ok": False, "error": "Foundation sample fixture not found "
             "(fixtures/focus_foundation_sample/)."}, status_code=404)
    sid = _FOUNDATION_SOURCE_ID
    if sid not in {s.source_id for s in registry.load()}:
        registry.add_source(SourceConfig(
            source_id=sid, source_type="upload-focus",
            display_name="FinOps Foundation FOCUS sample (anonymized real, CC BY 4.0)",
            location=f"out/uploads/{sid}",
            credential_ref="public:cc-by-4.0-sample", schedule="manual"))
    with _source_ingest_lock(sid):
        d = inbox_dir(sid)
        dest = os.path.join(d, "focus_sample_1000.csv")
        with open(_FOUNDATION_SAMPLE, "rb") as src, open(dest, "wb") as dst:
            dst.write(src.read())
        try:
            result = _ingest_upload(sid)
        except (UploadIngestError, loader_LoadConformanceError) as e:
            try:
                os.remove(dest)
            except OSError:
                pass
            return JSONResponse(
                {"ok": False, "error": f"sample rejected at load: {e}. Existing "
                 "data preserved."}, status_code=422)
        except JoinRebuildError as e:
            return JSONResponse(
                {"ok": False, "error": f"sample cost data was loaded, but the "
                 f"join/on-prem rebuild failed: {e}. Cost figures are updated; "
                 "the join view may be stale until the next ingest.",
                 "partition_committed": True}, status_code=500)
    obs.audit("load_sample", _rid(request), source_id=sid,
              focus_rows=result.get("focus_rows"))
    return {"ok": True, "dispatch": result, "sources": _sources_view()}


@router.post("/remove")
def connect_remove(body: dict, request: Request = None):
    source_id = (body.get("source_id") or "").strip()
    registry.remove_source(source_id)   # locked read-modify-write (no race)
    # De-registering a source must also drop its committed focus_costs partition
    # and rebuild the derived join — otherwise its rows keep contributing to
    # every KPI/conformance/AI answer as ghost data until the next full re-seed.
    from db import loader
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    deleted = loader.delete_source(source_id)
    _rebuild_join_and_onprem(root)
    result = dispatcher.run()
    obs.audit("source_remove", _rid(request), source_id=source_id, rows_removed=deleted)
    return {"ok": True, "rows_removed": deleted, "dispatch": result,
            "sources": _sources_view()}
