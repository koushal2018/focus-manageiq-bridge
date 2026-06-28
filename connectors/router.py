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

from fastapi import APIRouter, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from connectors import registry, dispatcher, upload_validate
from connectors.adapters import ADAPTERS, UploadSource, inbox_dir
from connectors.api_pull import API_PULL_TYPES
from connectors.contract import SourceConfig

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(os.path.dirname(THIS_DIR), "web", "templates")
templates = Jinja2Templates(directory=TEMPLATE_DIR)

router = APIRouter(prefix="/connect", tags=["connect"])


def _load_and_join() -> None:
    """Run the join + DB load + onprem steps after a dispatch so uploaded data
    actually reaches focus_costs (and the dashboard), mirroring docker/seed.py
    steps 4-6. The MIQ snapshot is produced at seed time; if it is missing
    (fresh container that never seeded) we regenerate it so the join can run."""
    import os
    from join import miq_snapshot, resource_join_map
    from db import loader

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    vms_path = os.path.join(root, "out", "miq", "vms.json")
    if not os.path.exists(vms_path):
        vms_path, _ = miq_snapshot.write_snapshots()
    os.environ.setdefault("MIQ_VMS_JSON", vms_path)

    combined = os.path.join(root, "out", "normalizer", "focus_combined.csv")
    rows = resource_join_map.build(combined)
    resource_join_map.write_csv(
        rows, os.path.join(root, "out", "join", "resource_join_map.csv"))
    loader.main()
    try:
        from onprem import cost_model
        cost_model.load_into_postgres()
    except Exception as e:  # onprem is supplementary; never fail the upload on it
        print(f"[upload] onprem load skipped: {e}")


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
def connect_add(body: dict):
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
    if not source_id:
        return JSONResponse({"ok": False, "error": "source_id is required"}, status_code=400)
    if not location:
        # default to the synthetic export for this type so the demo flows
        location = DEMO_LOCATIONS.get(source_type, "")
        if not location:
            return JSONResponse({"ok": False, "error": "location is required"}, status_code=400)

    registry.add_source(SourceConfig(
        source_id=source_id,
        source_type=source_type,
        display_name=display_name,
        location=location,
        credential_ref=credential_ref,
        schedule=body.get("schedule", "daily"),
    ))

    # Re-run the dispatcher so the new source's rows land immediately. The
    # join + load steps are separate (run by the operator / scheduler); we
    # report what the dispatch produced.
    result = dispatcher.run()
    return {"ok": True, "dispatch": result, "sources": _sources_view()}


@router.post("/upload")
async def connect_upload(source_id: str = Form(...), file: UploadFile = File(...)):
    """Real upload ingestion: validate FOCUS-conformance BEFORE accepting, write
    to the source's inbox, register an upload source if new, run the dispatcher.
    A file that fails validation is never written and never ingested."""
    sid = (source_id or "").strip()
    if not sid:
        return JSONResponse({"ok": False, "error": "source_id is required"}, status_code=400)

    raw = await file.read()
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
    with open(dest, "wb") as f:
        f.write(raw)

    result = dispatcher.run()
    _load_and_join()
    UploadSource().advance_watermark(
        SourceConfig(sid, "upload-focus", sid, d, "upload", "manual"))
    return {"ok": True, "dispatch": result, "sources": _sources_view()}


@router.post("/remove")
def connect_remove(body: dict):
    source_id = (body.get("source_id") or "").strip()
    current = registry.load()
    kept = [s for s in current if s.source_id != source_id]
    registry.save(kept)
    result = dispatcher.run()
    return {"ok": True, "dispatch": result, "sources": _sources_view()}
