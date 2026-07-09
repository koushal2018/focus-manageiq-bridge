"""FastAPI application for the AnyBank multi-cloud FinOps PoC.

Four views (SPEC §3.5), each carrying an honest data-source banner that
states where its data comes from and what FOCUS can/can't do.

Run:
    .venv/bin/python -m uvicorn web.app:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import base64
import hmac
import os
import time

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from web import queries
from web import observability as obs
from web import tenant

# Slice 7: optional Bedrock NL-query layer. Mounted as a router; works
# (in canned-query-only mode) even when Bedrock is disabled.
from ai.router import router as ai_router

# Connect-and-run: the clickable "register a data source" admin surface.
from connectors.router import router as connect_router


THIS_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(THIS_DIR, "templates")
STATIC_DIR = os.path.join(THIS_DIR, "static")


app = FastAPI(
    title=f"{tenant.get('product_name')} — {tenant.get('org_name')}",
    description=(
        "Multi-cloud FinOps console (FOCUS + ManageIQ). Config-driven, "
        "single-tenant per deploy. Read GOTCHAS.md before changing anything."
    ),
    version="0.6.0",
)

templates = Jinja2Templates(directory=TEMPLATE_DIR)
# Tenant branding is a Jinja GLOBAL (set once) rather than threaded through
# every TemplateResponse — every template, including _base.html, can read
# `tenant.org_name` etc. Config-driven single-tenant: a customer edits
# config/tenant.json, no template edits. (Spec 3 packaging.)
templates.env.globals["tenant"] = tenant.branding()
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# --- Optional HTTP Basic Auth gate -----------------------------------------
# OFF by default (local dev + docker stack unaffected). ON when BASIC_AUTH_USER
# and BASIC_AUTH_PASS are both set in the environment. This keeps the
# bank-branded (synthetic) console off the open internet for any sharing path
# — CloudFront, a tunnel, or a directly-exposed port. CloudFront also enforces
# Basic Auth at the edge; this app-layer gate is defence-in-depth and means a
# misconfigured origin SG can't leak an unauthenticated console.
_BA_USER = os.environ.get("BASIC_AUTH_USER", "")
_BA_PASS = os.environ.get("BASIC_AUTH_PASS", "")
_BA_ENABLED = bool(_BA_USER and _BA_PASS)
# /healthz is exempt so load-balancer / CloudFront health checks still pass;
# /metrics is exempt so an in-cluster Prometheus scraper needs no credentials
# (it's reached over the cluster network / a ServiceMonitor, not the edge).
_BA_EXEMPT = {"/healthz", "/metrics"}


@app.middleware("http")
async def _basic_auth(request: Request, call_next):
    if not _BA_ENABLED or request.url.path in _BA_EXEMPT:
        return await call_next(request)
    header = request.headers.get("authorization", "")
    if header.startswith("Basic "):
        try:
            raw = base64.b64decode(header[6:]).decode("utf-8", "replace")
            user, _, pwd = raw.partition(":")
            # constant-time compare on both fields (avoid timing oracle)
            if hmac.compare_digest(user, _BA_USER) and hmac.compare_digest(pwd, _BA_PASS):
                return await call_next(request)
        except Exception:
            pass
    return Response(
        "Authentication required.",
        status_code=401,
        headers={"WWW-Authenticate": f'Basic realm="{tenant.get("product_name")} (synthetic)"'},
    )


# --- Observability middleware ----------------------------------------------
# Registered AFTER _basic_auth so it wraps it (FastAPI runs middleware in
# reverse registration order) — every request, including a 401, is timed,
# counted, and logged with a request id. Best-effort: never breaks a request.
@app.middleware("http")
async def _observe(request: Request, call_next):
    rid = obs.new_request_id()
    request.state.request_id = rid
    start = time.perf_counter()
    status = 500
    try:
        with obs.inflight():
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = rid
            return response
    finally:
        dur = time.perf_counter() - start
        path = request.url.path
        obs.record_request(request.method, path, status, dur)
        # /metrics and /healthz are noisy; log them at a coarser level by event
        obs.log_event(
            "http_request", request_id=rid, method=request.method, path=path,
            status=status, duration_ms=round(dur * 1000, 2),
            client=request.client.host if request.client else None)


app.include_router(ai_router)
app.include_router(connect_router)


@app.get("/metrics")
def metrics():
    """Prometheus scrape endpoint (text exposition). Exempt from Basic Auth
    via _BA_EXEMPT so a scraper doesn't need credentials inside the cluster."""
    return PlainTextResponse(obs.render_prometheus(),
                             media_type="text/plain; version=0.0.4")


@app.get("/login", response_class=HTMLResponse)
def login(request: Request):
    return templates.TemplateResponse(
        request, "login.html",
        {"kpis": queries.headline_kpis(), "active": None},
    )


@app.get("/welcome", response_class=HTMLResponse)
def welcome(request: Request):
    return templates.TemplateResponse(
        request, "splash.html",
        {"kpis": queries.headline_kpis(), "active": None},
    )


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        request, "index.html",
        {
            "kpis": queries.headline_kpis(),
            "pipeline": queries.pipeline_snapshot(),
            "ingest": queries.provider_ingest(),
            "join_dist": queries.join_distribution(),
            "rightsizing": queries.top_rightsizing(6),
            "cvo": queries.cloud_vs_onprem_with_budget(),
            "conformance": queries.focus_conformance(),
            "active": "home",
        },
    )


@app.get("/workload/{vm_id}", response_class=HTMLResponse)
def workload_detail(request: Request, vm_id: str):
    detail = queries.workload_detail(vm_id)
    if not detail:
        return templates.TemplateResponse(
            request, "view_detail_missing.html",
            {"vm_id": vm_id, "active": "utilization"}, status_code=404,
        )
    return templates.TemplateResponse(
        request, "view_detail.html",
        {"d": detail, "vm_id": vm_id, "active": "utilization"},
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


@app.get("/faq", response_class=HTMLResponse)
def faq(request: Request):
    return templates.TemplateResponse(
        request, "view_faq.html", {"active": "faq"},
    )


# Health endpoint for sanity / smoke
@app.get("/healthz")
def healthz():
    stats = queries.headline_stats()
    return {"ok": True, **{k: v for k, v in stats.items() if k != "join_status"}}
