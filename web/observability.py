"""Observability for the FinOps app — dependency-free (no prometheus_client).

Three things a production deploy on ROSA/OpenShift needs and the PoC lacked:
  1. Structured (JSON) request logs with a per-request id, so a log aggregator
     (CloudWatch / Loki / the OpenShift logging stack) can index them.
  2. A Prometheus `/metrics` endpoint (hand-rolled text exposition — no extra
     dependency, keeping the clone-and-deploy footprint small) for request
     counts, in-flight, and latency buckets.
  3. An append-only AUDIT log for the destructive/state-changing connect
     endpoints (upload / add / remove) — a bank needs to answer "who changed
     the ingested data, when, and what happened."

All in-process and best-effort: observability must never break a request, so
every hook swallows its own errors. Metrics are process-local (per worker);
a multi-worker gunicorn deploy aggregates at the Prometheus scrape layer, which
is the normal pattern.
"""
from __future__ import annotations

import json
import logging
import sys
import threading
import time
import uuid
from collections import defaultdict

# --- structured logging -----------------------------------------------------

_logger = logging.getLogger("finops")
if not _logger.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter("%(message)s"))  # we emit JSON ourselves
    _logger.addHandler(_h)
    _logger.setLevel(logging.INFO)
    _logger.propagate = False


def log_event(event: str, **fields) -> None:
    """Emit one structured JSON log line. Never raises."""
    try:
        rec = {"ts": _now_iso(), "event": event, **fields}
        _logger.info(json.dumps(rec, default=str))
    except Exception:
        pass


def _now_iso() -> str:
    # time.gmtime avoids the Date.now() ban concerns (this is runtime logging,
    # not workflow-replayable code) and keeps logs in UTC.
    t = time.time()
    ms = int((t % 1) * 1000)
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + f".{ms:03d}Z"


# --- metrics (process-local, Prometheus text exposition) --------------------

_lock = threading.Lock()
_req_total: dict[tuple[str, str, int], int] = defaultdict(int)  # (method,path,status)->n
_req_inflight = 0
# Latency histogram buckets in seconds (Prometheus convention).
_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
_latency_buckets: dict[str, list[int]] = defaultdict(lambda: [0] * (len(_BUCKETS) + 1))
_latency_sum: dict[str, float] = defaultdict(float)
_latency_count: dict[str, int] = defaultdict(int)
# Domain counters that matter to FinOps operators.
_counters: dict[str, int] = defaultdict(int)


def inc_counter(name: str, n: int = 1) -> None:
    with _lock:
        _counters[name] += n


def _route_label(path: str) -> str:
    """Collapse high-cardinality path params so the metric label set stays
    bounded (a /workload/{id} per id would explode Prometheus cardinality)."""
    parts = path.split("/")
    out = []
    for p in parts:
        # numeric or long id segments -> placeholder
        if p.isdigit() or len(p) > 24:
            out.append(":id")
        else:
            out.append(p)
    return "/".join(out) or "/"


def record_request(method: str, path: str, status: int, dur_s: float) -> None:
    label = _route_label(path)
    with _lock:
        _req_total[(method, label, status)] += 1
        _latency_sum[label] += dur_s
        _latency_count[label] += 1
        b = _latency_buckets[label]
        placed = False
        for i, edge in enumerate(_BUCKETS):
            if dur_s <= edge:
                b[i] += 1
                placed = True
                break
        if not placed:
            b[-1] += 1


class inflight:
    """Context manager tracking concurrent in-flight requests."""
    def __enter__(self):
        global _req_inflight
        with _lock:
            _req_inflight += 1
        return self

    def __exit__(self, *exc):
        global _req_inflight
        with _lock:
            _req_inflight -= 1
        return False


def render_prometheus() -> str:
    """Render current metrics in Prometheus text exposition format (v0.0.4)."""
    lines: list[str] = []
    with _lock:
        lines.append("# HELP finops_requests_total Total HTTP requests.")
        lines.append("# TYPE finops_requests_total counter")
        for (method, path, status), n in sorted(_req_total.items()):
            lines.append(
                f'finops_requests_total{{method="{method}",path="{path}",'
                f'status="{status}"}} {n}')

        lines.append("# HELP finops_requests_inflight In-flight HTTP requests.")
        lines.append("# TYPE finops_requests_inflight gauge")
        lines.append(f"finops_requests_inflight {_req_inflight}")

        lines.append("# HELP finops_request_duration_seconds Request latency.")
        lines.append("# TYPE finops_request_duration_seconds histogram")
        for label in sorted(_latency_count):
            cumulative = 0
            b = _latency_buckets[label]
            for i, edge in enumerate(_BUCKETS):
                cumulative += b[i]
                lines.append(
                    f'finops_request_duration_seconds_bucket{{path="{label}",'
                    f'le="{edge}"}} {cumulative}')
            cumulative += b[-1]
            lines.append(
                f'finops_request_duration_seconds_bucket{{path="{label}",'
                f'le="+Inf"}} {cumulative}')
            lines.append(
                f'finops_request_duration_seconds_sum{{path="{label}"}} '
                f'{_latency_sum[label]:.6f}')
            lines.append(
                f'finops_request_duration_seconds_count{{path="{label}"}} '
                f'{_latency_count[label]}')

        for name, n in sorted(_counters.items()):
            lines.append(f"# TYPE finops_{name} counter")
            lines.append(f"finops_{name} {n}")
    return "\n".join(lines) + "\n"


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


# --- audit log (append-only, for state-changing endpoints) ------------------

def audit(action: str, request_id: str, **fields) -> None:
    """Record a state-changing action (upload/add/remove). Distinct `event`
    so an aggregator can route audit lines to a retained store. Never raises."""
    log_event("audit", action=action, request_id=request_id, **fields)
    inc_counter(f"audit_{action}_total")
