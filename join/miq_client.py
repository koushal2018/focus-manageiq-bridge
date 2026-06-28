"""Minimal MIQ REST client for fetching VM inventory.

Per GOTCHA G-2: ManageIQ token auth uses X-Auth-Token, NOT Authorization
Bearer. We default to HTTP Basic because that works per-request without
maintaining a session token. Auth credentials come from MIQ_USER /
MIQ_PASS env vars; for the local PoC appliance the default cred is
admin:smartvm (see GOTCHA G-1 --- rotate before any non-PoC use).

Per GOTCHA G-6: we trust the appliance cert by setting REQUESTS_CA_BUNDLE
to an exported PEM. The exact export command is documented in GOTCHAS.md
G-6. If MIQ_CA_BUNDLE is unset and MIQ_URL is HTTPS, this client raises
loudly --- it will NOT silently set verify=False.
"""
from __future__ import annotations

import os
import urllib.parse
import urllib.request
import ssl
import json
import base64


class MIQAuthError(RuntimeError):
    pass


class MIQHTTPError(RuntimeError):
    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body[:200]}")
        self.status = status
        self.body = body


def _basic_auth_header(user: str, password: str) -> str:
    raw = f"{user}:{password}".encode()
    return "Basic " + base64.b64encode(raw).decode()


def get_vms(
    url: str | None = None,
    user: str | None = None,
    password: str | None = None,
    ca_bundle: str | None = None,
) -> list[dict]:
    """Fetch /api/vms?expand=resources and return resources list.

    Per GOTCHA G-3 we deliberately do NOT pass an `attributes=` filter ---
    each version's vms collection has its own attribute names and a wrong
    name 400s the whole request. Take the default attribute set.
    """
    url = url or os.environ.get("MIQ_URL", "https://localhost/api")
    user = user or os.environ.get("MIQ_USER", "admin")
    password = password or os.environ.get("MIQ_PASS", "smartvm")
    ca_bundle = ca_bundle or os.environ.get("MIQ_CA_BUNDLE")

    if url.startswith("https://") and not ca_bundle:
        # GOTCHA G-6: fail loud. We do NOT provide a `verify=False` escape
        # hatch --- not even for the local synthetic appliance --- because
        # the habit of disabling TLS verification travels into production
        # code by copy-paste. The right local workaround is to export and
        # trust the appliance's self-signed cert.
        raise MIQAuthError(
            "MIQ_CA_BUNDLE not set. Export the appliance cert with:\n"
            "  openssl s_client -connect localhost:443 -servername localhost </dev/null 2>/dev/null \\\n"
            "    | openssl x509 > miq_appliance.pem\n"
            "then set MIQ_CA_BUNDLE=/path/to/miq_appliance.pem and re-run."
        )
    context: ssl.SSLContext | None = (
        ssl.create_default_context(cafile=ca_bundle) if ca_bundle else None
    )

    endpoint = url.rstrip("/") + "/vms?expand=resources"
    req = urllib.request.Request(
        endpoint,
        headers={
            "Authorization": _basic_auth_header(user, password),
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, context=context, timeout=10) as resp:
            body = resp.read().decode()
    except urllib.error.HTTPError as e:
        raise MIQHTTPError(e.code, e.read().decode()) from None

    data = json.loads(body)
    return data.get("resources", [])


def _get_json(endpoint: str, user: str, password: str,
              context: "ssl.SSLContext | None") -> dict:
    req = urllib.request.Request(
        endpoint,
        headers={
            "Authorization": _basic_auth_header(user, password),
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, context=context, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise MIQHTTPError(e.code, e.read().decode()) from None


def get_metric_rollups(
    vm_id: int | str,
    url: str | None = None,
    user: str | None = None,
    password: str | None = None,
    ca_bundle: str | None = None,
    capture_interval: str = "hourly",
) -> list[dict]:
    """Fetch hourly metric_rollups for one VM:
        /api/vms/:id/metric_rollups?expand=resources&capture_interval=hourly

    Per GOTCHA J-3 the rollups we care about are the VmOrTemplate resource's
    `cpu_usage_rate_average` and `mem_usage_absolute_average`. We do NOT pass an
    `attributes=` filter (GOTCHA G-3 — version-specific names 400 the request);
    take the default set and read those fields, tolerating their absence.

    Returns the raw rollup resource dicts (the collector maps them to the
    miq_utilization shape). TLS discipline identical to get_vms (G-6: no
    verify=False escape hatch)."""
    url = url or os.environ.get("MIQ_URL", "https://localhost/api")
    user = user or os.environ.get("MIQ_USER", "admin")
    password = password or os.environ.get("MIQ_PASS", "smartvm")
    ca_bundle = ca_bundle or os.environ.get("MIQ_CA_BUNDLE")

    if url.startswith("https://") and not ca_bundle:
        raise MIQAuthError(
            "MIQ_CA_BUNDLE not set — refusing to fetch metrics over HTTPS without "
            "a trusted CA bundle (G-6). Export the appliance cert and set "
            "MIQ_CA_BUNDLE; this client never sets verify=False.")
    context = ssl.create_default_context(cafile=ca_bundle) if ca_bundle else None

    endpoint = (url.rstrip("/") + f"/vms/{vm_id}/metric_rollups"
                f"?expand=resources&capture_interval={urllib.parse.quote(capture_interval)}")
    data = _get_json(endpoint, user, password, context)
    return data.get("resources", [])
