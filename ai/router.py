"""FastAPI router for the Bedrock NL-query service.

Mounted under /ai by the main web app. Endpoints:

  GET  /ai/                   --- the canned-query + free-text UI page
  POST /ai/canned             --- run a named canned query
                                  body: {name, params}
  POST /ai/ask                --- free-text question (gated by BEDROCK_DISABLED)
                                  body: {question}
  GET  /ai/status             --- whether Bedrock is enabled this session

The page works even when Bedrock is disabled --- canned queries run unconditionally.
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ai import canned, bedrock_client, sql_guard


THIS_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(os.path.dirname(THIS_DIR), "web", "templates")
templates = Jinja2Templates(directory=TEMPLATE_DIR)


router = APIRouter(prefix="/ai", tags=["ai"])


@router.get("/status")
def status() -> dict:
    return {
        "bedrock_enabled": os.environ.get("BEDROCK_DISABLED", "1") != "1",
        "region": bedrock_client.DEFAULT_REGION,
        "model": bedrock_client.DEFAULT_MODEL_ID,
        "canned_queries": [
            {"name": q.name, "description": q.description, "params": list(q.params)}
            for q in canned.QUERIES.values()
        ],
    }


@router.get("/", response_class=HTMLResponse)
def ai_index(request: Request):
    return templates.TemplateResponse(
        request, "view_ai_query.html",
        {
            "active": "ai-query",
            "bedrock_enabled": os.environ.get("BEDROCK_DISABLED", "1") != "1",
            "region": bedrock_client.DEFAULT_REGION,
            "model": bedrock_client.DEFAULT_MODEL_ID,
            "queries": list(canned.QUERIES.values()),
        },
    )


@router.post("/canned")
def run_canned(body: dict[str, Any]) -> dict:
    name = body.get("name")
    params = body.get("params") or {}
    if not name:
        raise HTTPException(400, "missing 'name'")
    try:
        return canned.run_canned(name, params)
    except canned.CannedError as e:
        raise HTTPException(400, str(e))


@router.post("/ask")
def ask(body: dict[str, Any]) -> dict:
    """Free-text path. Fails closed unless BEDROCK_DISABLED=0."""
    question = (body.get("question") or "").strip()
    if not question:
        raise HTTPException(400, "missing 'question'")
    try:
        answer = bedrock_client.ask_bedrock(question)
    except bedrock_client.BedrockDisabled as e:
        # Per SPEC §3.6 + GOTCHA G-10 below, this is the explicit "AI off"
        # response --- not a silent fallback. The UI should show this verbatim.
        raise HTTPException(503, str(e))
    except bedrock_client.BedrockGuardrailRefusal as e:
        return {"sql": str(e), "rows": [], "raw_text": "(guardrail refusal)", "refused": True}
    except bedrock_client.BedrockQueryExecutionError as e:
        # Guard-valid SQL that failed at execution. Clean 422, not a 500.
        raise HTTPException(422, str(e))
    except sql_guard.SqlValidationError as e:
        raise HTTPException(
            422,
            f"Model produced unsafe SQL: {e.reason}. SQL was rejected; "
            "no query was executed against the database.",
        )
    return {
        "sql": answer.sql,
        "rows": answer.rows,
        "raw_text": answer.raw_text,
        "warnings": answer.warnings or [],   # H-10 financial-sanity flags
        "refused": False,
    }


@router.post("/narrate")
def narrate(body: dict[str, Any]) -> dict:
    """FinOps-assistant prose over rows the caller ALREADY has.

    Does not touch the database. The narration is bounded to the supplied
    rows (SPEC §0 — no new numbers). Returns {narrative} or {narrative: null}
    when Bedrock is off or the call fails (the caller's deterministic answer
    line is the source of truth and always stands).
    """
    question = (body.get("question") or "").strip()
    rows = body.get("rows")
    if not isinstance(rows, list):
        raise HTTPException(400, "missing 'rows' (list)")
    text = bedrock_client.narrate_answer(question, rows)
    return {"narrative": text}
