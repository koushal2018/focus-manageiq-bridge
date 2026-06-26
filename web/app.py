"""FastAPI application for the ENBD multi-cloud FinOps PoC.

Four views (SPEC §3.5), each carrying an honest data-source banner that
states where its data comes from and what FOCUS can/can't do.

Run:
    .venv/bin/python -m uvicorn web.app:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import os

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from web import queries


THIS_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(THIS_DIR, "templates")
STATIC_DIR = os.path.join(THIS_DIR, "static")


app = FastAPI(
    title="ENBD Multi-Cloud FinOps PoC",
    description=(
        "Throwaway de-risking spike for the ENBD engagement. "
        "Read GOTCHAS.md before changing anything."
    ),
    version="0.5.0",
)

templates = Jinja2Templates(directory=TEMPLATE_DIR)
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    stats = queries.headline_stats()
    return templates.TemplateResponse(
        request, "index.html",
        {"stats": stats, "active": "home"},
    )


@app.get("/views/ai", response_class=HTMLResponse)
def view_ai(request: Request):
    rows = queries.ai_cost_by_model()
    return templates.TemplateResponse(
        request, "view_ai.html",
        {"rows": rows, "active": "ai"},
    )


@app.get("/views/utilization", response_class=HTMLResponse)
def view_utilization(request: Request):
    rows = queries.utilization_x_cost()
    return templates.TemplateResponse(
        request, "view_utilization.html",
        {"rows": rows, "active": "utilization"},
    )


@app.get("/views/cloud-vs-onprem", response_class=HTMLResponse)
def view_cloud_vs_onprem(request: Request):
    cloud = queries.cloud_cost_by_provider()
    on_prem = queries.onprem_cost_estimate()
    on_prem_total = sum(r["monthly_cost_usd"] for r in on_prem)
    return templates.TemplateResponse(
        request, "view_cloud_vs_onprem.html",
        {
            "cloud": cloud,
            "on_prem": on_prem,
            "on_prem_total": round(on_prem_total, 2),
            "active": "cloud-vs-onprem",
        },
    )


@app.get("/views/carbon", response_class=HTMLResponse)
def view_carbon(request: Request):
    feeds = queries.carbon_stub()
    placeholder = queries.carbon_intensity_placeholder()
    return templates.TemplateResponse(
        request, "view_carbon.html",
        {
            "feeds": feeds,
            "placeholder": placeholder,
            "active": "carbon",
        },
    )


# Health endpoint for sanity / smoke
@app.get("/healthz")
def healthz():
    stats = queries.headline_stats()
    return {"ok": True, **{k: v for k, v in stats.items() if k != "join_status"}}
