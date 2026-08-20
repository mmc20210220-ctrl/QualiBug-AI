"""Low-authority runtime implementation-surface projection.

Runtime Fact Candidates may prove that a read-only implementation surface exists
without proving any business rule about it. This module exposes only those
receipt-backed GET/HEAD identities to Behavior-IR expansion. It never mutates
accepted facts, creates authorization/state relations, or promotes write APIs.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

_ALLOWED_KINDS = frozenset({"runtime_operation", "runtime_observation_path"})
_ALLOWED_METHODS = frozenset({"GET", "HEAD"})


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _stable_operation_id(method: str, path: str, candidate_id: str) -> str:
    material = json.dumps(
        {"method": method, "path": path, "candidate_id": candidate_id},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "runtime-candidate:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]


def candidate_read_operations(ledger: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return read-only implementation operations proven by runtime candidates."""
    operations: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw in _list(_dict(ledger).get("candidates")):
        if not isinstance(raw, dict):
            continue
        row = _dict(raw)
        if _text(row.get("status")).upper() != "CANDIDATE":
            continue
        if _text(row.get("kind")).lower() not in _ALLOWED_KINDS:
            continue
        method = _text(row.get("method")).upper()
        path = _text(row.get("path"))
        candidate_id = _text(row.get("candidate_id"))
        evidence_refs = [
            _text(value) for value in _list(row.get("evidence_refs")) if _text(value)
        ]
        if (
            method not in _ALLOWED_METHODS
            or not path.startswith("/")
            or not candidate_id
            or not evidence_refs
        ):
            continue
        key = (method, path)
        if key in seen:
            continue
        seen.add(key)
        operations.append(
            {
                "id": _stable_operation_id(method, path, candidate_id),
                "operation_id": _stable_operation_id(method, path, candidate_id),
                "method": method,
                "path": path,
                "source_id": "runtime_fact_candidate",
                "summary": "Runtime-observed read implementation surface",
                "description": (
                    "Read-only implementation surface observed by governed runtime evidence; "
                    "does not assert a business contract."
                ),
                "parameters": [],
                "request_schema": {},
                "response_schema": {},
                "read_write": "read",
                "side_effect_class": "read",
                "derivation": "runtime-fact-candidate",
                "authority_grade": "RUNTIME_OBSERVED",
                "runtime_fact_candidate_id": candidate_id,
                "runtime_evidence_refs": evidence_refs,
                "source_refs": [
                    dict(ref)
                    for ref in _list(row.get("source_refs"))
                    if isinstance(ref, dict)
                ],
            }
        )
    return operations


def merge_candidate_read_operations(
    operations: list[dict[str, Any]],
    ledger: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Append candidate read surfaces without overriding documented identity."""
    merged = [dict(row) for row in operations if isinstance(row, dict)]
    existing = {
        (_text(row.get("method")).upper(), _text(row.get("path")))
        for row in merged
        if _text(row.get("method")) and _text(row.get("path"))
    }
    added: list[dict[str, Any]] = []
    for operation in candidate_read_operations(ledger):
        key = (_text(operation.get("method")).upper(), _text(operation.get("path")))
        if key in existing:
            continue
        existing.add(key)
        merged.append(operation)
        added.append(operation)
    return merged, {
        "schema_version": "qualibug.runtime-implementation-candidate-projection.v1",
        "status": "APPLIED",
        "candidate_read_operation_count": len(candidate_read_operations(ledger)),
        "added_operation_count": len(added),
        "added_operations": [
            {"method": _text(row.get("method")), "path": _text(row.get("path"))}
            for row in added
        ],
        "authority": "receipt_backed_runtime_candidate_read_surface_only",
        "write_surface_promoted": False,
        "business_fact_promoted": False,
    }


__all__ = ["candidate_read_operations", "merge_candidate_read_operations"]
