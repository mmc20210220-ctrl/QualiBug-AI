"""Read-only runtime probing of target frontend bundles for API routes.

Web systems without a standard document endpoint still expose their full API
surface verbatim inside the frontend JavaScript bundle: every path string the
SPA calls is a literal in the bundle. This module fetches the approved
non-production frontend entry, follows same-host script references, extracts
``/api/...`` route literals, and returns an OpenAPI-shaped route inventory
that the scan mainline can merge into its operation surface.

Safety: GET only; approved non-production frontend URL only (caller
contract); same-host pinning; bounded script count/size; bounded total
budget; HTML entry parsing only; template literals normalized to ``{param}``
placeholders. Every outcome is receipted; probing never blocks a scan.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

_MAX_SCRIPTS = 20
_MAX_SCRIPT_BYTES = 2 * 1024 * 1024
_PROBE_TIMEOUT_SECONDS = 4.0
_TOTAL_BUDGET_SECONDS = 30.0
_USER_AGENT = "qualibug-runtime-frontend-route-probe/1.0"
_RECEIPT_SCHEMA = "qualibug.runtime-frontend-route-probe.v1"

_SCRIPT_SRC_RE = re.compile(r"""<script\b[^>]*\bsrc\s*=\s*["']([^"']+)["']""", re.I)
_PLAIN_ROUTE_RE = re.compile(r"""["'](/api/[A-Za-z0-9_\-/:]+)["']""")
_TEMPLATE_ROUTE_RE = re.compile(r"`(/api/[^`]*?)`")
_TEMPLATE_PARAM_RE = re.compile(r"\$\{[^}]*\}")


def _base_host(url: str) -> str:
    return urllib.parse.urlparse(url).netloc.lower()


def _fetch(url: str, timeout: float, host: str) -> tuple[int, bytes | None]:
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"User-Agent": _USER_AGENT, "Accept": "*/*"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        final_host = urllib.parse.urlparse(response.geturl()).netloc.lower()
        if final_host != host:
            return 0, None
        return response.status, response.read(_MAX_SCRIPT_BYTES + 1)


def _clean_route(raw: str) -> str:
    route = raw.strip().rstrip("/")
    route = _TEMPLATE_PARAM_RE.sub("{param}", route)
    # A route still containing template artifacts is not a stable literal.
    if "{" in route and ("}" not in route or "${" in route):
        return ""
    # Normalize nested placeholders that survived: /api/x/{param}/sub
    if route.count("{") != route.count("}"):
        return ""
    return route


def _extract_routes(bundle_text: str) -> list[str]:
    routes: list[str] = []
    for match in _PLAIN_ROUTE_RE.finditer(bundle_text or ""):
        routes.append(match.group(1))
    for match in _TEMPLATE_ROUTE_RE.finditer(bundle_text or ""):
        # Template literal route: keep only the literal prefix up to the first
        # dynamic expression; the prefix is still a real, stable route family.
        literal = match.group(1).split("${", 1)[0].rstrip("/")
        if literal and literal.count("/") >= 1:
            routes.append(literal)
    cleaned: list[str] = []
    for raw in routes:
        route = _clean_route(raw)
        if route and route not in cleaned:
            cleaned.append(route)
    return cleaned


def _route_inventory_document(routes: list[str]) -> str:
    paths = {
        route: {
            "get": {},
            "x-qualibug-route-source": "frontend_bundle",
        }
        for route in sorted(routes)
    }
    return json.dumps(
        {
            "openapi": "3.0.0",
            "info": {
                "title": "runtime-frontend-route-inventory",
                "version": "1.0",
            },
            "paths": paths,
        },
        ensure_ascii=False,
    )


def probe_frontend_routes(
    frontend_url: str,
    *,
    timeout_seconds: float = _PROBE_TIMEOUT_SECONDS,
    total_budget_seconds: float = _TOTAL_BUDGET_SECONDS,
) -> dict[str, Any]:
    """Probe an approved non-production frontend for its API route inventory.

    Returns a receipt with ``status`` in ``skipped|found|not_found|failed``.
    On ``found`` the receipt carries ``routes`` and ``document_text`` (a
    synthetic OpenAPI route inventory ready to merge into the operation
    surface).
    """
    entry = str(frontend_url or "").strip().rstrip("/")
    if not entry:
        return {"schema_version": _RECEIPT_SCHEMA, "status": "skipped", "reason": "empty_frontend_url"}
    host = _base_host(entry)
    if not host:
        return {"schema_version": _RECEIPT_SCHEMA, "status": "skipped", "reason": "invalid_frontend_url"}
    started = time.monotonic()
    attempts: list[dict[str, Any]] = []

    try:
        status, body = _fetch(entry, timeout_seconds, host)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        return {
            "schema_version": _RECEIPT_SCHEMA,
            "status": "failed",
            "reason": f"entry_fetch_failed:{type(exc).__name__}",
            "attempts": attempts,
        }
    if body is None:
        return {
            "schema_version": _RECEIPT_SCHEMA,
            "status": "failed",
            "reason": "host_pinned_redirect_blocked",
            "attempts": attempts,
        }
    if len(body) > _MAX_SCRIPT_BYTES:
        body = body[:_MAX_SCRIPT_BYTES]
    html = body.decode("utf-8", errors="replace")
    attempts.append({"url": entry, "outcome": "entry_fetched", "status": status, "bytes": len(body)})

    script_srcs = _SCRIPT_SRC_RE.findall(html)
    script_urls: list[str] = []
    for src in script_srcs:
        if not src.strip() or src.strip().startswith(("data:", "blob:")):
            continue
        resolved = urllib.parse.urljoin(entry + "/", src.strip())
        if _base_host(resolved) != host:
            continue
        if resolved not in script_urls:
            script_urls.append(resolved)
    script_urls = script_urls[:_MAX_SCRIPTS]

    routes: list[str] = []
    for url in script_urls:
        if time.monotonic() - started > total_budget_seconds:
            attempts.append({"url": url, "outcome": "budget_exhausted"})
            continue
        try:
            status, bundle = _fetch(url, timeout_seconds, host)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
            attempts.append({"url": url, "outcome": "script_fetch_failed", "error": type(exc).__name__})
            continue
        if bundle is None:
            attempts.append({"url": url, "outcome": "host_pinned_redirect_blocked"})
            continue
        if len(bundle) > _MAX_SCRIPT_BYTES:
            attempts.append({"url": url, "outcome": "oversize_script_skipped"})
            continue
        text = bundle.decode("utf-8", errors="replace")
        found = _extract_routes(text)
        attempts.append({"url": url, "outcome": "routes_extracted" if found else "no_routes", "routes": len(found), "bytes": len(bundle)})
        for route in found:
            if route not in routes:
                routes.append(route)

    if not routes:
        return {
            "schema_version": _RECEIPT_SCHEMA,
            "status": "not_found",
            "attempts": attempts,
        }
    return {
        "schema_version": _RECEIPT_SCHEMA,
        "status": "found",
        "routes": sorted(routes),
        "route_count": len(routes),
        "document_text": _route_inventory_document(routes),
        "attempts": attempts,
    }
