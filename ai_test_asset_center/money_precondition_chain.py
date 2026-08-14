"""Money precondition facade with source-declared subject identity.

The established money-family planning mechanics live in
``_money_precondition_chain_mechanics``. A request field name such as
``orderId`` is not subject authority. The field must carry an explicit target
that the shared BodyReferenceAuthority resolves to one Behavior IR entity.

Create operations remain uniqueness-gated: an ambiguous subject is skipped when
another explicitly targeted subject has one unique create; if no unique subject
exists, the ambiguity is surfaced instead of selecting source order.
"""
from __future__ import annotations

from typing import Any

from . import _money_precondition_chain_mechanics as _core
from .body_reference_authority import resolve_body_reference

for _name in dir(_core):
    if not _name.startswith("__") and not _name.startswith("_original_"):
        globals()[_name] = getattr(_core, _name)

_original_plan_money_family_precondition = _core.plan_money_family_precondition

REASON_CREATE_AMBIGUOUS = "MONEY_PRECONDITION_CREATE_OPERATION_AMBIGUOUS"


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
    operation: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> list[tuple[str, str]]:
    """Resolve subject fields only through explicit target-bearing metadata."""

    example = _core._request_example(operation)
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


def _subject_entities_from_example(
    example: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> list[tuple[str, str]]:
    """Mechanics hook using the current operation identity carried by the facade."""

    operation_ref = _text(_dict(behavior_ir).get("_body_reference_operation_ref"))
    operation = _dict(_operation_index(behavior_ir).get(operation_ref))
    if not operation:
        return []
    # The historical caller already passes this operation's source example. A
    # mismatch is fail-closed instead of searching other operations by payload.
    if _core._request_example(operation) != _dict(example):
        return []
    return _source_declared_subject_pairs(operation, behavior_ir)


def _create_operation_candidates_for_entity(
    behavior_ir: dict[str, Any],
    entity: dict[str, Any],
) -> list[dict[str, Any]]:
    entity_name = _text(entity.get("name"))
    if not entity_name:
        return []
    from .experiment_runtime_support import normalize_path_placeholders

    collection = ""
    for key in ("collection_path", "http_collection", "collection"):
        value = _text(entity.get(key))
        if value:
            collection = normalize_path_placeholders(value).rstrip("/")
            break

    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in _list(_dict(behavior_ir).get("operations")):
        operation = _dict(raw)
        operation_ref = _text(operation.get("id") or operation.get("operation_id"))
        if not operation_ref or operation_ref in seen:
            continue
        if _text(operation.get("method")).upper() != "POST":
            continue
        path = normalize_path_placeholders(
            _text(operation.get("path") or operation.get("raw_path"))
        ).rstrip("/")
        if not path.startswith("/") or "{" in path or ":" in path:
            continue
        if collection:
            if path != collection:
                continue
        else:
            segments = [segment for segment in path.strip("/").split("/") if segment]
            if not segments or _text(segments[-1]).lower() not in {
                entity_name.lower(),
                entity_name.lower() + "s",
            }:
                continue
        if not _core._request_example(operation):
            continue
        seen.add(operation_ref)
        candidates.append(operation)

    candidates.sort(
        key=lambda row: (
            _text(row.get("id") or row.get("operation_id")),
            _text(row.get("path") or row.get("raw_path")),
        )
    )
    return candidates


def _create_operation_for_entity(
    behavior_ir: dict[str, Any],
    entity: dict[str, Any],
) -> dict[str, Any]:
    candidates = _create_operation_candidates_for_entity(behavior_ir, entity)
    return dict(candidates[0]) if len(candidates) == 1 else {}


def _ambiguous_subjects(
    *,
    behavior_ir: dict[str, Any],
    operation: dict[str, Any],
) -> list[dict[str, Any]]:
    pairs = _source_declared_subject_pairs(operation, behavior_ir)
    entities = {
        _text(row.get("id")): row
        for row in _list(_dict(behavior_ir).get("entities"))
        if isinstance(row, dict) and _text(row.get("id"))
    }
    ambiguous: list[dict[str, Any]] = []
    unique_exists = False
    for entity_id, reference_field in pairs:
        candidates = _create_operation_candidates_for_entity(
            behavior_ir,
            _dict(entities.get(entity_id)),
        )
        if len(candidates) == 1:
            unique_exists = True
            continue
        if len(candidates) > 1:
            ambiguous.append(
                {
                    "entity_ref": _text(entity_id),
                    "reference_field": _text(reference_field),
                    "candidate_operation_ids": [
                        _text(row.get("id") or row.get("operation_id"))
                        for row in candidates
                    ],
                }
            )
    return [] if unique_exists else ambiguous


def plan_money_family_precondition(
    *,
    behavior_ir: dict[str, Any],
    operation: dict[str, Any],
    actor_refs: list[str],
    property_spec: dict[str, Any] | None = None,
    family: str = "",
    environment_type: str = "",
) -> dict[str, Any]:
    ambiguous = _ambiguous_subjects(
        behavior_ir=behavior_ir,
        operation=operation,
    )
    if ambiguous:
        return {
            "status": _core.BLOCKED,
            "reason_code": REASON_CREATE_AMBIGUOUS,
            "steps": [],
            "identity_binding_target": _text(ambiguous[0].get("reference_field")),
            "entity_ref": _text(ambiguous[0].get("entity_ref")),
            "create_operation_ref": "",
            "ambiguous_subjects": ambiguous,
            "source_order_selection_allowed": False,
        }

    governed_ir = dict(_dict(behavior_ir))
    governed_ir["_body_reference_operation_ref"] = _text(
        operation.get("id") or operation.get("operation_id")
    )
    return _original_plan_money_family_precondition(
        behavior_ir=governed_ir,
        operation=operation,
        actor_refs=actor_refs,
        property_spec=property_spec,
        family=family,
        environment_type=environment_type,
    )


_core._create_operation_for_entity = _create_operation_for_entity
_core._subject_entities_from_example = _subject_entities_from_example

__all__ = sorted(
    {
        *[
            name
            for name in dir(_core)
            if not name.startswith("__") and not name.startswith("_original_")
        ],
        "REASON_CREATE_AMBIGUOUS",
        "_source_declared_subject_pairs",
        "_create_operation_candidates_for_entity",
        "_create_operation_for_entity",
        "plan_money_family_precondition",
    }
)
