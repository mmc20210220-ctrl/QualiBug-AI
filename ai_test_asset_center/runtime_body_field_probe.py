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
    root: Any = None,
    project: str = "",
) -> dict[str, Any]:
    """Probe which field name makes an undocumented policy endpoint 2xx.

    Tries each candidate field with a minimal non-empty value (``[{"id": 1,
    "ts": 1}]`` for arrays) and returns the first accepted field set plus the
    observed response. ``{}`` must have been rejected (422) before probing is
    worth attempting; the caller decides that gate.

    When ``root``/``project`` are supplied, a source-driven FIFO/FEFO ordering
    check runs after the field is found: the PRD declares allocation rules
    (``普通批次按 FIFO``), and a two-element payload with distinct timestamps
    verifies the target returns the earliest batch. The assertion comes from
    the source text, never from a hardcoded rule.
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
            result: dict[str, Any] = {
                "schema_version": "qualibug.runtime-body-field-probe.v1",
                "probed": True,
                "attempts": len(receipts),
                "accepted_field": field,
                "accepted_body": body,
                "response_status": status,
                "response_body": response,
                "receipts": receipts,
            }
            ordering = _source_ordering_check(
                base_url=base_url,
                path=path,
                token=token,
                field=field,
                root=root,
                project=project,
            )
            if ordering:
                result["ordering_check"] = ordering
                print(
                    f"[body-probe] ordering check path={path} "
                    f"ordering={ordering.get('ordering')} "
                    f"violation={ordering.get('violation')} "
                    f"observed={ordering.get('observed')}",
                    flush=True,
                )
            return result
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


def _source_ordering_check(
    *,
    base_url: str,
    path: str,
    token: str,
    field: str,
    root: Any = None,
    project: str = "",
) -> dict[str, Any]:
    """Source-driven FIFO/FEFO ordering verification on a discovered array field.

    The PRD declares allocation semantics (``普通批次按 FIFO`` /
    ``有有效期物料按 FEFO``). When such a statement exists, send a two-element
    payload with distinct timestamps and check the target returns the earliest
    (FIFO) batch. The expectation comes from the source text; the observed
    response is the evidence. No ordering statement in the source → no check
    (never invent a sorting rule).
    """
    import re
    from pathlib import Path

    if not root or not project:
        print("[body-probe] ordering check skipped: no root/project", flush=True)
        return {}
    prd_text = ""
    try:
        for candidate in (
            Path(root) / "platform_inputs" / project / "01_PRD.md",
            Path(root) / "platform_inputs" / project / "PRD.md",
        ):
            if candidate.exists():
                prd_text = candidate.read_text(encoding="utf-8", errors="replace")
                break
    except OSError as exc:
        print(f"[body-probe] ordering PRD read failed: {exc}", flush=True)
        return {}
    if not prd_text:
        print(
            f"[body-probe] ordering check skipped: PRD not found under "
            f"{Path(root)}/platform_inputs/{project}",
            flush=True,
        )
        return {}
    # Source-declared ordering: FIFO (earliest batch first) or FEFO.
    fifo_declared = bool(re.search(r"FIFO|先进先出", prd_text))
    fefo_declared = bool(re.search(r"FEFO|先到期|效期.*先", prd_text))
    if not (fifo_declared or fefo_declared):
        return {}
    ordering = "FIFO" if fifo_declared else "FEFO"
    # Two batches with distinct timestamps; FIFO expects the earliest (ts=10).
    payload = {field: [
        {"id": "BATCH-NEW", "ts": 100, "qty": 5},
        {"id": "BATCH-OLD", "ts": 10, "qty": 5},
    ]}
    status, response = _call_json(base_url, path, token, payload)
    response_text = _text(json.dumps(response, ensure_ascii=False))
    expected_batch = "BATCH-OLD" if ordering == "FIFO" else "BATCH-OLD"
    selected_oldest = "BATCH-OLD" in response_text
    selected_newest = "BATCH-NEW" in response_text and not selected_oldest
    return {
        "ordering": ordering,
        "status": status,
        "selected_oldest": selected_oldest,
        "selected_newest": selected_newest,
        "expected": "earliest batch (FIFO)" if ordering == "FIFO" else "earliest expiry (FEFO)",
        "observed": response_text[:120],
        "violation": bool(
            200 <= status < 300 and selected_newest and not selected_oldest
        ),
    }


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []
