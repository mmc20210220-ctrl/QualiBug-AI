"""Outermost runtime-binding facade with unique cleanup authority.

Target-aware binding mechanics live in ``_runtime_binding_graph_target_mechanics``.
This final boundary ensures automatically created fixtures never carry a list of
cleanup candidates that a later executor could index by source order.

Cleanup resolution is delegated to the shared CleanupOperationAuthority. A
source-backed compensator relation wins when unique; otherwise exactly one
identity-bound same-collection DELETE is required. Ambiguous/missing cleanup
removes the fixture fallback, or blocks a fixture-only binding.
"""
from __future__ import annotations

from typing import Any

from . import _runtime_binding_graph_target_mechanics as _target
from .cleanup_operation_authority import resolve_cleanup_operation
from .real_id_resolver import normalize_path_placeholders

for _name in dir(_target):
    if not _name.startswith("__") and not _name.startswith("_original_"):
        globals()[_name] = getattr(_target, _name)

_original_build_binding_plan = _target.build_binding_plan


def __getattr__(name: str) -> Any:
    return getattr(_target, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_target)))


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


def _declared_cleanup_operations(
    create_path: str,
    *,
    behavior_ir: dict[str, Any],
    max_candidates: int = 4,
) -> list[dict[str, str]]:
    """Compatibility surface that resolves cleanup only for one exact create op."""

    normalized = normalize_path_placeholders(_text(create_path)).rstrip("/")
    creates = [
        operation
        for operation in _operation_index(behavior_ir).values()
        if _text(operation.get("method")).upper() == "POST"
        and normalize_path_placeholders(
            _text(operation.get("path") or operation.get("raw_path"))
        ).rstrip("/")
        == normalized
    ]
    if len(creates) != 1:
        return []
    receipt = resolve_cleanup_operation(creates[0], behavior_ir=behavior_ir)
    if _text(receipt.get("status")) != "RESOLVED":
        return []
    cleanup = _dict(receipt.get("cleanup_operation"))
    return [cleanup] if cleanup else []


def _govern_fixture_cleanup_authority(
    plan: list[dict[str, Any]],
    *,
    behavior_ir: dict[str, Any],
) -> list[dict[str, Any]]:
    operations = _operation_index(behavior_ir)
    governed: list[dict[str, Any]] = []
    for raw in plan:
        row = dict(raw) if isinstance(raw, dict) else raw
        if not isinstance(row, dict):
            governed.append(row)
            continue
        setup = dict(_dict(row.get("fixture_setup")))
        if not setup:
            governed.append(row)
            continue

        create_ref = _text(
            setup.get("operation_ref") or setup.get("create_operation_ref")
        )
        create_operation = _dict(operations.get(create_ref))
        receipt = (
            resolve_cleanup_operation(create_operation, behavior_ir=behavior_ir)
            if create_operation
            else {
                "schema_version": "qualibug.cleanup-operation-authority.v1",
                "status": "UNRESOLVED",
                "reason_code": "CLEANUP_CREATE_OPERATION_IDENTITY_MISSING",
                "create_operation_ref": create_ref,
                "cleanup_operation": {},
                "candidate_operation_ids": [],
                "source_order_selection_allowed": False,
            }
        )
        if _text(receipt.get("status")) == "RESOLVED":
            _cleanup_op = _dict(receipt.get("cleanup_operation"))
            # Keep the executable cleanup list minimal (operation identity only);
            # the full authority projection stays in the receipt for governance.
            setup["cleanup_operations"] = [
                {
                    "operation_ref": _text(_cleanup_op.get("operation_ref")),
                    "method": _text(_cleanup_op.get("method")),
                    "path": normalize_path_placeholders(
                        _text(_cleanup_op.get("path"))
                    ),
                }
            ]
            setup["cleanup_operation_authority_receipt"] = dict(receipt)
            row["fixture_setup"] = setup
            governed.append(row)
            continue

        row["fixture_cleanup_authority_receipt"] = dict(receipt)
        row["fixture_setup_unavailable_reason"] = _text(
            receipt.get("reason_code")
        ) or "CLEANUP_OPERATION_UNRESOLVED"
        row.pop("fixture_setup", None)
        if _text(row.get("source_priority")) == "fixture_create_only":
            row.update(
                {
                    "status": "blocked",
                    "source_priority": "fixture_cleanup_authority_unresolved",
                    "blocked_reason": row["fixture_setup_unavailable_reason"],
                    "value_fingerprint": "",
                }
            )
        governed.append(row)
    return governed


def build_binding_plan(
    *,
    operation: dict[str, Any],
    obligation: dict[str, Any],
    actors: list[dict[str, Any]] | None = None,
    available_values: dict[str, dict[str, Any]] | None = None,
    behavior_ir: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    ir = _dict(behavior_ir)
    plan = _original_build_binding_plan(
        operation=operation,
        obligation=obligation,
        actors=actors,
        available_values=available_values,
        behavior_ir=ir,
    )
    return _govern_fixture_cleanup_authority(plan, behavior_ir=ir)


# Patch compatibility call sites that dynamically resolve the old path-only
# cleanup helper. The plan post-processor above remains the final authority.
_target.build_binding_plan = build_binding_plan
_target._declared_cleanup_operations = _declared_cleanup_operations
for _module in (
    getattr(_target, "_semantic", None),
    getattr(getattr(_target, "_semantic", None), "_authority", None),
    getattr(
        getattr(getattr(_target, "_semantic", None), "_authority", None),
        "_core",
        None,
    ),
):
    if _module is not None:
        setattr(_module, "_declared_cleanup_operations", _declared_cleanup_operations)

__all__ = sorted(
    {
        *[
            name
            for name in dir(_target)
            if not name.startswith("__") and not name.startswith("_original_")
        ],
        "build_binding_plan",
        "_declared_cleanup_operations",
        "_govern_fixture_cleanup_authority",
    }
)
