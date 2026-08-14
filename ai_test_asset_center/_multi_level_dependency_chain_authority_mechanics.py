"""Multi-level dependency facade with explicit reference/create/actor authority.

The recursive planning mechanics live in ``_multi_level_dependency_chain_mechanics``.
This facade preserves the latest primary-key-first identity selection while restoring
three fail-closed boundaries that concurrent replay must not erase:

* nested request fields resolve referenced entities only through the shared
  BodyReferenceAuthority; ``billingAddressId``/``addressId`` spelling is not entity
  identity;
* every dependency entity has exactly one source-backed create operation; and
* that create operation has exactly one permitted fixture actor after caller
  restrictions are applied.

The original mechanics still own cycle/depth, identity-output, cleanup, readback and
leaves-first DAG construction. No parallel dependency planner is introduced.
"""
from __future__ import annotations

from typing import Any

from . import _multi_level_dependency_chain_mechanics as _core
from .body_reference_authority import resolve_body_reference
from .money_precondition_chain import _create_operation_candidates_for_entity

for _name in dir(_core):
    if not _name.startswith("__") and not _name.startswith("_original_"):
        globals()[_name] = getattr(_core, _name)

_original_plan_level = _core._plan_level

REASON_CREATE_AMBIGUOUS = "MULTI_LEVEL_DEPENDENCY_CREATE_AMBIGUOUS"
REASON_ACTOR_AMBIGUOUS = "MULTI_LEVEL_DEPENDENCY_ACTOR_AMBIGUOUS"


def __getattr__(name: str) -> Any:
    return getattr(_core, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_core)))


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


