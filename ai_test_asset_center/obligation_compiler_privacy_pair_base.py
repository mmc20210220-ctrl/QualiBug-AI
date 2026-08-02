"""Source-grounded actor pairing for privacy and visibility obligations.

The baseline obligation compiler remains unchanged. This facade replaces
single-actor privacy/visibility obligations with explicit permitted/denied actor
pairs from Behavior IR. Without two distinct runtime actors, a control/treatment
experiment cannot exercise disclosure boundaries and is recorded as a coverage
gap instead of a misleading executable obligation.
"""
from __future__ import annotations

from collections.abc import Callable
import hashlib
from typing import Any

from . import obligation_compiler_base as _base
from .obligation_compiler_base import *  # noqa: F401,F403


_original_compile = _base.compile_obligations_from_behavior_ir
_PAIR_FAMILIES = frozenset({"privacy", "visibility"})


def __getattr__(name: str) -> Any:
    return getattr(_base, name)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _actor_pair_gap(
    *,
    family: str,
    invariant_ref: str,
    operation_ref: str,
    source_refs: list[dict[str, Any]],
) -> dict[str, Any]:
    material = "|".join([
        "BLOCKED_MISSING_ACTOR_PAIR",
        family,
        invariant_ref,
        operation_ref,
    ])
    return {
        "id": "compile_gap_" + hashlib.sha256(
            material.encode("utf-8")
        ).hexdigest()[:16],
        "code": "BLOCKED_MISSING_ACTOR_PAIR",
        "subject_ref": invariant_ref or operation_ref,
        "operation_ref": operation_ref,
        "risk_family": family,
        "required_relation_types": ["permits", "denies"],
        "required_binding": "distinct_runtime_control_and_treatment_actors",
        "description": (
            "Privacy/visibility testing requires one source-permitted actor "
            "and one source-denied actor for the same operation"
        ),
        "status": "unsupported",
        "source_refs": [
            dict(item) for item in source_refs if isinstance(item, dict)
        ][:5],
    }


