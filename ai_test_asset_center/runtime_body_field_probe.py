"""Runtime request-body field probing for undocumented policy endpoints.

A policy/decision endpoint (``/resolve``, ``/check``, ``/calculate``) often
declares a permissive ``{"type": "object"}`` request body in the OpenAPI while
the implementation reads specific fields (e.g. ``candidates``). A bodyless or
``{}`` request is rejected 422 and the endpoint's real semantics — including
its defects — stay untestable. This module performs a bounded, receipted
runtime probe: try candidate field names with a minimal non-empty value and
record the first field set that the target accepts with 2xx. The probe is
purely observational (the target's own response is the evidence); the
candidate vocabulary is generic collection nouns plus array fields already
declared by any service's request schemas — never industry or benchmark terms.
"""

from __future__ import annotations

import json
import urllib.request
import urllib.error
from typing import Any

# Generic collection-shaped nouns used to probe undocumented array fields.
# Explicit, bounded, industry-neutral: a real enterprise system may name its
# payload list any of these, and the probe is a finite list with a receipt.
_GENERIC_COLLECTION_FIELDS = (
    "candidates",
    "items",
    "rows",
    "list",
    "data",
    "records",
    "values",
    "batches",
    "lines",
    "details",
    "entries",
    "elements",
    "payloads",
    "resources",
)

# Probe budget: one HTTP request per candidate per endpoint. 14 candidates is
# a bounded, receipted cost; the probe never fuzzes values.
_MAX_PROBE_FIELDS = 20


def _text(value: Any) -> str:
    return str(value or "").strip()


def _call_json(
    base_url: str,
    path: str,
    token: str,
    body: Any,
    timeout: float = 8.0,
) -> tuple[int, Any]:
    url = base_url.rstrip("/") + "/" + str(path).lstrip("/")
    req = urllib.request.Request(url, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    req.data = json.dumps(body).encode("utf-8")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read(200_000).decode("utf-8", errors="replace")
            try:
                return int(response.status), json.loads(raw) if raw else None
            except Exception:
                return int(response.status), raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return int(exc.code), json.loads(raw) if raw else None
        except Exception:
            return int(exc.code), raw


def _declared_array_fields(behavior_ir: dict[str, Any]) -> list[str]:
    """Array-type request fields already declared by any service's schemas.

    Source-grounded candidates: a field the enterprise documents anywhere as
    an array is a legitimate probe target on an undocumented endpoint too.
    """
    fields: list[str] = []
    seen: set[str] = set()
    for op in _list(behavior_ir.get("operations")):
        if not isinstance(op, dict):
            continue
        schema = _dict(op.get("request_schema"))
        media = _dict(_dict(schema.get("content")).get("application/json"))
        props = _dict(media.get("schema")).get("properties")
        for name, prop in _dict(props).items():
            if isinstance(prop, dict) and _text(prop.get("type")).lower() == "array":
                norm = _text(name)
                if norm and norm not in seen:
                    seen.add(norm)
                    fields.append(norm)
    return fields


def probe_undocumented_request_fields(
    *,
    base_url: str,
    path: str,
    token: str,
    behavior_ir: dict[str, Any] | None = None,
    max_fields: int = _MAX_PROBE_FIELDS,
) -> dict[str, Any]:
    """Probe which field name makes an undocumented policy endpoint 2xx.

    Tries each candidate field with a minimal non-empty value (``[{"id": 1,
    "ts": 1}]`` for arrays) and returns the first accepted field set plus the
    observed response. ``{}`` must have been rejected (422) before probing is
    worth attempting; the caller decides that gate.

    Returns:
        {
            "schema_version": "qualibug.runtime-body-field-probe.v1",
            "probed": bool, "attempts": int, "accepted_field": str|"",
            "accepted_body": {...}|None, "response_status": int|0,
            "receipts": [{field, status, response_preview}],
        }
    """
    candidates: list[str] = []
    seen: set[str] = set()
    for field in (*_declared_array_fields(behavior_ir or {}), *_GENERIC_COLLECTION_FIELDS):
        norm = _text(field)
        if norm and norm not in seen:
            seen.add(norm)
            candidates.append(norm)
        if len(candidates) >= max_fields:
            break

    receipts: list[dict[str, Any]] = []
    probe_body = [{"id": 1, "ts": 1}]
    for field in candidates:
        body = {field: probe_body}
        status, response = _call_json(base_url, path, token, body)
        preview = _text(json.dumps(response, ensure_ascii=False))[:120]
        receipts.append({
            "field": field,
            "status": status,
            "response_preview": preview,
        })
        if 200 <= status < 300:
            return {
                "schema_version": "qualibug.runtime-body-field-probe.v1",
                "probed": True,
                "attempts": len(receipts),
                "accepted_field": field,
                "accepted_body": body,
                "response_status": status,
                "response_body": response,
                "receipts": receipts,
            }
    return {
        "schema_version": "qualibug.runtime-body-field-probe.v1",
        "probed": True,
        "attempts": len(receipts),
        "accepted_field": "",
        "accepted_body": None,
        "response_status": 0,
        "response_body": None,
        "receipts": receipts,
    }


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []
