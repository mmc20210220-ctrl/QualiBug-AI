"""Outermost runtime-binding target-authority facade.

All accumulated binding / observer / fixture authorities live in
``_runtime_binding_graph_semantic_mechanics``.  This boundary makes body
resource references target-specific: ``x-foreign-key: true`` proves only that a
relation exists, not what entity it targets. Formal runtime binding therefore
requires one explicit target-bearing source declaration and verifies that every
resolver/create operation actually belongs to that target entity.

Field spelling remains diagnostic only. ``addressId`` cannot become an address
resolver unless the source declares the target and the selected GET/POST is
source-bound to that same entity.
"""
from __future__ import annotations

from typing import Any

from . import _runtime_binding_graph_semantic_mechanics as _semantic
from .body_reference_authority import resolve_body_reference

for _name in dir(_semantic):
    if not _name.startswith("__") and not _name.startswith("_original_"):
        globals()[_name] = getattr(_semantic, _name)

_original_build_binding_plan = _semantic.build_binding_plan


def __getattr__(name: str) -> Any:
    return getattr(_semantic, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_semantic)))


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


def _source_bound_operation_entity_refs(
    operation: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> set[str]:
    refs = {
        _text(value)
        for value in [
            *_list(operation.get("entity_refs")),
            operation.get("entity_ref"),
        ]
        if _text(value)
    }
    operation_ref = _text(operation.get("id") or operation.get("operation_id"))
    entity_ids = {
        _text(row.get("id"))
        for row in _list(_dict(behavior_ir).get("entities"))
        if isinstance(row, dict) and _text(row.get("id"))
    }
    for raw in _list(_dict(behavior_ir).get("relations")):
        relation = _dict(raw)
        if (
            not _list(relation.get("source_refs"))
            or _text(relation.get("status")) in {"conflicting", "unsupported"}
        ):
            continue
        relation_refs = {
            _text(relation.get("operation_ref")),
            _text(relation.get("from_ref")),
            _text(relation.get("to_ref")),
            _text(relation.get("entity_ref")),
        }
        if operation_ref in relation_refs:
            refs.update(ref for ref in relation_refs if ref in entity_ids)
    return refs


def _operation_matches_target_entity(
    operation_ref: str,
    target_entity_ref: str,
    behavior_ir: dict[str, Any],
) -> bool:
    operation = _dict(_operation_index(behavior_ir).get(_text(operation_ref)))
    if not operation or not _text(target_entity_ref):
        return False
    return _text(target_entity_ref) in _source_bound_operation_entity_refs(
        operation,
        behavior_ir,
    )


def _reference_rows_for_binding(
    operation: dict[str, Any],
    body_paths: list[str],
    behavior_ir: dict[str, Any],
) -> tuple[list[dict[str, Any]], str]:
    references: list[dict[str, Any]] = []
    for body_path in list(dict.fromkeys(_text(path) for path in body_paths if _text(path))):
        receipt = resolve_body_reference(
            operation,
            body_path,
            behavior_ir=behavior_ir,
        )
        references.append(receipt)
        if _text(receipt.get("status")) != "RESOLVED":
            return references, _text(receipt.get("reason_code")) or "BODY_REFERENCE_UNRESOLVED"
    targets = {
        _text(receipt.get("target_entity_ref"))
        for receipt in references
        if _text(receipt.get("target_entity_ref"))
    }
    if len(targets) != 1:
        return references, "BODY_REFERENCE_TARGET_AMBIGUOUS"
    return references, ""


def _binding_target_operations_match(
    row: dict[str, Any],
    *,
    target_entity_ref: str,
    behavior_ir: dict[str, Any],
) -> tuple[bool, str]:
    source_priority = _text(row.get("source_priority"))
    resolver_refs = [
        _text(_dict(raw).get("operation_ref") or _dict(raw).get("operation_id"))
        for raw in _list(row.get("resolver_operations"))
        if _text(_dict(raw).get("operation_ref") or _dict(raw).get("operation_id"))
    ]
    fixture_ref = _text(
        _dict(row.get("fixture_setup")).get("operation_ref")
        or _dict(row.get("fixture_setup")).get("create_operation_ref")
    )

    if source_priority == "same_actor_list_read":
        if not resolver_refs:
            return False, "BODY_REFERENCE_RESOLVER_MISSING"
        mismatched = [
            ref
            for ref in resolver_refs
            if not _operation_matches_target_entity(ref, target_entity_ref, behavior_ir)
        ]
        if mismatched:
            return False, "BODY_REFERENCE_RESOLVER_TARGET_MISMATCH"
        return True, ""

    if source_priority == "fixture_create_only":
        if not fixture_ref:
            return False, "BODY_REFERENCE_FIXTURE_OPERATION_MISSING"
        if not _operation_matches_target_entity(
            fixture_ref,
            target_entity_ref,
            behavior_ir,
        ):
            return False, "BODY_REFERENCE_FIXTURE_TARGET_MISMATCH"
        return True, ""

    return True, ""


def _govern_body_reference_targets(
    plan: list[dict[str, Any]],
    *,
    operation: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> list[dict[str, Any]]:
    path_placeholders = set(
        _semantic.extract_placeholders(
            operation.get("path"),
            operation.get("operation_id"),
            *[str(value) for value in _list(operation.get("parameters"))],
        )
    )
    ownership_params = set(
        _semantic._ownership_params_declared_on_operation(operation)
    )
    governed: list[dict[str, Any]] = []
    for raw in plan:
        row = dict(raw) if isinstance(raw, dict) else raw
        if not isinstance(row, dict):
            governed.append(row)
            continue
        target = _text(row.get("target"))
        source_priority = _text(row.get("source_priority"))
        if (
            not _semantic._identity_shaped_target(target)
            or target in path_placeholders
            or target in ownership_params
            or source_priority
            in {
                "ownership_identity_param",
                "actor_credential_secret",
                "sequential_output_binding",
                "runtime_actor_secret_ref",
                "body_identity_relation_unresolved",
            }
        ):
            governed.append(row)
            continue
        if source_priority not in {"same_actor_list_read", "fixture_create_only"}:
            governed.append(row)
            continue

        body_paths = [
            _text(value)
            for value in _list(row.get("body_template_paths"))
            if _text(value)
        ]
        references, problem = _reference_rows_for_binding(
            operation,
            body_paths,
            behavior_ir,
        )
        if not problem:
            target_entity_ref = _text(references[0].get("target_entity_ref"))
            matches, problem = _binding_target_operations_match(
                row,
                target_entity_ref=target_entity_ref,
                behavior_ir=behavior_ir,
            )
            if matches:
                row["body_reference_target_entity_ref"] = target_entity_ref
                row["body_reference_authority_receipts"] = references
                governed.append(row)
                continue

        row.update(
            {
                "status": "blocked",
                "source_priority": "body_reference_target_unresolved",
                "resolver_operations": [],
                "value_fingerprint": "",
                "blocked_reason": problem or "BODY_REFERENCE_TARGET_UNRESOLVED",
                "body_reference_authority_receipts": references,
            }
        )
        row.pop("fixture_setup", None)
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
    return _govern_body_reference_targets(
        plan,
        operation=operation,
        behavior_ir=ir,
    )


_semantic.build_binding_plan = build_binding_plan
_semantic._authority.build_binding_plan = build_binding_plan
_semantic._authority._core.build_binding_plan = build_binding_plan

__all__ = sorted(
    {
        *[
            name
            for name in dir(_semantic)
            if not name.startswith("__") and not name.startswith("_original_")
        ],
        "build_binding_plan",
        "_govern_body_reference_targets",
        "_binding_target_operations_match",
        "resolve_body_reference",
    }
)
