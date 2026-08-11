"""Unique cleanup-operation authority for automatically created fixtures.

A destructive compensation route cannot be chosen by source order. This module
resolves one create operation to exactly one cleanup operation using two authority
tiers:

1. one source-backed, non-conflicting ``compensates`` relation that links the
   exact create operation to one declared mutating compensator; otherwise
2. one identity-bound DELETE on the same source collection.

Multiple candidates within the winning tier are AMBIGUOUS. Structural DELETE
requires exactly one path identity placeholder, so bulk collection DELETE is
never automatic fixture cleanup.
"""
from __future__ import annotations

from typing import Any

from .real_id_resolver import (
    collection_path,
    infer_path_params,
    normalize_path_placeholders,
)

SCHEMA_VERSION = "qualibug.cleanup-operation-authority.v1"
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _operation_index(behavior_ir: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        _text(row.get("id") or row.get("operation_id")): row
        for row in _list(_dict(behavior_ir).get("operations"))
        if isinstance(row, dict)
        and _text(row.get("id") or row.get("operation_id"))
    }


def _operation_projection(
    operation: dict[str, Any],
    *,
    authority: str,
    compensates_operation_ref: str,
) -> dict[str, Any]:
    return {
        "operation_ref": _text(operation.get("id") or operation.get("operation_id")),
        "method": _text(operation.get("method")).upper(),
        "path": normalize_path_placeholders(
            _text(operation.get("path") or operation.get("raw_path"))
        ),
        "source": authority,
        "compensates_operation_ref": _text(compensates_operation_ref),
    }


def _source_compensator_candidates(
    create_operation: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> list[dict[str, Any]]:
    create_ref = _text(
        create_operation.get("id") or create_operation.get("operation_id")
    )
    operations = _operation_index(behavior_ir)
    candidates: dict[str, dict[str, Any]] = {}
    if not create_ref:
        return []

    for raw in _list(_dict(behavior_ir).get("relations")):
        relation = _dict(raw)
        if (
            _text(relation.get("relation_type")) != "compensates"
            or not _list(relation.get("source_refs"))
            or _text(relation.get("status")) in {"conflicting", "unsupported"}
        ):
            continue
        refs = {
            _text(relation.get("operation_ref")),
            _text(relation.get("from_ref")),
            _text(relation.get("to_ref")),
            _text(relation.get("compensator_ref")),
            _text(relation.get("cleanup_operation_ref")),
        }
        refs.discard("")
        if create_ref not in refs:
            continue

        explicit = _text(
            relation.get("compensator_ref")
            or relation.get("cleanup_operation_ref")
        )
        candidate_refs: set[str] = set()
        if explicit:
            candidate_refs.add(explicit)
        if _text(relation.get("to_ref")) == create_ref:
            for key in ("operation_ref", "from_ref"):
                value = _text(relation.get(key))
                if value and value != create_ref:
                    candidate_refs.add(value)

        for candidate_ref in candidate_refs:
            operation = _dict(operations.get(candidate_ref))
            method = _text(operation.get("method")).upper()
            path = normalize_path_placeholders(
                _text(operation.get("path") or operation.get("raw_path"))
            )
            if (
                not operation
                or method not in _WRITE_METHODS
                or not path.startswith("/")
            ):
                continue
            candidates[candidate_ref] = operation

    return [candidates[key] for key in sorted(candidates)]


def _identity_delete_candidates(
    create_operation: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> list[dict[str, Any]]:
    create_path = normalize_path_placeholders(
        _text(create_operation.get("path") or create_operation.get("raw_path"))
    ).rstrip("/")
    if not create_path.startswith("/") or infer_path_params(create_path):
        return []

    candidates: dict[str, dict[str, Any]] = {}
    for operation_ref, operation in _operation_index(behavior_ir).items():
        if _text(operation.get("method")).upper() != "DELETE":
            continue
        path = normalize_path_placeholders(
            _text(operation.get("path") or operation.get("raw_path"))
        )
        placeholders = infer_path_params(path)
        if len(placeholders) != 1:
            continue
        if normalize_path_placeholders(collection_path(path)).rstrip("/") != create_path:
            continue
        candidates[operation_ref] = operation
    return [candidates[key] for key in sorted(candidates)]


def resolve_cleanup_operation(
    create_operation: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    """Resolve one cleanup route or return a named missing/ambiguity receipt."""

    create_ref = _text(
        create_operation.get("id") or create_operation.get("operation_id")
    )
    if not create_ref:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "UNRESOLVED",
            "reason_code": "CLEANUP_CREATE_OPERATION_IDENTITY_MISSING",
            "create_operation_ref": "",
            "cleanup_operation": {},
            "candidate_operation_ids": [],
            "source_order_selection_allowed": False,
        }

    explicit = _source_compensator_candidates(create_operation, behavior_ir)
    if len(explicit) == 1:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "RESOLVED",
            "reason_code": "",
            "create_operation_ref": create_ref,
            "authority": "source_compensates_relation",
            "cleanup_operation": _operation_projection(
                explicit[0],
                authority="explicit_compensator_relation",
                compensates_operation_ref=create_ref,
            ),
            "candidate_operation_ids": [
                _text(explicit[0].get("id") or explicit[0].get("operation_id"))
            ],
            "source_order_selection_allowed": False,
        }
    if len(explicit) > 1:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "UNRESOLVED",
            "reason_code": "CLEANUP_COMPENSATOR_AMBIGUOUS",
            "create_operation_ref": create_ref,
            "cleanup_operation": {},
            "candidate_operation_ids": [
                _text(row.get("id") or row.get("operation_id"))
                for row in explicit
            ],
            "source_order_selection_allowed": False,
        }

    deletes = _identity_delete_candidates(create_operation, behavior_ir)
    if len(deletes) == 1:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "RESOLVED",
            "reason_code": "",
            "create_operation_ref": create_ref,
            "authority": "identity_bound_same_collection_delete",
            "cleanup_operation": _operation_projection(
                deletes[0],
                authority="entity_delete_route",
                compensates_operation_ref=create_ref,
            ),
            "candidate_operation_ids": [
                _text(deletes[0].get("id") or deletes[0].get("operation_id"))
            ],
            "source_order_selection_allowed": False,
        }
    if len(deletes) > 1:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "UNRESOLVED",
            "reason_code": "CLEANUP_DELETE_ROUTE_AMBIGUOUS",
            "create_operation_ref": create_ref,
            "cleanup_operation": {},
            "candidate_operation_ids": [
                _text(row.get("id") or row.get("operation_id"))
                for row in deletes
            ],
            "source_order_selection_allowed": False,
        }

    return {
        "schema_version": SCHEMA_VERSION,
        "status": "UNRESOLVED",
        "reason_code": "CLEANUP_OPERATION_MISSING",
        "create_operation_ref": create_ref,
        "cleanup_operation": {},
        "candidate_operation_ids": [],
        "source_order_selection_allowed": False,
    }


__all__ = [
    "SCHEMA_VERSION",
    "resolve_cleanup_operation",
]
