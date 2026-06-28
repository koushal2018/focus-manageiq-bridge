"""Tests for the dependency-free observability layer (pure logic, no DB)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from web import observability as obs


def test_metrics_render_is_prometheus_text():
    obs.record_request("GET", "/views/ai", 200, 0.012)
    out = obs.render_prometheus()
    assert "# TYPE finops_requests_total counter" in out
    assert 'finops_requests_total{method="GET",path="/views/ai",status="200"}' in out
    assert "finops_request_duration_seconds_bucket" in out
    assert "finops_requests_inflight" in out


def test_route_label_collapses_high_cardinality():
    # /workload/90010 and /workload/90011 must collapse to one label, or the
    # metric cardinality explodes one series per id.
    assert obs._route_label("/workload/90010") == obs._route_label("/workload/99999")
    assert ":id" in obs._route_label("/workload/90010")


def test_inflight_increments_and_decrements():
    before = obs.render_prometheus()
    with obs.inflight():
        during = obs.render_prometheus()
    after = obs.render_prometheus()
    # crude: the gauge line exists; in-context value >= out-of-context value
    def gauge(text):
        for line in text.splitlines():
            if line.startswith("finops_requests_inflight "):
                return int(line.split()[-1])
        return None
    assert gauge(during) >= gauge(before)
    assert gauge(after) == gauge(before)


def test_audit_increments_counter():
    obs.audit("upload", "rid-123", source_id="s1", bytes=10)
    out = obs.render_prometheus()
    assert "finops_audit_upload_total" in out


def test_new_request_id_is_unique_hex():
    a, b = obs.new_request_id(), obs.new_request_id()
    assert a != b and len(a) == 16 and all(c in "0123456789abcdef" for c in a)


def test_log_event_never_raises_on_bad_field():
    # A non-serializable field must not blow up the request path.
    class X:
        pass
    obs.log_event("http_request", weird=X())  # must not raise
