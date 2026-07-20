"""Automatic runtime binding resolution for experiment execution.

Resolves path placeholders (e.g. {id}, {orderId}) by calling GET list
endpoints declared in Behavior IR. Fully data-driven and industry-neutral:
only consumes operations declared in the Behavior IR graph.

Schema: qualibug.runtime-binding-resolver.v1
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
import urllib.error
from typing import Any

_SCHEMA = "qualibug.runtime-binding-resolver.v1"
_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")

# Timeout for binding resolution HTTP calls (seconds)
_BINDING_TIMEOUT = 10


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _extract_placeholders(path: str) -> list[str]:
    """Extract placeholder names from a path template."""
    return _PLACEHOLDER_RE.findall(path or "")


def _find_list_endpoints_for_entity(
    behavior_ir: dict[str, Any],
    placeholder_name: str,
) -> list[dict[str, Any]]:
    """Find GET endpoints that can resolve a placeholder.

    Strategy: match placeholder name to entity/collection names in the IR.
    E.g. {orderId} matches GET /api/orders or GET /api/v1/orders.
    """
    operations = _list(behavior_ir.get("operations"))
    candidates: list[dict[str, Any]] = []

    # Normalize placeholder: orderId -> order, userId -> user, id -> generic
    ph_lower = placeholder_name.lower()
    # Strip common suffixes
    entity_hint = re.sub(r"(id|_id|Id)$", "", ph_lower).strip("_")

    for op in operations:
        if not isinstance(op, dict):
            continue
        method = _text(op.get("method")).upper()
        if method not in ("GET", "HEAD"):
            continue
        path = _text(op.get("path") or op.get("raw_path"))
        if not path:
            continue
        # Skip paths with unresolved placeholders (can't call them)
        if _PLACEHOLDER_RE.search(path):
            continue
        # Match: path contains the entity hint
        path_lower = path.lower()
        if entity_hint and entity_hint in path_lower:
            candidates.append(op)
        elif not entity_hint:
            # Generic {id} - match any list endpoint
            candidates.append(op)

    return candidates


def _call_get_endpoint(
    base_url: str,
    path: str,
    token: str,
    timeout: int = _BINDING_TIMEOUT,
) -> dict[str, Any] | None:
    """Call a GET endpoint and return parsed JSON response."""
    url = base_url.rstrip("/") + "/" + path.lstrip("/")
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        req = urllib.request.Request(url, headers=headers, method="GET")
        resp = urllib.request.urlopen(req, timeout=timeout)
        body = resp.read().decode("utf-8", errors="replace")
        return json.loads(body)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError):
        return None


def _extract_id_from_response(response: Any, placeholder_name: str) -> str:
    """Extract a resource ID from a GET list response.

    Handles common response shapes:
    - [ {id: "...", ...}, ... ]
    - { data: [ {id: "...", ...} ] }
    - { items: [ ... ] }
    - { results: [ ... ] }
    - { content: [ ... ] }
    """
    items: list[Any] = []
    if isinstance(response, list):
        items = response
    elif isinstance(response, dict):
        # Try common wrapper keys
        for key in ("data", "items", "results", "content", "records", "rows", "list"):
            val = response.get(key)
            if isinstance(val, list):
                items = val
                break
        if not items and isinstance(response.get("data"), dict):
            # Nested: {data: {items: [...]}}
            nested = response["data"]
            for key in ("items", "results", "content", "records", "rows", "list"):
                val = nested.get(key)
                if isinstance(val, list):
                    items = val
                    break

    if not items:
        return ""

    # Get first item's ID
    first = items[0] if items else {}
    if not isinstance(first, dict):
        return ""

    # Try common ID field names
    ph_lower = placeholder_name.lower()
    entity_hint = re.sub(r"(id|_id|Id)$", "", ph_lower).strip("_")

    # Exact match first
    for field in (placeholder_name, ph_lower, f"{entity_hint}_id", f"{entity_hint}Id"):
        val = first.get(field)
        if val is not None and str(val).strip():
            return str(val).strip()

    # Generic ID fields
    for field in ("id", "ID", "Id", "_id", "uuid", "key"):
        val = first.get(field)
        if val is not None and str(val).strip():
            return str(val).strip()

    return ""


def auto_resolve_bindings(
    behavior_ir: dict[str, Any],
    actor_tokens: dict[str, str],
    base_url: str,
    *,
    required_placeholders: set[str] | None = None,
    max_resolution_attempts: int = 20,
) -> dict[str, Any]:
    """Automatically resolve path placeholders by calling GET endpoints.

    Args:
        behavior_ir: The Behavior IR v2 model.
        actor_tokens: Map of role/secret_ref -> bearer token.
        base_url: Target base URL.
        required_placeholders: If provided, only resolve these placeholders.
        max_resolution_attempts: Max GET calls to make.

    Returns:
        {
            "schema_version": ...,
            "bindings": {placeholder_name: resolved_value},
            "receipts": [{placeholder, endpoint, status, value_fingerprint}],
            "attempts": int,
            "resolved_count": int,
            "failed_count": int,
        }
    """
    if not base_url:
        return {
            "schema_version": _SCHEMA,
            "bindings": {},
            "receipts": [],
            "attempts": 0,
            "resolved_count": 0,
            "failed_count": 0,
        }

    # Collect all placeholders from Behavior IR operations
    operations = _list(behavior_ir.get("operations"))
    all_placeholders: set[str] = set()
    for op in operations:
        if not isinstance(op, dict):
            continue
        path = _text(op.get("path") or op.get("raw_path"))
        for ph in _extract_placeholders(path):
            all_placeholders.add(ph)

    if required_placeholders:
        all_placeholders &= required_placeholders

    if not all_placeholders:
        return {
            "schema_version": _SCHEMA,
            "bindings": {},
            "receipts": [],
            "attempts": 0,
            "resolved_count": 0,
            "failed_count": 0,
        }

    # Pick the best available token (prefer admin, then any)
    token = ""
    for role_key in ("admin", "administrator", "superuser", "root"):
        if role_key in actor_tokens:
            token = actor_tokens[role_key]
            break
    if not token and actor_tokens:
        token = next(iter(actor_tokens.values()), "")

    bindings: dict[str, str] = {}
    receipts: list[dict[str, Any]] = []
    attempts = 0

    for placeholder in sorted(all_placeholders):
        if attempts >= max_resolution_attempts:
            break
        if placeholder in bindings:
            continue

        # Find GET endpoints that can resolve this placeholder
        candidates = _find_list_endpoints_for_entity(behavior_ir, placeholder)
        resolved = False

        for endpoint in candidates[:3]:  # Try up to 3 endpoints per placeholder
            if attempts >= max_resolution_attempts:
                break
            attempts += 1

            path = _text(endpoint.get("path") or endpoint.get("raw_path"))
            response = _call_get_endpoint(base_url, path, token)

            if response is None:
                receipts.append({
                    "placeholder": placeholder,
                    "endpoint": path,
                    "status": "no_response",
                    "value_fingerprint": "",
                })
                continue

            value = _extract_id_from_response(response, placeholder)
            if value:
                bindings[placeholder] = value
                receipts.append({
                    "placeholder": placeholder,
                    "endpoint": path,
                    "status": "resolved",
                    "value_fingerprint": value[:8] + "..." if len(value) > 8 else value,
                })
                resolved = True
                break
            else:
                receipts.append({
                    "placeholder": placeholder,
                    "endpoint": path,
                    "status": "empty_response",
                    "value_fingerprint": "",
                })

        if not resolved and placeholder not in bindings:
            receipts.append({
                "placeholder": placeholder,
                "endpoint": "",
                "status": "unresolved",
                "value_fingerprint": "",
            })

    resolved_count = len(bindings)
    failed_count = len(all_placeholders) - resolved_count

    return {
        "schema_version": _SCHEMA,
        "bindings": bindings,
        "receipts": receipts,
        "attempts": attempts,
        "resolved_count": resolved_count,
        "failed_count": failed_count,
    }


def collect_required_placeholders(
    experiments: list[dict[str, Any]],
    behavior_ir: dict[str, Any],
) -> set[str]:
    """Collect all unresolved placeholders from a batch of experiments."""
    ops_by_id = {
        _text(op.get("id")): op
        for op in _list(behavior_ir.get("operations"))
        if isinstance(op, dict) and _text(op.get("id"))
    }
    placeholders: set[str] = set()

    for exp in experiments:
        if not isinstance(exp, dict):
            continue
        for plan_key in ("control_plan", "treatment_plan", "setup_plan"):
            for step in _list(exp.get(plan_key)):
                if not isinstance(step, dict):
                    continue
                op_ref = _text(step.get("operation_ref"))
                op = ops_by_id.get(op_ref, {})
                path = _text(step.get("path") or op.get("path") or op.get("raw_path"))
                for ph in _extract_placeholders(path):
                    placeholders.add(ph)

    return placeholders
