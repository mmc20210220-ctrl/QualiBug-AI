"""Unique cleanup-operation authority for automatically created fixtures.

A destructive compensation route cannot be chosen by source order, and a
source ``compensates`` relation alone does not prove that the current executor
can safely invoke its operation.  Today automatic fixture cleanup has a formal
request materialization contract only for identity-bound DELETE: the target is
bound into exactly one path placeholder from the created resource.

Authority tiers are therefore:

1. one source-backed, non-conflicting ``compensates`` relation whose compensator
   is an identity-bound write (DELETE, or a POST/PUT/PATCH state-transition
   action) with exactly one path identity placeholder; otherwise
2. one identity-bound DELETE on the same source collection.

The executor materializes identity-bound state-transition compensators the same
way it does DELETE: ``runtime_cleanup_paths`` binds the single path identity
placeholder from the accepted create response (exactly one candidate required),
the body is built from a declared template with unresolved server-assigned
fields dropped and required unresolved fields failing visibly, and restoration
is proven by ``_cleanup_compensates_created_resource``. The executor must never
reuse the original business request body, drop required fields silently, or
infer a cleanup method/path to make them executable. Identity-free compensators
(with zero path identity placeholders) remain protocol gaps.
"""
from __future__ import annotations

from typing import Any

from .real_id_resolver import (
    collection_path,
    infer_path_params,
    normalize_path_placeholders,
)

SCHEMA_VERSION = "qualibug.cleanup-operation-authority.v2"
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
    method = _text(operation.get("method")).upper()
    return {
        "operation_ref": _text(operation.get("id") or operation.get("operation_id")),
        "method": method,
        "path": normalize_path_placeholders(
            _text(operation.get("path") or operation.get("raw_path"))
        ),
        "source": authority,
        "compensates_operation_ref": _text(compensates_operation_ref),
        "request_materialization_authority": (
            "identity_bound_path_delete"
            if method == "DELETE"
            else "identity_bound_path_state_transition"
        ),
    }


def _source_compensator_candidates(
    create_operation: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return every source-backed compensator operation, executable or not."""

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


def _identity_bound_path_write(
    operation: dict[str, Any],
    methods: frozenset[str],
) -> bool:
    if _text(operation.get("method")).upper() not in methods:
        return False
    path = normalize_path_placeholders(
        _text(operation.get("path") or operation.get("raw_path"))
    )
    return bool(path.startswith("/") and len(infer_path_params(path)) == 1)


def _identity_bound_delete(operation: dict[str, Any]) -> bool:
    return _identity_bound_path_write(operation, frozenset({"DELETE"}))


def _identity_bound_state_transition(operation: dict[str, Any]) -> bool:
    return _identity_bound_path_write(operation, _WRITE_METHODS - frozenset({"DELETE"}))


def _identity_bound_compensator(operation: dict[str, Any]) -> bool:
    """True when the executor can materialize this compensator route.

    Both identity-bound DELETE and identity-bound POST/PUT/PATCH state
    transitions share one binding contract: exactly one path identity
    placeholder resolved from the accepted create response.
    """
    return _identity_bound_delete(operation) or _identity_bound_state_transition(operation)


def _compensator_authority(operation: dict[str, Any]) -> str:
    return (
        "explicit_identity_delete_compensator"
        if _identity_bound_delete(operation)
        else "explicit_identity_state_transition_compensator"
    )


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
        if not _identity_bound_delete(operation):
            continue
        path = normalize_path_placeholders(
            _text(operation.get("path") or operation.get("raw_path"))
        )
        if normalize_path_placeholders(collection_path(path)).rstrip("/") != create_path:
            continue
        candidates[operation_ref] = operation
    return [candidates[key] for key in sorted(candidates)]


def _candidate_diagnostic(operation: dict[str, Any]) -> dict[str, Any]:
    path = normalize_path_placeholders(
        _text(operation.get("path") or operation.get("raw_path"))
    )
    return {
        "operation_ref": _text(operation.get("id") or operation.get("operation_id")),
        "method": _text(operation.get("method")).upper(),
        "path": path,
        "path_identity_count": len(infer_path_params(path)),
        "executor_protocol_supported": _identity_bound_compensator(operation),
    }


def resolve_cleanup_operation(
    create_operation: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    """Resolve one currently executable cleanup route or a named protocol gap."""

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
            "unsupported_compensators": [],
            "source_order_selection_allowed": False,
        }

    explicit_all = _source_compensator_candidates(create_operation, behavior_ir)
    explicit = [row for row in explicit_all if _identity_bound_compensator(row)]
    unsupported = [row for row in explicit_all if not _identity_bound_compensator(row)]

    if len(explicit) == 1:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "RESOLVED",
            "reason_code": "",
            "create_operation_ref": create_ref,
            "authority": "source_compensates_relation",
            "cleanup_operation": _operation_projection(
                explicit[0],
                authority=_compensator_authority(explicit[0]),
                compensates_operation_ref=create_ref,
            ),
            "candidate_operation_ids": [
                _text(explicit[0].get("id") or explicit[0].get("operation_id"))
            ],
            "unsupported_compensators": [
                _candidate_diagnostic(row) for row in unsupported
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
            "unsupported_compensators": [
                _candidate_diagnostic(row) for row in unsupported
            ],
            "source_order_selection_allowed": False,
        }

    # An explicit relation that points at a protocol the current cleanup executor
    # cannot materialize is retained as a diagnostic, but a separately declared
    # same-collection identity DELETE may still be a safe cleanup route.
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
            "unsupported_compensators": [
                _candidate_diagnostic(row) for row in unsupported
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
            "unsupported_compensators": [
                _candidate_diagnostic(row) for row in unsupported
            ],
            "source_order_selection_allowed": False,
        }

    if unsupported:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "UNRESOLVED",
            "reason_code": "CLEANUP_COMPENSATOR_PROTOCOL_UNPROVEN",
            "create_operation_ref": create_ref,
            "cleanup_operation": {},
            "candidate_operation_ids": [
                _text(row.get("id") or row.get("operation_id"))
                for row in unsupported
            ],
            "unsupported_compensators": [
                _candidate_diagnostic(row) for row in unsupported
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
        "unsupported_compensators": [],
        "source_order_selection_allowed": False,
    }


__all__ = [
    "SCHEMA_VERSION",
    "resolve_cleanup_operation",
]