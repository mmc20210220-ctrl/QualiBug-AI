"""Append operation-contract validation seeds without requiring entity joins.

A request contract belongs to the operation that declares it. Testing explicit
JSON Schema/OpenAPI constraints therefore must not depend on a separate
operation<->entity relation. Actor execution authority does not move: a seed is
created only when a source-backed ``permits`` relation resolves to an active
runtime actor. Read-only probes require no cleanup; write probes retain the
canonical cleanup requirement. Public/no-permit operations remain a visible
coverage gap until public execution is source-declared by a dedicated authority.
"""
from __future__ import annotations

from typing import Any

from .behavior_ir_core import _infer_operation_effect, _operation_declares_public_access


def _d(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _l(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _t(value: Any) -> str:
    return str(value or "").strip()


def _core(compiler_base: Any) -> Any:
    pair = getattr(compiler_base, "_pair", None)
    core = getattr(pair, "_base", None) if pair is not None else None
    return core or compiler_base


def _operation_effect(operation: dict[str, Any]) -> str:
    return _t(
        _infer_operation_effect(
            operation,
            _t(operation.get("method")).upper(),
        )
    ).lower()



def _operation_declares_anonymous_execution(operation: dict[str, Any]) -> bool:
    # Behavior IR currently normalizes a missing security field to [], so an
    # empty list cannot prove that OpenAPI explicitly declared anonymous
    # access. Use only the operation's own source-backed public-access text.
    return _operation_declares_public_access(_d(operation))


def _ensure_source_declared_anonymous_actor(ir: dict[str, Any]) -> None:
    if not any(
        isinstance(operation, dict) and _operation_declares_anonymous_execution(operation)
        for operation in _l(ir.get("operations"))
    ):
        return
    from .credential_boundary_guard import _ensure_anonymous_actor

    _ensure_anonymous_actor(ir)


def _schema_probe_has_source_variants(operation: dict[str, Any], core: Any) -> bool:
    """Ask the canonical expander whether this operation has executable schema targets."""

    from .validation_obligation_expander import expand_validation_obligation

    probe = core.make_obligation(
        risk_family="validation",
        subject_refs=[_t(operation.get("id"))],
        property_spec={
            "template": "single_dimension_mutation",
            "operation_ref": _t(operation.get("id")),
            "operation_path_prefix": core._operation_path_prefix(operation),
            "require_control_success": True,
        },
        required_operations=[_t(operation.get("id"))],
        required_observers=["http_response"],
        cleanup_requirement={"required": False},
        source_refs=core._combined_source_refs(operation),
        confidence=float(operation.get("confidence") or 0.7),
    )
    variants = expand_validation_obligation(probe, operation=operation)
    return any(
        _t(_d(row).get("property", {}).get("validation_constraint_source"))
        == "request_schema"
        for row in variants
        if isinstance(row, dict)
    )


def append_operation_schema_validation_seeds(
    compiled: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
    compiler_base: Any,
) -> dict[str, Any]:
    """Return compiled obligations plus source-backed operation contract seeds."""

    core = _core(compiler_base)
    ir = _d(behavior_ir)
    _ensure_source_declared_anonymous_actor(ir)
    operations = core._accepted(_l(ir.get("operations")))
    actors = core._active_actors(core._accepted(_l(ir.get("actors"))))
    relations = core._accepted(_l(ir.get("relations")))
    active_by_id = {
        _t(actor.get("id")): actor
        for actor in actors
        if isinstance(actor, dict) and _t(actor.get("id"))
    }
    all_actors_by_id = {
        _t(actor.get("id")): actor
        for actor in core._accepted(_l(ir.get("actors")))
        if isinstance(actor, dict) and _t(actor.get("id"))
    }
    operations_by_id = {
        _t(operation.get("id")): operation
        for operation in operations
        if isinstance(operation, dict) and _t(operation.get("id"))
    }

    output = dict(_d(compiled))
    obligations = [
        dict(row) for row in _l(output.get("obligations")) if isinstance(row, dict)
    ]
    gaps = [dict(row) for row in _l(output.get("coverage_gaps")) if isinstance(row, dict)]
    existing_seed_keys = {
        (
            _t(_d(row.get("property")).get("operation_ref")),
            _t(_d(row.get("property")).get("actor_ref")),
        )
        for row in obligations
        if _t(row.get("risk_family")) == "validation"
        and _t(_d(row.get("property")).get("template")) == "single_dimension_mutation"
    }
    existing_gap_ids = {_t(row.get("id")) for row in gaps if _t(row.get("id"))}

    additions: list[dict[str, Any]] = []
    blocked_operation_refs: set[str] = set()

    def _append_gap(gap: dict[str, Any]) -> None:
        gap = dict(gap)
        gap["schema_validation_seed_blocked"] = True
        gap_id = _t(gap.get("id"))
        if gap_id and gap_id in existing_gap_ids:
            for existing in gaps:
                if _t(existing.get("id")) == gap_id:
                    existing["schema_validation_seed_blocked"] = True
                    return
        gaps.append(gap)
        if gap_id:
            existing_gap_ids.add(gap_id)

    for operation_ref in sorted(operations_by_id):
        operation = operations_by_id[operation_ref]
        effect = _operation_effect(operation)
        if effect not in {"read", "write"}:
            continue
        if not _schema_probe_has_source_variants(operation, core):
            continue

        permit_relations = core._relations_for_operation(
            relations, operation_ref, {"permits"}
        )
        explicit_anonymous = _operation_declares_anonymous_execution(operation)
        permitted_actor_refs = sorted({
            core._relation_actor_ref(relation)
            for relation in permit_relations
            if core._relation_actor_ref(relation) in active_by_id
        })
        if explicit_anonymous and "anonymous" in active_by_id:
            permitted_actor_refs = ["anonymous"]
        if not permitted_actor_refs:
            blocked_operation_refs.add(operation_ref)
            if not permit_relations:
                gap = core._compile_gap(
                    subject_ref=operation_ref, relation_types={"permits"}
                )
                gap = dict(gap)
                gap["description"] = (
                    "Operation declares request-schema validation constraints but no "
                    "source-backed permits relation authorizes an executable probe"
                )
                _append_gap(gap)
            else:
                emitted = False
                for relation in permit_relations:
                    actor_ref = core._relation_actor_ref(relation)
                    actor = all_actors_by_id.get(actor_ref)
                    if not actor:
                        continue
                    _append_gap(core._actor_binding_gap(
                        actor=actor,
                        operation_ref=operation_ref,
                        relation=relation,
                    ))
                    emitted = True
                if not emitted:
                    _append_gap(core._compile_gap(
                        subject_ref=operation_ref, relation_types={"permits"}
                    ))
            continue

        actor_ref = permitted_actor_refs[0]
        if (operation_ref, actor_ref) in existing_seed_keys:
            continue
        actor = active_by_id[actor_ref]
        actor_relations = [
            relation
            for relation in permit_relations
            if core._relation_actor_ref(relation) == actor_ref
        ]
        cleanup_requirement = (
            core._cleanup_requirement(operation, operations, relations, required=True)
            if effect == "write"
            else {"required": False}
        )
        additions.append(core.make_obligation(
            risk_family="validation",
            subject_refs=[operation_ref, actor_ref],
            property_spec={
                "template": "single_dimension_mutation",
                "operation_ref": operation_ref,
                "actor_ref": actor_ref,
                "operation_path_prefix": core._operation_path_prefix(operation),
                "require_control_success": True,
                "schema_validation_seed_authority": "operation_request_contract",
                "operation_effect": effect,
                "actor_execution_authority": (
                    "operation_public_access_contract"
                    if explicit_anonymous
                    else "source_permits_relation"
                ),
            },
            required_actors=[actor_ref],
            required_operations=[operation_ref],
            required_observers=["http_response"],
            cleanup_requirement=cleanup_requirement,
            source_refs=core._combined_source_refs(
                operation, actor, *actor_relations
            ),
            relation_refs=sorted({
                _t(relation.get("id"))
                for relation in actor_relations
                if _t(relation.get("id"))
            }),
            confidence=min(
                float(operation.get("confidence") or 0.7),
                float(actor.get("confidence") or 0.7),
            ),
        ))
        existing_seed_keys.add((operation_ref, actor_ref))

    obligations = core.dedupe_obligations([*obligations, *additions])
    from .coverage_unit_registry import attach_canonical_obligation_keys

    obligations = attach_canonical_obligation_keys(obligations, behavior_ir=ir)
    output["obligations"] = obligations
    output["obligation_count"] = len(obligations)
    output["coverage_gaps"] = gaps
    output["by_family"] = {
        family: sum(1 for row in obligations if _t(row.get("risk_family")) == family)
        for family in core.RISK_FAMILIES
    }
    output["schema_validation_seed_receipt"] = {
        "authority": "operation_request_contract",
        "seed_count": len(additions),
        "blocked_operation_count": len(blocked_operation_refs),
        "source_order_selection_allowed": False,
        "implicit_public_actor_allowed": False,
    }
    return output


__all__ = ["append_operation_schema_validation_seeds"]
