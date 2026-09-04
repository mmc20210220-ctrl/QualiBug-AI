from __future__ import annotations

"""Source-backed API runtime binding for persisted Agent Tasks.

This module deliberately does not infer an endpoint from an ``operation_ref``.
Callers must provide normalized API-operation candidates that came from a
persisted project authority such as OpenAPI/Swagger/Behavior IR. A binding is
usable only when exactly one distinct candidate has an explicit method, path,
and source evidence.
"""

from typing import Any

API_BINDING_SCHEMA = "qualibug.agent-task-api-binding.v1"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _normalize_evidence(value: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for raw in _rows(value):
        source_id = _text(raw.get("source_id"))
        locator = _text(
            raw.get("source_locator")
            or raw.get("locator")
            or raw.get("asset_ref")
            or raw.get("document_block_id")
            or raw.get("document_node_id")
        )
        if not source_id or not locator:
            continue
        row = {
            key: item
            for key, item in {
                "source_id": source_id,
                "source_revision": _text(
                    raw.get("source_revision") or raw.get("revision") or raw.get("version")
                ),
                "source_locator": locator,
                "asset_ref": _text(raw.get("asset_ref")),
                "quote": _text(raw.get("quote")),
                "quote_hash": _text(raw.get("quote_hash")),
                "fact_id": _text(raw.get("fact_id")),
                "derivation": _text(raw.get("derivation")),
            }.items()
            if item
        }
        key = (
            source_id,
            _text(row.get("source_revision")),
            locator,
            _text(row.get("fact_id")),
        )
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)
    return rows


def _normalize_candidate(raw: dict[str, Any], operation_ref: str) -> dict[str, Any] | None:
    if _text(raw.get("operation_ref")) != operation_ref:
        return None
    method = _text(raw.get("method")).upper()
    path = _text(raw.get("path"))
    evidence = _normalize_evidence(raw.get("evidence"))
    if not method or not path or not evidence:
        return {
            "operation_ref": operation_ref,
            "method": method,
            "path": path,
            "evidence": evidence,
            "source_kind": _text(raw.get("source_kind") or raw.get("source_type")),
            "invalid": True,
        }
    return {
        "operation_ref": operation_ref,
        "method": method,
        "path": path,
        "evidence": evidence,
        "source_kind": _text(raw.get("source_kind") or raw.get("source_type")),
        "invalid": False,
    }


def _candidate_identity(candidate: dict[str, Any]) -> tuple[str, str, tuple[tuple[str, str, str], ...]]:
    evidence_key = tuple(
        sorted(
            (
                _text(item.get("source_id")),
                _text(item.get("source_revision")),
                _text(item.get("source_locator")),
            )
            for item in _rows(candidate.get("evidence"))
        )
    )
    return (
        _text(candidate.get("method")).upper(),
        _text(candidate.get("path")),
        evidence_key,
    )


def _blocked(operation_ref: str, code: str, message: str, *, matches: int = 0) -> dict[str, Any]:
    return {
        "schema": API_BINDING_SCHEMA,
        "ok": False,
        "status": "BLOCKED",
        "operation_ref": operation_ref,
        "binding": None,
        "blocking_codes": [code],
        "reasons": [{"code": code, "message": message}],
        "match_count": int(matches),
    }


def resolve_source_backed_api_binding(
    *,
    operation_ref: str,
    operation_candidates: Any,
) -> dict[str, Any]:
    """Resolve one executable API operation without inventing runtime truth.

    ``operation_candidates`` must already be normalized from persisted project
    authorities. Matching is exact on ``operation_ref``; this function never
    derives a URL path or HTTP method from the reference string itself.
    """

    ref = _text(operation_ref)
    if not ref:
        return _blocked(
            ref,
            "API_BINDING_OPERATION_REF_MISSING",
            "Agent Task has no explicit operation_ref to bind.",
        )

    normalized = [
        candidate
        for raw in _rows(operation_candidates)
        if (candidate := _normalize_candidate(raw, ref)) is not None
    ]
    if not normalized:
        return _blocked(
            ref,
            "API_BINDING_NOT_FOUND",
            "No persisted source-backed API operation matches operation_ref.",
        )

    invalid = [candidate for candidate in normalized if candidate.get("invalid") is True]
    valid = [candidate for candidate in normalized if candidate.get("invalid") is not True]
    if invalid and not valid:
        return _blocked(
            ref,
            "API_BINDING_EVIDENCE_INCOMPLETE",
            "Matched API operation is missing method, path, or source evidence.",
            matches=len(normalized),
        )

    distinct: dict[tuple[str, str, tuple[tuple[str, str, str], ...]], dict[str, Any]] = {}
    for candidate in valid:
        distinct.setdefault(_candidate_identity(candidate), candidate)

    if len(distinct) != 1 or invalid:
        return _blocked(
            ref,
            "API_BINDING_AMBIGUOUS",
            "operation_ref does not resolve to exactly one source-backed API operation.",
            matches=len(normalized),
        )

    binding = dict(next(iter(distinct.values())))
    binding.pop("invalid", None)
    return {
        "schema": API_BINDING_SCHEMA,
        "ok": True,
        "status": "BOUND",
        "operation_ref": ref,
        "binding": binding,
        "blocking_codes": [],
        "reasons": [],
        "match_count": len(normalized),
    }
