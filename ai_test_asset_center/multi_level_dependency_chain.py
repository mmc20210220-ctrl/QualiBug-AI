"""Multi-level dependency facade with unique create/actor authority.

The recursive dependency DAG mechanics live in
``_multi_level_dependency_chain_mechanics``. A dependency create may have
several structurally plausible POSTs or several permitted fixture actors; source
order is not enough to decide either identity.

This facade collects every source-backed create candidate (entity collection,
reference-field collection, explicit ``produces`` relation), requires exactly
one candidate operation, then requires exactly one operation-specific permitted
actor after applying any caller actor restriction. Each recursive level passes
through the same gate, so a deep chain cannot silently choose the first create
or first actor.
"""
from __future__ import annotations

from typing import Any

from . import _multi_level_dependency_chain_mechanics as _core
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


def _resolve_create_operation_candidates(
    behavior_ir: dict[str, Any],
    entity: dict[str, Any],
    reference_field: str,
) -> list[dict[str, Any]]:
    """Collect all source-backed create candidates with no first-match shortcut."""

    from .experiment_runtime_support import normalize_path_placeholders
    from .real_id_resolver import body_field_collection_paths

    operations = _operation_index(behavior_ir)
    candidates: dict[str, dict[str, Any]] = {}
    authorities: dict[str, set[str]] = {}

    def _add(operation: dict[str, Any], authority: str) -> None:
        operation_ref = _text(operation.get("id") or operation.get("operation_id"))
        if not operation_ref:
            return
        if _text(operation.get("method")).upper() != "POST":
            return
        path = normalize_path_placeholders(
            _text(operation.get("path") or operation.get("raw_path"))
        )
        if (
            not path.startswith("/")
            or "{" in path
            or ":" in path
            or not _core._tokenized_request_example(operation)
        ):
            return
        candidates.setdefault(operation_ref, operation)
        authorities.setdefault(operation_ref, set()).add(authority)

    for operation in _create_operation_candidates_for_entity(behavior_ir, entity):
        _add(_dict(operation), "entity_collection")

    for candidate_path in body_field_collection_paths(_text(reference_field)):
        normalized = normalize_path_placeholders(candidate_path).rstrip("/")
        if not normalized or "{" in normalized or ":" in normalized:
            continue
        for operation in operations.values():
            path = normalize_path_placeholders(
                _text(operation.get("path") or operation.get("raw_path"))
            ).rstrip("/")
            if path == normalized:
                _add(operation, "reference_field_collection")

    entity_id = _text(entity.get("id"))
    for raw in _list(_dict(behavior_ir).get("relations")):
        relation = _dict(raw)
        if (
            _text(relation.get("relation_type")) != "produces"
            or _text(relation.get("to_ref")) != entity_id
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
    candidates = _resolve_create_operation_candidates(
        behavior_ir,
        entity,
        reference_field,
    )
    return dict(candidates[0]) if len(candidates) == 1 else {}


def _create_actor_authority(
    *,
    create_operation: dict[str, Any],
    behavior_ir: dict[str, Any],
    actor_refs: list[str],
) -> tuple[str, str, list[str]]:
    """Return one operation-permitted fixture actor or an explicit ambiguity."""

    actors = {
        _text(row.get("id") or row.get("actor_id")): row
        for row in _list(_dict(behavior_ir).get("actors"))
        if isinstance(row, dict)
        and _text(row.get("id") or row.get("actor_id"))
    }
    declared = [
        actor_id
        for actor_id in _core._declared_fixture_actor_refs(
            create_operation,
            behavior_ir=behavior_ir,
        )
        if actor_id in actors
    ]
    declared = list(dict.fromkeys(declared))
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
) -> dict[str, Any]:
    """Gate one recursive level before the historical planner may choose anything."""

    entity = _core._entity_by_id(
        _list(_dict(behavior_ir).get("entities")),
        _text(entity_id),
    )
    primary_field = _text(reference_fields[0] if reference_fields else "")
    if entity:
        candidates = _resolve_create_operation_candidates(
            behavior_ir,
            entity,
            primary_field,
        )
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
        if len(candidates) == 1:
            actor_id, actor_authority, eligible = _create_actor_authority(
                create_operation=candidates[0],
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
                            candidates[0].get("id")
                            or candidates[0].get("operation_id")
                        ),
                        "actor_authority": actor_authority,
                        "eligible_actor_ids": eligible,
                        "source_order_selection_allowed": False,
                    },
                }
            actor_refs = [actor_id]

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
    )


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
        "_resolve_create_operation_candidates",
        "_resolve_create_operation",
        "_create_actor_authority",
        "_plan_level",
        "plan_multi_level_dependency_chain",
    }
)