def _pair_obligations(
    result: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    output = dict(_dict(result))
    ir = _dict(behavior_ir)
    actors = _base._active_actors(
        _base._accepted(_list(ir.get("actors")))
    )
    actors_by_id = {
        _text(actor.get("id")): actor
        for actor in actors
        if _text(actor.get("id"))
    }
    relations = _base._accepted(_list(ir.get("relations")))
    operations = {
        _text(operation.get("id")): operation
        for operation in _base._accepted(_list(ir.get("operations")))
        if _text(operation.get("id"))
    }
    invariants = {
        _text(invariant.get("id")): invariant
        for invariant in _base._accepted(_list(ir.get("invariants")))
        if _text(invariant.get("id"))
    }

    paired: list[dict[str, Any]] = []
    gaps = [
        dict(item)
        for item in _list(output.get("coverage_gaps"))
        if isinstance(item, dict)
    ]
    gap_ids = {_text(item.get("id")) for item in gaps}

    for obligation in _list(output.get("obligations")):
        if not isinstance(obligation, dict):
            continue
        family = _text(obligation.get("risk_family"))
        if family not in _PAIR_FAMILIES:
            paired.append(dict(obligation))
            continue
        prop = _dict(obligation.get("property"))
        existing_actors = [
            _text(value)
            for value in _list(obligation.get("required_actors"))
            if _text(value)
        ]
        control_ref = _text(prop.get("control_actor_ref"))
        treatment_ref = _text(prop.get("treatment_actor_ref"))
        if (
            len(set(existing_actors)) >= 2
            and control_ref
            and treatment_ref
            and control_ref != treatment_ref
        ):
            paired.append(dict(obligation))
            continue

        operation_ref = (
            next(
                (
                    _text(value)
                    for value in _list(obligation.get("required_operations"))
                    if _text(value)
                ),
                "",
            )
            or _text(prop.get("operation_ref"))
        )
        invariant_ref = _text(prop.get("invariant_ref"))
        permit_relations = [
            relation
            for relation in relations
            if _text(relation.get("operation_ref")) == operation_ref
            and _text(relation.get("relation_type")) == "permits"
            and _base._relation_actor_ref(relation) in actors_by_id
        ]
        deny_relations = [
            relation
            for relation in relations
            if _text(relation.get("operation_ref")) == operation_ref
            and _text(relation.get("relation_type")) == "denies"
            and _base._relation_actor_ref(relation) in actors_by_id
        ]
        pairs = [
            (permit, deny)
            for permit in permit_relations
            for deny in deny_relations
            if _base._relation_actor_ref(permit)
            != _base._relation_actor_ref(deny)
        ]
        if not pairs:
            gap = _actor_pair_gap(
                family=family,
                invariant_ref=invariant_ref,
                operation_ref=operation_ref,
                source_refs=[
                    dict(item)
                    for item in _list(obligation.get("source_refs"))
                    if isinstance(item, dict)
                ],
            )
            if gap["id"] not in gap_ids:
                gaps.append(gap)
                gap_ids.add(gap["id"])
            continue

        for permit, deny in pairs:
            allowed_ref = _base._relation_actor_ref(permit)
            denied_ref = _base._relation_actor_ref(deny)
            allowed = actors_by_id[allowed_ref]
            denied = actors_by_id[denied_ref]
            paired_property = dict(prop)
            paired_property.pop("actor_ref", None)
            paired_property.update({
                "template": f"{family}_control_treatment",
                "control_actor_ref": allowed_ref,
                "treatment_actor_ref": denied_ref,
                "operation_ref": operation_ref,
                "require_same_resource": True,
                "actor_pair_source": "permits_denies",
            })
            observer_ids = [
                _text(value)
                for value in _list(obligation.get("required_observers"))
                if _text(value)
            ]
            for observer_id in (
                "http_response",
                "actor_identity",
                "authorization_comparison",
            ):
                if observer_id not in observer_ids:
                    observer_ids.append(observer_id)
            relation_refs = sorted({
                *(
                    _text(value)
                    for value in _list(obligation.get("relation_refs"))
                    if _text(value)
                ),
                _text(permit.get("id")),
                _text(deny.get("id")),
            })
            source_node = {
                "source_refs": [
                    dict(item)
                    for item in _list(obligation.get("source_refs"))
                    if isinstance(item, dict)
                ]
            }
            source_refs = _base._combined_source_refs(
                source_node,
                invariants.get(invariant_ref) or {},
                operations.get(operation_ref) or {},
                allowed,
                denied,
                permit,
                deny,
            )
            confidence = min(
                float(obligation.get("confidence") or 0.5),
                float(allowed.get("confidence") or 0.7),
                float(denied.get("confidence") or 0.7),
                float(permit.get("confidence") or 0.8),
                float(deny.get("confidence") or 0.8),
            )
            paired.append(_base.make_obligation(
                risk_family=family,
                subject_refs=[
                    invariant_ref,
                    operation_ref,
                    allowed_ref,
                    denied_ref,
                ],
                property_spec=paired_property,
                required_actors=[allowed_ref, denied_ref],
                required_operations=[operation_ref],
                required_fixtures=[
                    _text(value)
                    for value in _list(obligation.get("required_fixtures"))
                    if _text(value)
                ],
                required_observers=observer_ids,
                cleanup_requirement=dict(
                    _dict(obligation.get("cleanup_requirement"))
                ),
                source_refs=source_refs,
                relation_refs=relation_refs,
                confidence=confidence,
            ))

    deduped = _base.dedupe_obligations(paired)
    output["obligations"] = deduped
    output["obligation_count"] = len(deduped)
    output["coverage_gaps"] = gaps
    output["by_family"] = {
        family: sum(
            1
            for obligation in deduped
            if _text(obligation.get("risk_family")) == family
        )
        for family in _base.RISK_FAMILIES
    }
    return output


def compile_obligations_from_behavior_ir(
    behavior_ir: dict[str, Any],
    *,
    base_compile: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    root: str = "",
    project: str = "",
) -> dict[str, Any]:
    compiler = base_compile or _original_compile
    if root or project:
        # Forwarded only when the caller actually has a workspace identity; a
        # base_compile that predates the parameters still works on plain IR.
        return _pair_obligations(
            compiler(behavior_ir, root=root, project=project),
            behavior_ir,
        )
    return _pair_obligations(
        compiler(behavior_ir),
        behavior_ir,
    )
