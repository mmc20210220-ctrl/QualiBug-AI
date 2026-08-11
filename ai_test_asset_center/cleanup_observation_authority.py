"""Post-cleanup observation authority for automatic HTTP fixture cleanup.

Successful compensation transport is not proof that the environment was
restored.  Cleanup equivalence needs a source-declared GET/HEAD observation that
can inspect either the exact deleted resource or its collection.

Authority tiers:
1. exactly one GET/HEAD whose path template is exactly the identity-bound cleanup
   path template; otherwise
2. exactly one placeholder-free GET/HEAD for the create collection.

Multiple candidates in the winning tier are AMBIGUOUS.  No path is fabricated
from the DELETE route and no observer is selected by source order.
"""
from __future__ import annotations

from typing import Any

from .real_id_resolver import infer_path_params, normalize_path_placeholders

SCHEMA_VERSION = "qualibug.cleanup-observation-authority.v1"


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


def _projection(operation: dict[str, Any], *, authority: str) -> dict[str, str]:
    return {
        "operation_ref": _text(operation.get("id") or operation.get("operation_id")),
        "method": _text(operation.get("method")).upper(),
        "path": normalize_path_placeholders(
            _text(operation.get("path") or operation.get("raw_path"))
        ),
        "authority": authority,
    }


def _read_candidates(behavior_ir: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [
        operation
        for operation in _operation_index(behavior_ir).values()
        if _text(operation.get("method")).upper() in {"GET", "HEAD"}
        and normalize_path_placeholders(
            _text(operation.get("path") or operation.get("raw_path"))
        ).startswith("/")
    ]
    rows.sort(
        key=lambda row: (
            _text(row.get("id") or row.get("operation_id")),
            _text(row.get("method")),
            _text(row.get("path") or row.get("raw_path")),
        )
    )
    return rows


def resolve_cleanup_observation(
    create_operation: dict[str, Any],
    cleanup_operation: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    create_ref = _text(
        create_operation.get("id") or create_operation.get("operation_id")
    )
    cleanup_ref = _text(
        cleanup_operation.get("id") or cleanup_operation.get("operation_ref")
        or cleanup_operation.get("operation_id")
    )
    create_path = normalize_path_placeholders(
        _text(create_operation.get("path") or create_operation.get("raw_path"))
    ).rstrip("/")
    cleanup_path = normalize_path_placeholders(
        _text(cleanup_operation.get("path") or cleanup_operation.get("raw_path"))
    )

    base = {
        "schema_version": SCHEMA_VERSION,
        "create_operation_ref": create_ref,
        "cleanup_operation_ref": cleanup_ref,
        "observation_operation": {},
        "candidate_operation_ids": [],
        "source_order_selection_allowed": False,
    }
    if (
        not create_ref
        or not cleanup_ref
        or not create_path.startswith("/")
        or not cleanup_path.startswith("/")
    ):
        return {
            **base,
            "status": "UNRESOLVED",
            "reason_code": "CLEANUP_OBSERVATION_OPERATION_IDENTITY_MISSING",
        }

    reads = _read_candidates(behavior_ir)
    exact = [
        row
        for row in reads
        if normalize_path_placeholders(
            _text(row.get("path") or row.get("raw_path"))
        ) == cleanup_path
    ]
    if len(exact) == 1:
        return {
            **base,
            "status": "RESOLVED",
            "reason_code": "",
            "authority": "exact_cleanup_identity_read",
            "observation_operation": _projection(
                exact[0], authority="exact_cleanup_identity_read"
            ),
            "candidate_operation_ids": [
                _text(exact[0].get("id") or exact[0].get("operation_id"))
            ],
        }
    if len(exact) > 1:
        return {
            **base,
            "status": "UNRESOLVED",
            "reason_code": "CLEANUP_OBSERVATION_IDENTITY_READ_AMBIGUOUS",
            "candidate_operation_ids": [
                _text(row.get("id") or row.get("operation_id")) for row in exact
            ],
        }

    # Collection readback can prove that the created identity disappeared from
    # the same collection.  It must be a real source read and placeholder-free.
    collection = [
        row
        for row in reads
        if not infer_path_params(
            normalize_path_placeholders(
                _text(row.get("path") or row.get("raw_path"))
            )
        )
        and normalize_path_placeholders(
            _text(row.get("path") or row.get("raw_path"))
        ).rstrip("/")
        == create_path
    ]
    if len(collection) == 1:
        return {
            **base,
            "status": "RESOLVED",
            "reason_code": "",
            "authority": "create_collection_read",
            "observation_operation": _projection(
                collection[0], authority="create_collection_read"
            ),
            "candidate_operation_ids": [
                _text(collection[0].get("id") or collection[0].get("operation_id"))
            ],
        }
    if len(collection) > 1:
        return {
            **base,
            "status": "UNRESOLVED",
            "reason_code": "CLEANUP_OBSERVATION_COLLECTION_READ_AMBIGUOUS",
            "candidate_operation_ids": [
                _text(row.get("id") or row.get("operation_id"))
                for row in collection
            ],
        }

    return {
        **base,
        "status": "UNRESOLVED",
        "reason_code": "CLEANUP_OBSERVATION_OPERATION_MISSING",
    }


__all__ = ["SCHEMA_VERSION", "resolve_cleanup_observation"]