def _source_declared_subject_pairs(
    example: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> list[tuple[str, str]]:
    """Resolve nested dependencies only from explicit target-bearing metadata."""

    operation_ref = _text(_dict(behavior_ir).get("_body_reference_operation_ref"))
    operation = _dict(_operation_index(behavior_ir).get(operation_ref))
    if not operation:
        return []
    if _core._tokenized_request_example(operation) != _dict(example):
        return []

    resolved: list[tuple[str, str]] = []
    for field in example:
        if not isinstance(field, str) or not _text(field):
            continue
        receipt = resolve_body_reference(
            operation,
            field,
            behavior_ir=behavior_ir,
        )
        entity_ref = _text(receipt.get("target_entity_ref"))
        if _text(receipt.get("status")) == "RESOLVED" and entity_ref:
            resolved.append((entity_ref, _text(field)))
    return list(dict.fromkeys(resolved))


def _resolve_create_operation_candidates(
    behavior_ir: dict[str, Any],
    entity: dict[str, Any],
) -> list[dict[str, Any]]:
    """Collect all source-backed creates without reference-field/path guessing."""

    candidates: dict[str, dict[str, Any]] = {}
    authorities: dict[str, set[str]] = {}

    def _add(operation: dict[str, Any], authority: str) -> None:
        operation_ref = _text(operation.get("id") or operation.get("operation_id"))
        if not operation_ref:
            return
        if _text(operation.get("method")).upper() != "POST":
            return
        if not _core._tokenized_request_example(operation):
            return
        candidates.setdefault(operation_ref, operation)
        authorities.setdefault(operation_ref, set()).add(authority)

    for operation in _create_operation_candidates_for_entity(behavior_ir, entity):
        _add(_dict(operation), "entity_collection")

    entity_ref = _text(entity.get("id"))
    operations = _operation_index(behavior_ir)
    for raw in _list(_dict(behavior_ir).get("relations")):
        relation = _dict(raw)
        if (
            _text(relation.get("relation_type")) != "produces"
            or _text(relation.get("to_ref")) != entity_ref
            or not _list(relation.get("source_refs"))
            or _text(relation.get("status")) in {"conflicting", "unsupported"}
        ):
            continue
        operation_ref = _text(
            relation.get("operation_ref") or relation.get("from_ref")
        )
        operation = _dict(operations.get(operation_ref))
        if operation:
            _add(operation, "explicit_produces_relation")

    rows: list[dict[str, Any]] = []
    for operation_ref in sorted(candidates):
        row = dict(candidates[operation_ref])
        row["_create_authorities"] = sorted(authorities.get(operation_ref, set()))
        rows.append(row)
    return rows


def _resolve_create_operation(
    behavior_ir: dict[str, Any],
    entity: dict[str, Any],
    reference_field: str,
) -> dict[str, Any]:
    candidates = _resolve_create_operation_candidates(behavior_ir, entity)
    return dict(candidates[0]) if len(candidates) == 1 else {}


def _create_actor_authority(
    *,
    create_operation: dict[str, Any],
    behavior_ir: dict[str, Any],
    actor_refs: list[str],
) -> tuple[str, str, list[str]]:
    """Return one operation-permitted actor; caller order is never authority."""

    actors = {
        _text(row.get("id") or row.get("actor_id")): row
        for row in _list(_dict(behavior_ir).get("actors"))
        if isinstance(row, dict)
        and _text(row.get("id") or row.get("actor_id"))
    }
    declared = list(
        dict.fromkeys(
            actor_id
            for actor_id in _core._declared_fixture_actor_refs(
                create_operation,
                behavior_ir=behavior_ir,
            )
            if actor_id in actors
        )
    )
    caller = {
        _text(actor_id)
        for actor_id in actor_refs
        if _text(actor_id) and _text(actor_id) in actors
    }
    eligible = [
        actor_id for actor_id in declared if not caller or actor_id in caller
    ]
    if len(eligible) == 1:
        return eligible[0], "operation_permits_unique", eligible
    if len(eligible) > 1:
        return "", "operation_permits_ambiguous", eligible
    return "", "operation_permits_missing", []


def _identity_is_ready_for_create_gate(entity: dict[str, Any]) -> bool:
    """Preserve mechanics first-loss ordering for missing/ambiguous identities."""

    fields = _core._declared_entity_identity_fields(entity)
    if not fields:
        return False
    if len(fields) == 1:
        return True
    structural = [
        field
        for field in fields
        if "".join(ch for ch in field.lower() if ch.isalnum())
        in {"id", "uuid", "pk", "key", "uid", "guid"}
    ]
    return bool(structural)


def _plan_level(
    *,
    behavior_ir: dict[str, Any],
    entity_id: str,
    reference_fields: list[str],
    actor_refs: list[str],
    depth: int,
    visited: list[str],
    planned_entities: set[str],
    steps_out: list[dict[str, Any]],
    unresolved_nested: list[dict[str, Any]],
    environment_type: str = "",
) -> dict[str, Any]:
    """Apply target/create/actor gates, then delegate DAG mechanics unchanged."""

    entity = _core._entity_by_id(
        _list(_dict(behavior_ir).get("entities")),
        _text(entity_id),
    )
    if (
        not entity
        or not _identity_is_ready_for_create_gate(entity)
        or _text(entity_id) in visited
        or int(depth or 0) > int(_core.MAX_DEPENDENCY_DEPTH)
    ):
        return _original_plan_level(
            behavior_ir=behavior_ir,
            entity_id=entity_id,
            reference_fields=reference_fields,
            actor_refs=actor_refs,
            depth=depth,
            visited=visited,
            planned_entities=planned_entities,
            steps_out=steps_out,
            unresolved_nested=unresolved_nested,
            environment_type=environment_type,
        )

    candidates = _resolve_create_operation_candidates(behavior_ir, entity)
    primary_field = _text(reference_fields[0] if reference_fields else "")
    if len(candidates) > 1:
        return {
            "status": "blocked",
            "reason_code": REASON_CREATE_AMBIGUOUS,
            "detail": {
                "entity_ref": _text(entity_id),
                "reference_field": primary_field,
                "candidate_operation_ids": [
                    _text(row.get("id") or row.get("operation_id"))
                    for row in candidates
                ],
                "candidate_authorities": {
                    _text(row.get("id") or row.get("operation_id")): list(
                        _list(row.get("_create_authorities"))
                    )
                    for row in candidates
                },
                "source_order_selection_allowed": False,
            },
        }

    governed_ir = dict(_dict(behavior_ir))
    governed_actor_refs = list(actor_refs)
    if len(candidates) == 1:
        create_operation = candidates[0]
        actor_id, actor_authority, eligible = _create_actor_authority(
            create_operation=create_operation,
            behavior_ir=behavior_ir,
            actor_refs=actor_refs,
        )
        if not actor_id:
            return {
                "status": "blocked",
                "reason_code": (
                    REASON_ACTOR_AMBIGUOUS
                    if actor_authority == "operation_permits_ambiguous"
                    else _core.REASON_NO_ACTOR
                ),
                "detail": {
                    "entity_ref": _text(entity_id),
                    "reference_field": primary_field,
                    "create_operation_ref": _text(
                        create_operation.get("id")
                        or create_operation.get("operation_id")
                    ),
                    "actor_authority": actor_authority,
                    "eligible_actor_ids": eligible,
                    "source_order_selection_allowed": False,
                },
            }
        governed_actor_refs = [actor_id]
        governed_ir["_body_reference_operation_ref"] = _text(
            create_operation.get("id") or create_operation.get("operation_id")
        )
    else:
        governed_ir.pop("_body_reference_operation_ref", None)

    return _original_plan_level(
        behavior_ir=governed_ir,
        entity_id=entity_id,
        reference_fields=reference_fields,
        actor_refs=governed_actor_refs,
        depth=depth,
        visited=visited,
        planned_entities=planned_entities,
        steps_out=steps_out,
        unresolved_nested=unresolved_nested,
        environment_type=environment_type,
    )


# Mechanics functions resolve these helpers dynamically, including recursive calls.
_core._subject_pairs_from_example = _source_declared_subject_pairs
_core._resolve_create_operation = _resolve_create_operation
_core._plan_level = _plan_level

plan_multi_level_dependency_chain = _core.plan_multi_level_dependency_chain

__all__ = sorted(
    {
        *[
            name
            for name in dir(_core)
            if not name.startswith("__") and not name.startswith("_original_")
        ],
        "REASON_CREATE_AMBIGUOUS",
        "REASON_ACTOR_AMBIGUOUS",
        "_source_declared_subject_pairs",
        "_resolve_create_operation_candidates",
        "_resolve_create_operation",
        "_create_actor_authority",
        "_plan_level",
        "plan_multi_level_dependency_chain",
    }
)
