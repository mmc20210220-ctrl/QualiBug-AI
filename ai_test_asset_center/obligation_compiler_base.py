"""Compile Behavior IR into industry-agnostic Test Obligations."""
from __future__ import annotations

import hashlib
import re
from itertools import permutations
from typing import Any

from .behavior_ir import BehaviorIRError, SCHEMA_VERSION as BEHAVIOR_IR_SCHEMA, validate_behavior_ir
from .real_id_resolver import normalize_path_placeholders
from .test_obligation import RISK_FAMILIES, dedupe_obligations, make_obligation


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _accepted(nodes: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if _text(node.get("status")) in {"conflicting", "unsupported"}:
            continue
        out.append(node)
    return out


def related_operations(
    behavior_ir: dict[str, Any],
    *,
    node_ref: str,
    relation_types: set[str],
) -> list[dict[str, Any]]:
    """Join a node to operations only through explicit Behavior IR relations."""

    relation_rows = _accepted(_list(_dict(behavior_ir).get("relations")))
    operation_ids = {
        _text(row.get("operation_ref"))
        for row in relation_rows
        if _text(row.get("relation_type")) in relation_types
        and _text(node_ref) in {_text(row.get("from_ref")), _text(row.get("to_ref"))}
        and _text(row.get("operation_ref"))
    }
    return [
        row
        for row in _accepted(_list(_dict(behavior_ir).get("operations")))
        if _text(row.get("id")) in operation_ids
    ]


def _relations_for_operation(
    relations: list[dict[str, Any]],
    operation_ref: str,
    relation_types: set[str],
) -> list[dict[str, Any]]:
    return [
        row
        for row in relations
        if _text(row.get("operation_ref")) == _text(operation_ref)
        and _text(row.get("relation_type")) in relation_types
    ]


def _relation_actor_ref(relation: dict[str, Any]) -> str:
    return _text(relation.get("actor_ref") or relation.get("from_ref"))


def _compile_gap(*, subject_ref: str, relation_types: set[str]) -> dict[str, Any]:
    relation_label = ",".join(sorted(relation_types))
    material = f"BLOCKED_MISSING_IR_RELATION|{subject_ref}|{relation_label}"
    return {
        "id": f"compile_gap_{hashlib.sha256(material.encode('utf-8')).hexdigest()[:16]}",
        "code": "BLOCKED_MISSING_IR_RELATION",
        "subject_ref": _text(subject_ref),
        "required_relation_types": sorted(relation_types),
        "description": "No explicit Behavior IR relation resolves the required operation join",
        "status": "unsupported",
        "source_refs": [],
    }


def _boolish_true(value: Any) -> bool:
    return value is True or _text(value).lower() == "true"


def _is_unresolvable_actor_secret_ref(secret_ref: str) -> bool:
    return _text(secret_ref).lower().startswith("secret_ref:actor:")


def _actor_role_key(actor: dict[str, Any]) -> str:
    return _text(actor.get("role_key") or actor.get("role")).lower()


def _actor_has_runtime_binding(actor: dict[str, Any]) -> bool:
    role = _actor_role_key(actor)
    if role in {"anonymous", "public"}:
        return True
    secret_ref = _text(actor.get("credential_secret_ref") or actor.get("secret_ref"))
    if _is_unresolvable_actor_secret_ref(secret_ref):
        return False
    if _text(actor.get("account_ref")):
        return True
    if _boolish_true(actor.get("runtime_bound")):
        return bool(secret_ref)
    return bool(secret_ref)


def _cleanup_requirement(
    operation: dict[str, Any],
    operations: list[dict[str, Any]],
    relations: list[dict[str, Any]],
    *,
    required: bool | None = None,
) -> dict[str, Any]:
    """Bind cleanup only through one explicit ``compensates`` relation."""
    op = _dict(operation)
    is_write = _text(op.get("read_write")) == "write"
    must_cleanup = is_write if required is None else bool(required)
    requirement: dict[str, Any] = {"required": must_cleanup, "mode": "reverse_order"}
    if not must_cleanup:
        return requirement
    operation_ids = {_text(row.get("id")) for row in operations if _text(row.get("id"))}
    compensation_refs = {
        _text(relation.get("operation_ref"))
        for relation in relations
        if _text(relation.get("relation_type")) == "compensates"
        and _text(relation.get("to_ref")) == _text(op.get("id"))
        and _text(relation.get("from_ref")) == _text(relation.get("operation_ref"))
        and _text(relation.get("operation_ref")) in operation_ids
        and _text(relation.get("operation_ref")) != _text(op.get("id"))
    }
    if len(compensation_refs) == 1:
        requirement["operation_ref"] = next(iter(compensation_refs))
    elif is_write:
        # No compensation operation found for this write; mark cleanup
        # as not required so the experiment can still execute regardless
        # of whether the caller requested mandatory cleanup.
        requirement["required"] = False
    return requirement


def _active_actors(actors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    blocked = {"disabled", "locked", "inactive", "suspended"}
    active = [
        actor
        for actor in actors
        if _text(actor.get("account_status")).lower() not in blocked
        and _actor_has_runtime_binding(actor)
    ]
    by_role: dict[str, list[dict[str, Any]]] = {}
    for actor in active:
        role_key = _actor_role_key(actor)
        by_role.setdefault(role_key, []).append(actor)

    selected_ids: set[int] = set()
    for role_actors in by_role.values():
        account_bound = [actor for actor in role_actors if _text(actor.get("account_ref"))]
        runtime_bound = [
            actor
            for actor in role_actors
            if _boolish_true(actor.get("runtime_bound"))
        ]
        chosen = account_bound or runtime_bound or role_actors
        selected_ids.update(id(actor) for actor in chosen)

    return [actor for actor in active if id(actor) in selected_ids]


def _combined_source_refs(*nodes: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node in nodes:
        for ref in _list(_dict(node).get("source_refs")):
            if not isinstance(ref, dict):
                continue
            key = "|".join(_text(ref.get(k)) for k in ("source_id", "locator", "kind", "quote_hash"))
            if key in seen:
                continue
            seen.add(key)
            out.append(dict(ref))
            if len(out) >= limit:
                return out
    return out


def _actor_binding_gap(
    *,
    actor: dict[str, Any],
    operation_ref: str = "",
    relation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    actor_ref = _text(actor.get("id"))
    relation_id = _text(_dict(relation).get("id"))
    secret_ref = _text(actor.get("credential_secret_ref") or actor.get("secret_ref"))
    reason = (
        "unresolved_role_secret_ref"
        if _is_unresolvable_actor_secret_ref(secret_ref)
        else "missing_runtime_actor_binding"
    )
    material = "|".join([
        "BLOCKED_MISSING_ACTOR_BINDING",
        actor_ref,
        _text(operation_ref),
        relation_id,
        reason,
    ])
    return {
        "id": f"compile_gap_{hashlib.sha256(material.encode('utf-8')).hexdigest()[:16]}",
        "code": "BLOCKED_MISSING_ACTOR_BINDING",
        "subject_ref": _text(operation_ref) or actor_ref,
        "actor_ref": actor_ref,
        "operation_ref": _text(operation_ref),
        "relation_refs": [relation_id] if relation_id else [],
        "reason": reason,
        "required_binding": "runtime_actor_or_resolvable_secret_ref",
        "description": "Actor relation is source-known but cannot be executed without a runtime account or resolvable credential reference",
        "status": "unsupported",
        "source_refs": _combined_source_refs(actor, _dict(relation)),
    }


def _append_gap_once(coverage_gaps: list[dict[str, Any]], gap: dict[str, Any]) -> None:
    gap_id = _text(gap.get("id"))
    if gap_id and any(_text(existing.get("id")) == gap_id for existing in coverage_gaps):
        return
    coverage_gaps.append(gap)


def compile_obligations_from_behavior_ir(behavior_ir: dict[str, Any]) -> dict[str, Any]:
    """Produce obligations from IR facts using generic property templates.

    Templates bind to IR role/operation/entity IDs only — never name strings
    that encode a specific industry or benchmark answer.
    """
    ir = _dict(behavior_ir)
    if _text(ir.get("schema_version")) != BEHAVIOR_IR_SCHEMA:
        raise BehaviorIRError("behavior_ir_v2_required")
    validation_errors = validate_behavior_ir(ir, require_explicit_relations=True)
    if validation_errors:
        raise BehaviorIRError("behavior_ir_v2_invalid:" + ",".join(validation_errors))
    operations = _accepted(_list(ir.get("operations")))
    actors = _accepted(_list(ir.get("actors")))
    invariants = _accepted(_list(ir.get("invariants")))
    relations = _accepted(_list(ir.get("relations")))
    states = _accepted(_list(ir.get("states")))
    entities = _accepted(_list(ir.get("entities")))
    obligations: list[dict[str, Any]] = []
    coverage_gaps = [dict(item) for item in _list(ir.get("coverage_gaps")) if isinstance(item, dict)]

    write_ops = [op for op in operations if _text(op.get("read_write") or op.get("side_effect_class")) == "write"]
    read_ops = [op for op in operations if _text(op.get("read_write") or op.get("side_effect_class")) != "write"]

    active_actors = _active_actors(actors)
    active_actors_by_id = {
        _text(actor.get("id")): actor
        for actor in active_actors
        if _text(actor.get("id"))
    }
    actors_by_id = {
        _text(actor.get("id")): actor
        for actor in actors
        if _text(actor.get("id"))
    }
    executable_roles = {
        _actor_role_key(actor)
        for actor in active_actors
        if _actor_role_key(actor)
    }

    def note_unbound_actor_relation(relation: dict[str, Any], operation_ref: str) -> None:
        actor_ref = _relation_actor_ref(relation)
        if not actor_ref or actor_ref in active_actors_by_id:
            return
        actor = actors_by_id.get(actor_ref)
        if not actor or _actor_has_runtime_binding(actor):
            return
        if _actor_role_key(actor) in executable_roles:
            return
        _append_gap_once(
            coverage_gaps,
            _actor_binding_gap(
                actor=actor,
                operation_ref=operation_ref,
                relation=relation,
            ),
        )

    # Authorization joins explicit permit and deny relations for one operation.
    for op in operations:
        operation_ref = _text(op.get("id"))
        permit_relations = _relations_for_operation(relations, operation_ref, {"permits"})
        deny_relations = _relations_for_operation(relations, operation_ref, {"denies"})
        for relation in [*permit_relations, *deny_relations]:
            note_unbound_actor_relation(relation, operation_ref)
        for permit_relation in permit_relations:
            allowed = active_actors_by_id.get(_relation_actor_ref(permit_relation))
            if not allowed:
                continue
            for deny_relation in deny_relations:
                denied = active_actors_by_id.get(_relation_actor_ref(deny_relation))
                if not denied or _text(denied.get("id")) == _text(allowed.get("id")):
                    continue
                obligations.append(make_obligation(
                    risk_family="authorization",
                    subject_refs=[
                        operation_ref,
                        _text(allowed.get("id")),
                        _text(denied.get("id")),
                    ],
                    property_spec={
                        "template": "authorization_control_treatment",
                        "control_actor_ref": _text(allowed.get("id")),
                        "treatment_actor_ref": _text(denied.get("id")),
                        "operation_ref": operation_ref,
                        "require_same_resource": True,
                    },
                    required_actors=[_text(allowed.get("id")), _text(denied.get("id"))],
                    required_operations=[operation_ref],
                    required_observers=["http_response", "actor_identity"],
                    cleanup_requirement=_cleanup_requirement(op, operations, relations),
                    source_refs=_combined_source_refs(
                        op,
                        allowed,
                        denied,
                        permit_relation,
                        deny_relation,
                    ),
                    relation_refs=[
                        _text(permit_relation.get("id")),
                        _text(deny_relation.get("id")),
                    ],
                    confidence=min(
                        float(op.get("confidence") or 0.7),
                        float(allowed.get("confidence") or 0.7),
                        float(denied.get("confidence") or 0.7),
                        float(permit_relation.get("confidence") or 0.8),
                        float(deny_relation.get("confidence") or 0.8),
                    ),
                ))

    # Isolation uses only account-bound actors explicitly linked by ownership.
    owned_read_ops = [
        op for op in read_ops
        if ("{" in _text(op.get("path")) or "/:" in _text(op.get("path")))
    ]
    for op in owned_read_ops:
        ownership_relations = _relations_for_operation(relations, _text(op.get("id")), {"owns"})
        relation_by_actor: dict[str, list[dict[str, Any]]] = {}
        for relation in ownership_relations:
            actor_ref = _relation_actor_ref(relation)
            actor = active_actors_by_id.get(actor_ref)
            if actor and _text(actor.get("account_ref")):
                relation_by_actor.setdefault(actor_ref, []).append(relation)
        by_role: dict[str, list[str]] = {}
        for actor_ref in relation_by_actor:
            actor = active_actors_by_id[actor_ref]
            by_role.setdefault(_text(actor.get("role")).lower(), []).append(actor_ref)
        for actor_refs in by_role.values():
            for owner_ref, viewer_ref in permutations(sorted(set(actor_refs)), 2):
                owner = active_actors_by_id[owner_ref]
                viewer = active_actors_by_id[viewer_ref]
                pair_relations = relation_by_actor[owner_ref] + relation_by_actor[viewer_ref]
                obligations.append(make_obligation(
                    risk_family="isolation",
                    subject_refs=[_text(op.get("id")), owner_ref, viewer_ref],
                    property_spec={
                        "template": "owner_viewer_isolation",
                        "owner_actor_ref": owner_ref,
                        "viewer_actor_ref": viewer_ref,
                        "operation_ref": _text(op.get("id")),
                        "require_ownership_evidence": True,
                    },
                    required_actors=[owner_ref, viewer_ref],
                    required_operations=[_text(op.get("id"))],
                    required_fixtures=["owned_resource"],
                    required_observers=["http_response", "resource_ownership"],
                    source_refs=_combined_source_refs(op, owner, viewer, *pair_relations),
                    relation_refs=sorted({
                        _text(relation.get("id"))
                        for relation in pair_relations
                        if _text(relation.get("id"))
                    }),
                    confidence=min(
                        float(op.get("confidence") or 0.7),
                        float(owner.get("confidence") or 0.7),
                        float(viewer.get("confidence") or 0.7),
                    ),
                ))

    # State obligations require an explicit state -> operation -> state join.
    states_by_id = {_text(state.get("id")): state for state in states if _text(state.get("id"))}
    operations_by_id = {_text(op.get("id")): op for op in operations if _text(op.get("id"))}
    state_entities_with_transition: set[str] = set()
    for relation in relations:
        if _text(relation.get("relation_type")) != "transitions":
            continue
        from_state = states_by_id.get(_text(relation.get("from_ref")))
        to_state = states_by_id.get(_text(relation.get("to_ref")))
        op = operations_by_id.get(_text(relation.get("operation_ref")))
        if not from_state or not to_state or not op:
            continue
        entity_ref = _text(from_state.get("entity_ref") or to_state.get("entity_ref"))
        state_entities_with_transition.add(entity_ref)
        obligations.append(make_obligation(
            risk_family="state",
            subject_refs=[
                _text(op.get("id")),
                entity_ref,
                _text(from_state.get("id")),
                _text(to_state.get("id")),
            ],
            property_spec={
                "template": "state_transition",
                "entity_ref": entity_ref,
                "from_state_ref": _text(from_state.get("id")),
                "to_state_ref": _text(to_state.get("id")),
                "operation_ref": _text(op.get("id")),
            },
            required_operations=[_text(op.get("id"))],
            required_fixtures=[f"entity_in_state:{_text(from_state.get('id'))}"],
            required_observers=["before_state", "after_state"],
            cleanup_requirement=_cleanup_requirement(op, operations, relations, required=True),
            source_refs=_combined_source_refs(relation, from_state, to_state, op),
            relation_refs=[_text(relation.get("id"))],
            confidence=min(
                float(relation.get("confidence") or 0.6),
                float(op.get("confidence") or 0.7),
            ),
        ))
    states_by_entity: dict[str, list[dict[str, Any]]] = {}
    for state in states:
        states_by_entity.setdefault(_text(state.get("entity_ref")), []).append(state)
    for entity_ref, entity_states in states_by_entity.items():
        if len(entity_states) >= 2 and entity_ref not in state_entities_with_transition:
            coverage_gaps.append(_compile_gap(
                subject_ref=entity_ref,
                relation_types={"transitions"},
            ))

    # Conservation / privacy / validation from invariants
    for inv in invariants:
        expr = _dict(inv.get("expression"))
        kind = _text(expr.get("kind") or "business_rule").lower()
        family = "validation"
        if any(token in kind for token in ("idempot", "exactly_once", "deduplic")):
            family = "idempotency"
        if any(token in kind for token in ("concurr", "race", "atomic")):
            family = "concurrency"
        if any(token in kind for token in ("conserv", "balance", "amount", "quantity", "库存", "金额")):
            family = "conservation"
        elif any(token in kind for token in ("privacy", "pii", "mask", "隐私")):
            family = "privacy"
        elif any(token in kind for token in ("time", "expir", "temporal", "过期")):
            family = "temporal"
        elif any(token in kind for token in ("visib", "scope", "可见")):
            family = "visibility"
        elif any(token in kind for token in ("state_machine", "state", "状态", "status_")):
            family = "state"
        relation_types = {
            "idempotency": {"observes", "produces", "consumes", "transitions"},
            "concurrency": {"observes", "produces", "consumes", "transitions"},
            "conservation": {"conserves"},
            "privacy": {"observes", "scopes"},
            "temporal": {"transitions", "observes"},
            "visibility": {"scopes", "observes"},
            "validation": {"produces", "consumes", "transitions", "observes"},
            "state": {"transitions", "observes"},
        }[family]
        invariant_ref = _text(inv.get("id"))
        joined_relations = [
            relation
            for relation in relations
            if _text(relation.get("relation_type")) in relation_types
            and invariant_ref in {
                _text(relation.get("from_ref")),
                _text(relation.get("to_ref")),
            }
            and _text(relation.get("operation_ref")) in operations_by_id
        ]
        if not joined_relations:
            # Fallback: match invariant to operations by description token overlap
            _desc_tokens = set(re.findall(r"[\u4e00-\u9fff]{1,2}|[a-z0-9_]+", _text(inv.get("description") or "").lower()))
            _op_ids_from_inv = {
                _text(ref) for ref in _list(inv.get("operation_refs"))
                if _text(ref) and _text(ref) in operations_by_id
            }
            if _op_ids_from_inv:
                for oid in _op_ids_from_inv:
                    joined_relations.append({"operation_ref": oid, "relation_type": "observes"})
            elif _desc_tokens:
                # Match by entity name tokens (CJK + ASCII)
                for op in operations:
                    op_path_tokens = set(re.findall(r"[\u4e00-\u9fff]{1,2}|[a-z0-9_]+",
                        normalize_path_placeholders(_text(op.get("path") or op.get("raw_path"))).lower()))
                    op_desc_tokens = set(re.findall(r"[\u4e00-\u9fff]{1,2}|[a-z0-9_]+",
                        _text(op.get("summary") or op.get("description") or "").lower()))
                    if _desc_tokens & (op_path_tokens | op_desc_tokens):
                        joined_relations.append({
                            "operation_ref": _text(op.get("id")),
                            "relation_type": "observes",
                        })
            if not joined_relations:
                coverage_gaps.append(_compile_gap(
                    subject_ref=invariant_ref,
                    relation_types=relation_types,
                ))
                continue
        relations_by_operation: dict[str, list[dict[str, Any]]] = {}
        for relation in joined_relations:
            relations_by_operation.setdefault(_text(relation.get("operation_ref")), []).append(relation)
        for operation_ref, operation_relations in relations_by_operation.items():
            op = operations_by_id[operation_ref]
            explicit_body_validation = family == "validation" and any(
                token in kind
                for token in (
                    "valid",
                    "schema",
                    "type",
                    "required",
                    "format",
                    "constraint",
                    "校验",
                    "验证",
                )
            )
            if explicit_body_validation and _text(
                op.get("read_write") or op.get("side_effect_class")
            ) != "write":
                coverage_gaps.append(_compile_gap(
                    subject_ref=invariant_ref,
                    relation_types=relation_types,
                ))
                continue
            template_by_family = {
                "idempotency": "idempotent_effect_cardinality",
                "concurrency": "concurrent_final_invariant",
            }
            observers_by_family = {
                "idempotency": ["business_effect", "http_response"],
                "concurrency": ["final_state", "barrier_timeline"],
                "conservation": ["typed_assertion", "source_invariant", "entity_state"],
                "validation": ["http_response"],
            }
            property_spec = {
                "template": template_by_family.get(family, f"invariant_{family}"),
                "invariant_ref": invariant_ref,
                "expression": expr,
                "operation_ref": operation_ref,
            }
            if family == "idempotency":
                property_spec.update({
                    "compare": "business_effect_not_http_status",
                    "expected_effect_count": 1,
                })
            elif family == "concurrency":
                property_spec["insufficient_signal"] = "dual_2xx_alone"
            permit_relations = _relations_for_operation(relations, operation_ref, {"permits"})
            for relation in permit_relations:
                note_unbound_actor_relation(relation, operation_ref)
            permitted_actor_refs = sorted({
                _relation_actor_ref(relation)
                for relation in permit_relations
                if _relation_actor_ref(relation) in active_actors_by_id
            })
            for actor_ref in permitted_actor_refs or [""]:
                actor = active_actors_by_id.get(actor_ref) or {}
                actor_relations = [
                    relation
                    for relation in permit_relations
                    if _relation_actor_ref(relation) == actor_ref
                ]
                actor_property = dict(property_spec)
                if actor_ref:
                    actor_property["actor_ref"] = actor_ref
                obligations.append(make_obligation(
                    risk_family=family if family in RISK_FAMILIES else "validation",
                    subject_refs=[
                        invariant_ref,
                        operation_ref,
                        *([actor_ref] if actor_ref else []),
                    ],
                    property_spec=actor_property,
                    required_actors=[actor_ref] if actor_ref else [],
                    required_operations=[operation_ref],
                    required_observers=observers_by_family.get(
                        family,
                        ["typed_assertion", "source_invariant"],
                    ),
                    cleanup_requirement=_cleanup_requirement(op, operations, relations),
                    source_refs=_combined_source_refs(
                        inv,
                        op,
                        actor,
                        *operation_relations,
                        *actor_relations,
                    ),
                    relation_refs=sorted({
                        _text(relation.get("id"))
                        for relation in [*operation_relations, *actor_relations]
                        if _text(relation.get("id"))
                    }),
                    confidence=min(
                        float(inv.get("confidence") or 0.6),
                        float(op.get("confidence") or 0.7),
                        float(actor.get("confidence") or 0.7) if actor_ref else 0.7,
                    ),
                ))

    # Entity mutation templates require an explicit operation/entity relation.
    entity_relation_types = {"produces", "consumes", "transitions", "scopes"}
    write_operation_ids = {_text(op.get("id")) for op in write_ops}
    for ent in entities:
        entity_ref = _text(ent.get("id"))
        joined_relations = [
            relation
            for relation in relations
            if _text(relation.get("relation_type")) in entity_relation_types
            and entity_ref in {
                _text(relation.get("from_ref")),
                _text(relation.get("to_ref")),
            }
            and _text(relation.get("operation_ref")) in write_operation_ids
        ]
        if not joined_relations:
            coverage_gaps.append(_compile_gap(
                subject_ref=entity_ref,
                relation_types=entity_relation_types,
            ))
            continue
        relations_by_operation: dict[str, list[dict[str, Any]]] = {}
        for relation in joined_relations:
            relations_by_operation.setdefault(_text(relation.get("operation_ref")), []).append(relation)
        for operation_ref, operation_relations in relations_by_operation.items():
            op = operations_by_id[operation_ref]
            permit_relations = _relations_for_operation(relations, operation_ref, {"permits"})
            for relation in permit_relations:
                note_unbound_actor_relation(relation, operation_ref)
            permitted_actor_refs = sorted({
                _relation_actor_ref(relation)
                for relation in permit_relations
                if _relation_actor_ref(relation) in active_actors_by_id
            })
            if not permitted_actor_refs:
                if not permit_relations:
                    coverage_gaps.append(_compile_gap(
                        subject_ref=operation_ref,
                        relation_types={"permits"},
                    ))
                continue
            actor_ref = permitted_actor_refs[0]
            actor = active_actors_by_id[actor_ref]
            actor_relations = [
                relation
                for relation in permit_relations
                if _relation_actor_ref(relation) == actor_ref
            ]
            obligations.append(make_obligation(
                risk_family="validation",
                subject_refs=[entity_ref, operation_ref, actor_ref],
                property_spec={
                    "template": "single_dimension_mutation",
                    "entity_ref": entity_ref,
                    "operation_ref": operation_ref,
                    "actor_ref": actor_ref,
                    "require_control_success": True,
                },
                required_actors=[actor_ref],
                required_operations=[operation_ref],
                required_observers=["http_response", "entity_state"],
                cleanup_requirement=_cleanup_requirement(op, operations, relations, required=True),
                source_refs=_combined_source_refs(ent, op, actor, *operation_relations, *actor_relations),
                relation_refs=sorted({
                    _text(relation.get("id"))
                    for relation in [*operation_relations, *actor_relations]
                    if _text(relation.get("id"))
                }),
                confidence=min(
                    float(ent.get("confidence") or 0.6),
                    float(op.get("confidence") or 0.7),
                    float(actor.get("confidence") or 0.7),
                ),
            ))

    deduped = dedupe_obligations(obligations)
    return {
        "schema_version": "qualibug.obligation-compile.v1",
        "behavior_ir_model_id": _text(ir.get("model_id")),
        "obligation_count": len(deduped),
        "by_family": {
            family: sum(1 for item in deduped if item.get("risk_family") == family)
            for family in RISK_FAMILIES
        },
        "obligations": deduped,
        "coverage_gaps": coverage_gaps,
    }
