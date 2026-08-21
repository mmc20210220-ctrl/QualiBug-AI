"""Read-only runtime probing for standard API document endpoints.

A genuinely unfamiliar target's strongest documented-surface source is its
own standard document endpoint (``/openapi.json``, ``/swagger.json``,
``/v2/api-docs``, ...). When the submitted source materials carry no
machine-parseable API contract, this module probes those endpoints on an
already-approved non-production base URL with short timeouts, validates the
response shape as an OpenAPI/Swagger contract, and returns the raw document
text so the scan mainline can expand its operation surface.

Safety: GET only; approved non-production base URL only (caller contract);
bounded path whitelist; bounded response size; bounded total probe budget;
host-pinned redirects. Every outcome is receipted and a probe failure never
blocks a scan.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

_PROBE_PATHS = (
    "/openapi.json",
    "/swagger.json",
    "/v3/api-docs",
    "/v2/api-docs",
    "/api-docs.json",
    "/api/openapi.json",
    "/docs/openapi.json",
    "/swagger/v1/swagger.json",
    "/api/swagger.json",
    "/openapi.yaml",
    "/swagger.yaml",
)
_MAX_DOC_BYTES = 2 * 1024 * 1024
_PROBE_TIMEOUT_SECONDS = 4.0
_TOTAL_BUDGET_SECONDS = 15.0
_USER_AGENT = "qualibug-runtime-api-doc-probe/1.0"
_RECEIPT_SCHEMA = "qualibug.runtime-api-doc-probe.v1"


def _base_host(base_url: str) -> str:
    return urllib.parse.urlparse(base_url).netloc.lower()


def _looks_like_api_contract(text: str) -> bool:
    """Validate that the probed payload is an OpenAPI/Swagger-style contract.

    Loose structural validation: a contract must be JSON/YAML-ish and carry a
    ``paths`` map or list of operations. Full schema validation stays with the
    universal parser downstream.
    """
    stripped = text.strip()
    if not stripped:
        return False
    payload: Any = None
    if stripped.startswith("{"):
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            return False
        return isinstance(payload, dict) and isinstance(payload.get("paths"), dict) and bool(payload["paths"])
    if stripped.startswith("["):
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            return False
        return isinstance(payload, list) and bool(payload)
    # YAML-ish OpenAPI documents carry "openapi:" or "swagger:" plus "paths:".
    lowered = stripped.lower()
    return ("openapi:" in lowered or "swagger:" in lowered) and "paths:" in lowered


def probe_runtime_api_document(
    base_url: str,
    *,
    timeout_seconds: float = _PROBE_TIMEOUT_SECONDS,
    total_budget_seconds: float = _TOTAL_BUDGET_SECONDS,
) -> dict[str, Any]:
    """Probe standard document endpoints on an approved non-production base URL.

    Returns a receipt with ``status`` in
    ``disabled|skipped|found|not_found|failed``. On ``found`` the receipt
    carries ``document_text`` and ``source_path``.
    """
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        return {"schema_version": _RECEIPT_SCHEMA, "status": "skipped", "reason": "empty_base_url"}
    host = _base_host(base)
    if not host:
        return {"schema_version": _RECEIPT_SCHEMA, "status": "skipped", "reason": "invalid_base_url"}
    attempts: list[dict[str, Any]] = []
    started = time.monotonic()
    for path in _PROBE_PATHS:
        if time.monotonic() - started > total_budget_seconds:
            attempts.append({"path": path, "outcome": "budget_exhausted"})
            continue
        url = f"{base}{path}"
        outcome: dict[str, Any] = {"path": path}
        request = urllib.request.Request(
            url,
            method="GET",
            headers={"User-Agent": _USER_AGENT, "Accept": "application/json, application/yaml, text/plain, */*"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                final_host = urllib.parse.urlparse(response.geturl()).netloc.lower()
                if final_host != host:
                    outcome.update({"outcome": "host_pinned_redirect_blocked", "status": response.status})
                    attempts.append(outcome)
                    continue
                content_type = str(response.headers.get("Content-Type") or "")
                if "text/html" in content_type:
                    outcome.update({"outcome": "html_response_skipped", "status": response.status})
                    attempts.append(outcome)
                    continue
                body = response.read(_MAX_DOC_BYTES + 1)
                if len(body) > _MAX_DOC_BYTES:
                    outcome.update({"outcome": "oversize_response", "status": response.status})
                    attempts.append(outcome)
                    continue
                text = body.decode("utf-8", errors="replace")
                if not _looks_like_api_contract(text):
                    outcome.update({"outcome": "not_an_api_contract", "status": response.status})
                    attempts.append(outcome)
                    continue
                attempts.append({"path": path, "outcome": "found", "status": response.status, "bytes": len(body)})
                return {
                    "schema_version": _RECEIPT_SCHEMA,
                    "status": "found",
                    "source_path": path,
                    "document_text": text,
                    "attempts": attempts,
                }
        except urllib.error.HTTPError as exc:
            outcome.update({"outcome": "http_error", "status": exc.code})
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            outcome.update({"outcome": "network_error", "error": type(exc).__name__})
        attempts.append(outcome)
    return {"schema_version": _RECEIPT_SCHEMA, "status": "not_found", "attempts": attempts}
