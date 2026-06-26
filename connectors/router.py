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

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from connectors import registry, dispatcher
from connectors.adapters import ADAPTERS
from connectors.contract import SourceConfig

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(os.path.dirname(THIS_DIR), "web", "templates")
templates = Jinja2Templates(directory=TEMPLATE_DIR)

router = APIRouter(prefix="/connect", tags=["connect"])


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


@router.post("/remove")
def connect_remove(body: dict):
    source_id = (body.get("source_id") or "").strip()
    current = registry.load()
    kept = [s for s in current if s.source_id != source_id]
    registry.save(kept)
    result = dispatcher.run()
    return {"ok": True, "dispatch": result, "sources": _sources_view()}
