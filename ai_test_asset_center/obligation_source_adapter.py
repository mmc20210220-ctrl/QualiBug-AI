"""Pure adapter from legacy discovery candidates to Test Obligations.

The adapter resolves source intent against Behavior IR only.  It cannot
execute traffic, verify a result, classify a defect, or persist formal state.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .behavior_ir import BehaviorIRError, SCHEMA_VERSION as BEHAVIOR_IR_SCHEMA
from .test_obligation import (
    RISK_FAMILIES,
    dedupe_obligations,
    make_obligation,
    resolve_risk_family,
)


ADAPTER_SCHEMA = "qualibug.obligation-source-adapter.v1"
_FORBIDDEN_AUTHORITY_KEYS = frozenset({
    "customer_delivery_status",
    "execution_status",
    "gate_passed",
    "oracle_verdict",
    "send_request",
})
# _FAMILY_ALIASES moved to test_obligation.RISK_FAMILY_ALIASES so there is one
# resolution authority instead of two divergent maps. Every entry was carried over
# with the same canonical target; the one deliberate change is tenant_isolation,
# which had no entry here and so silently became "validation".
_RELATION_TYPES_BY_FAMILY = {
    "authorization": {"permits", "denies"},
    "isolation": {"owns", "scopes"},
    "state": {"transitions"},
    "conservation": {"conserves"},
    "idempotency": {"produces", "consumes", "transitions"},
    "concurrency": {"produces", "consumes", "transitions"},
    "validation": {"produces", "consumes", "transitions", "observes"},
    "visibility": {"scopes", "observes"},
    "temporal": {"transitions", "observes"},
    "privacy": {"scopes", "observes"},
}
_TEMPLATE_BY_FAMILY = {
    "authorization": "authorization_control_treatment",
    "isolation": "owner_viewer_isolation",
    "state": "state_transition",
    "conservation": "invariant_conservation",
    "idempotency": "idempotent_effect_cardinality",
    "concurrency": "concurrent_final_invariant",
    "validation": "source_declared_validation",
    "visibility": "source_declared_visibility",
    "temporal": "source_declared_temporal",
    "privacy": "source_declared_privacy",
}
# Every entry MUST name observers present and implemented in
# observer_contracts_base.OBSERVER_REGISTRY. compile_observer_requirements returns
# BLOCKED_MISSING_OBSERVER for an unregistered id, so a typo here silently kills a whole
# risk family on this path.
#
# It did exactly that: "resource_visibility", "clock" and "privacy_surface" were never
# registered, so visibility, temporal and privacy -- 3 of the 10 canonical families --
# compiled to BLOCKED_MISSING_OBSERVER every time an obligation reached this path.
# Verified by calling compile_observer_requirements over each family's declared set.
# Replaced with the registered observers that serve the same role:
#   visibility  : resource_ownership -- the implemented actor-vs-resource access observer
#                 (isolation already uses it for the same question)
#   temporal    : temporal_window -- the implemented observer on the
#                 temporal_convergence surface
#   privacy     : actor_identity alongside http_response, so a field-exposure assertion
#                 has both the response body and the identity that received it
#
# tests/test_family_observer_registration.py asserts every family's set compiles, so a
# family can no longer be declared against an observer that does not exist.
_OBSERVERS_BY_FAMILY = {
    "authorization": ["http_response", "actor_identity"],
    "isolation": ["http_response", "resource_ownership"],
    "state": ["before_state", "after_state"],
    "conservation": ["before_state", "after_state", "typed_assertion"],
    "idempotency": ["business_effect", "http_response"],
    "concurrency": ["final_state", "barrier_timeline"],
    "validation": ["http_response", "typed_assertion"],
    "visibility": ["http_response", "resource_ownership"],
    "temporal": ["before_state", "after_state", "temporal_window"],
    "privacy": ["http_response", "actor_identity"],
}


class ObligationSourceAdapterError(BehaviorIRError):
    """A source candidate cannot be represented as a grounded obligation."""


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _path_shape(value: Any) -> str:
    path = _text(value).split("?", 1)[0].strip().lower()
    if not path:
        return ""
    segments = [
        "{}"
        if (
            (segment.startswith("{") and segment.endswith("}"))
            or segment.startswith(":")
        )
        else segment
        for segment in path.strip("/").split("/")
        if segment
    ]
    return "/" + "/".join(segments)


def _assert_source_only(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = re.sub(r"[^a-z0-9_]+", "_", _text(key).lower()).strip("_")
            if normalized in _FORBIDDEN_AUTHORITY_KEYS:
                raise ObligationSourceAdapterError(
                    f"candidate_execution_authority_forbidden:{normalized}"
                )
            _assert_source_only(item)
    elif isinstance(value, list):
        for item in value:
            _assert_source_only(item)


def _declared_risk_family(candidate: dict[str, Any]) -> str:
    """The family as the candidate declared it, before any narrowing."""
    return _text(
        candidate.get("risk_family")
        or candidate.get("family")
        or candidate.get("category")
        or candidate.get("risk_type")
    ).lower()


def _risk_family(candidate: dict[str, Any]) -> str:
    """Resolve through the single registry authority in test_obligation.

    This used to apply a private _FAMILY_ALIASES map and then silently rewrite
    anything unrecognized to "validation" -- a second, divergent taxonomy. The
    registry now owns aliasing, and make_obligation records the declared family
    plus a reason code, so the narrowing is visible in the obligation.
    """
    return resolve_risk_family(_declared_risk_family(candidate))["canonical"]


def _stable_gap(
    *,
    candidate_id: str,
    code: str,
    detail: str,
) -> dict[str, Any]:
    material = f"{candidate_id}|{code}|{detail}"
    return {
        "gap_id": f"adapter_gap_{hashlib.sha256(material.encode('utf-8')).hexdigest()[:16]}",
        "candidate_id": candidate_id,
        "code": code,
        "detail": detail,
    }


def _candidate_id(candidate: dict[str, Any]) -> str:
    supplied = _text(
        candidate.get("candidate_id")
        or candidate.get("hypothesis_id")
        or candidate.get("slice_id")
        or candidate.get("id")
    )
    if supplied:
        return supplied[:160]
    canonical = json.dumps(candidate, ensure_ascii=False, sort_keys=True, default=str)
    return f"candidate_{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:20]}"


def _depth_uncompiled_detail(candidate: dict[str, Any]) -> str:
    """Name the reason deep comprehension cannot compile to a single operation.

    Returns "" when the candidate carries no cross-entity / multi-step depth,
    otherwise a short reason code. Used to emit an explicit coverage gap so the
    uncompiled depth is countable, never silently dropped.
    """
    depth = candidate.get("depth")
    if not isinstance(depth, dict) or not depth:
        return ""
    if depth.get("cascade_chain"):
        return "cascade_chain_uncompiled"
    steps = depth.get("verification_steps")
    if isinstance(steps, dict) and len(steps) > 1:
        return "multi_step_verification_uncompiled"
    if depth.get("target_entity") and depth.get("target_entity") != _text(
        candidate.get("entity")
    ):
        return "cross_entity_uncompiled"
    return ""


def _operation_matches(
    candidate: dict[str, Any],
    operations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    operation_hint = _text(
        candidate.get("operation_ref")
        or candidate.get("operation_id")
        or candidate.get("operationId")
    )
    method = _text(candidate.get("method") or candidate.get("http_method")).upper()
    path = _path_shape(candidate.get("path") or candidate.get("endpoint"))
    matches = operations
    if operation_hint:
        matches = [
            operation
            for operation in matches
            if operation_hint in {
                _text(operation.get("id")),
                _text(operation.get("operation_id") or operation.get("operationId")),
            }
        ]
    if method:
        matches = [
            operation
            for operation in matches
            if _text(operation.get("method")).upper() == method
        ]
    if path:
        matches = [
            operation
            for operation in matches
            if _path_shape(operation.get("path")) == path
        ]
    if not operation_hint and (not method or not path):
        return []
    return matches


def _dedupe_source_refs(*groups: list[Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in groups:
        for row in group:
            if not isinstance(row, dict):
                continue
            canonical = json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
            if canonical in seen:
                continue
            seen.add(canonical)
            output.append(dict(row))
    return output


def _planning_property(
    candidate: dict[str, Any],
    *,
    family: str,
    operation_ref: str,
) -> dict[str, Any]:
    supplied = candidate.get("property")
    property_spec = dict(supplied) if isinstance(supplied, dict) else {}
    property_spec.setdefault("template", _TEMPLATE_BY_FAMILY[family])
    property_spec["operation_ref"] = operation_ref
    intent = _text(
        candidate.get("intent")
        or candidate.get("expected_behavior")
        or candidate.get("title")
        or candidate.get("description")
    )
    if intent:
        property_spec.setdefault("source_intent", intent[:500])
    # Carry the reasoner's deep-comprehension fields (cascade_chain /
    # source_state / multi-step verification) into the obligation identity so
    # they are observable end-to-end instead of being dropped at the bridge.
    # The single-operation compiler ignores unknown property keys, so carrying
    # depth does not disturb execution and does not cross the execution-layer
    # boundary; it makes the uncompilable remainder traceable.
    depth = candidate.get("depth")
    if isinstance(depth, dict) and depth:
        property_spec.setdefault("depth", depth)
    return property_spec


def _executable_actor_for_role(
    actor_ref: str,
    actors_by_id: dict[str, dict[str, Any]],
) -> str:
    """Resolve a non-executable role actor to an executable same-role account.

    Behavior-IR actors fall into two classes: permission-matrix role actors
    (synthetic ``secret_ref:actor:*`` credential refs that can never log in)
    and runtime account actors (declared test accounts with resolvable
    credentials).  IR ``permits``/``denies`` relations reference the role
    actors, so every obligation the Reasoner bridge produces referenced
    non-executable identities and the binding gate blocked all 603 of them at
    compile time -- the single largest funnel loss.  When the referenced actor
    is non-executable, substitute the executable account actor of the same
    role (same role_key, resolvable secret, account_ref preferred, runtime
    bound preferred), mirroring the experiment compiler's implicit actor
    ranking.  The substitution is source-grounded: the account actor is the
    declared runtime incarnation of that role, never an invented identity.
    Falls back to the original ref when no executable same-role actor exists,
    so the obligation still blocks visibly downstream instead of guessing.
    """
    actor = actors_by_id.get(actor_ref)
    if not actor:
        return actor_ref
    secret_ref = _text(actor.get("credential_secret_ref") or actor.get("secret_ref"))
    if secret_ref and not secret_ref.lower().startswith("secret_ref:actor:"):
        return actor_ref  # already executable
    role_key = _text(actor.get("role_key") or actor.get("role")).lower()
    if not role_key:
        return actor_ref
    candidates = sorted(
        (
            (
                0 if _text(candidate.get("account_ref")) else 1,
                0 if candidate.get("runtime_bound") is True else 1,
                candidate_id,
            )
            for candidate_id, candidate in actors_by_id.items()
            if candidate_id != actor_ref
            and _text(candidate.get("role_key") or candidate.get("role")).lower() == role_key
            and _text(candidate.get("credential_secret_ref") or candidate.get("secret_ref"))
            and not _text(
                candidate.get("credential_secret_ref") or candidate.get("secret_ref")
            ).lower().startswith("secret_ref:actor:")
        )
    )
    return candidates[0][2] if candidates else actor_ref


def _make_grounded_obligations(
    candidate: dict[str, Any],
    *,
    candidate_id: str,
    family: str,
    operation: dict[str, Any],
    relations: list[dict[str, Any]],
    actors_by_id: dict[str, dict[str, Any]],
    states_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    operation_ref = _text(operation.get("id"))
    property_spec = _planning_property(
        candidate,
        family=family,
        operation_ref=operation_ref,
    )
    if family == "state":
        transition_relations = [
            relation
            for relation in relations
            if _text(relation.get("relation_type")) == "transitions"
        ]
        if len(transition_relations) != 1:
            return []
        transition = transition_relations[0]
        from_state_ref = _text(transition.get("from_ref"))
        to_state_ref = _text(transition.get("to_ref"))
        from_state = states_by_id.get(from_state_ref)
        to_state = states_by_id.get(to_state_ref)
        if not from_state or not to_state:
            return []
        property_spec.update({
            "from_state_ref": from_state_ref,
            "to_state_ref": to_state_ref,
            "entity_ref": _text(
                from_state.get("entity_ref") or to_state.get("entity_ref")
            ),
        })
    relation_refs = sorted({
        _text(relation.get("id"))
        for relation in relations
        if _text(relation.get("id"))
    })
    source_refs = _dedupe_source_refs(
        _list(candidate.get("source_refs")),
        _list(operation.get("source_refs")),
        *[_list(relation.get("source_refs")) for relation in relations],
    )
    operation_is_write = (
        _text(operation.get("read_write") or operation.get("side_effect_class")) == "write"
        or _text(operation.get("method")).upper() in {"POST", "PUT", "PATCH", "DELETE"}
    )
    cleanup: dict[str, Any] = {
        "required": operation_is_write,
        "mode": "reverse_order",
    }
    compensation_refs = {
        _text(relation.get("operation_ref"))
        for relation in relations
        if _text(relation.get("relation_type")) == "compensates"
        and _text(relation.get("to_ref")) == operation_ref
        and _text(relation.get("operation_ref"))
    }
    if len(compensation_refs) == 1:
        cleanup["operation_ref"] = next(iter(compensation_refs))

    actor_sets: list[list[str]] = [[]]
    if family == "authorization":
        permitted = sorted({
            _text(relation.get("actor_ref") or relation.get("from_ref"))
            for relation in relations
            if _text(relation.get("relation_type")) == "permits"
            and _text(relation.get("actor_ref") or relation.get("from_ref")) in actors_by_id
        })
        denied = sorted({
            _text(relation.get("actor_ref") or relation.get("from_ref"))
            for relation in relations
            if _text(relation.get("relation_type")) == "denies"
            and _text(relation.get("actor_ref") or relation.get("from_ref")) in actors_by_id
        })
        actor_sets = [
            [control_ref, treatment_ref]
            for control_ref in permitted
            for treatment_ref in denied
            if control_ref != treatment_ref
        ]
    elif family == "isolation":
        owned = sorted({
            _text(relation.get("actor_ref") or relation.get("from_ref"))
            for relation in relations
            if _text(relation.get("relation_type")) in {"owns", "scopes"}
            and _text(relation.get("actor_ref") or relation.get("from_ref")) in actors_by_id
        })
        actor_sets = [
            [owner_ref, viewer_ref]
            for index, owner_ref in enumerate(owned)
            for viewer_ref in owned[index + 1:]
        ]

    obligations: list[dict[str, Any]] = []
    for actor_refs in actor_sets:
        # Role actors never execute (synthetic secret_ref:actor:*); substitute
        # the executable same-role account actor so the obligation can run as
        # a real declared identity instead of blocking the whole pair.
        actor_refs = [
            _executable_actor_for_role(actor_ref, actors_by_id)
            for actor_ref in actor_refs
        ]
        if len(set(actor_refs)) != len(actor_refs):
            # Control and treatment collapsed onto one account (only one
            # executable actor for the role): no comparison is possible.
            continue
        candidate_property = dict(property_spec)
        if family == "authorization" and len(actor_refs) == 2:
            candidate_property.update({
                "control_actor_ref": actor_refs[0],
                "treatment_actor_ref": actor_refs[1],
                "require_same_resource": True,
            })
        elif family == "isolation" and len(actor_refs) == 2:
            candidate_property.update({
                "owner_actor_ref": actor_refs[0],
                "viewer_actor_ref": actor_refs[1],
                "require_ownership_evidence": True,
            })
        obligations.append(make_obligation(
            risk_family=family,
            subject_refs=[candidate_id, operation_ref, *actor_refs],
            property_spec=candidate_property,
            required_actors=actor_refs,
            required_operations=[operation_ref],
            required_observers=_OBSERVERS_BY_FAMILY[family],
            cleanup_requirement=cleanup,
            source_refs=source_refs,
            relation_refs=relation_refs,
            confidence=float(candidate.get("confidence") or candidate.get("priority") or 0.5),
        ))
    return obligations


def adapt_source_candidates_to_obligations(
    candidates: list[dict[str, Any]],
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    """Resolve source candidates through exact V2 operation relations."""

    if not isinstance(behavior_ir, dict) or _text(behavior_ir.get("schema_version")) != BEHAVIOR_IR_SCHEMA:
        raise ObligationSourceAdapterError("behavior_ir_v2_required")
    raw_operations = behavior_ir.get("operations")
    raw_relations = behavior_ir.get("relations")
    raw_actors = behavior_ir.get("actors")
    if not isinstance(raw_operations, list) or not isinstance(raw_relations, list) or not isinstance(raw_actors, list):
        raise ObligationSourceAdapterError("behavior_ir_adapter_collections_invalid")
    operations = [row for row in raw_operations if isinstance(row, dict)]
    relations = [row for row in raw_relations if isinstance(row, dict)]
    actors_by_id = {
        _text(row.get("id")): row
        for row in raw_actors
        if isinstance(row, dict) and _text(row.get("id"))
    }
    states_by_id = {
        _text(row.get("id")): row
        for row in _list(behavior_ir.get("states"))
        if isinstance(row, dict) and _text(row.get("id"))
    }
    items = [row for row in (candidates or []) if isinstance(row, dict)]
    obligations: list[dict[str, Any]] = []
    coverage_gaps: list[dict[str, Any]] = []
    depth_carried_count = 0
    depth_uncompiled_count = 0

    for candidate in items:
        _assert_source_only(candidate)
        candidate_id = _candidate_id(candidate)
        candidate_source_refs = _list(candidate.get("source_refs"))
        if not any(isinstance(row, dict) and row for row in candidate_source_refs):
            coverage_gaps.append(_stable_gap(
                candidate_id=candidate_id,
                code="BLOCKED_MISSING_SOURCE_REF",
                detail="candidate_source_refs_missing",
            ))
            continue
        matches = _operation_matches(candidate, operations)
        if not matches:
            coverage_gaps.append(_stable_gap(
                candidate_id=candidate_id,
                code="BLOCKED_MISSING_IR_RELATION",
                detail="exact_operation_join_missing",
            ))
            continue
        if len(matches) != 1:
            coverage_gaps.append(_stable_gap(
                candidate_id=candidate_id,
                code="BLOCKED_AMBIGUOUS_IR_RELATION",
                detail="exact_operation_join_ambiguous",
            ))
            continue
        operation = matches[0]
        operation_ref = _text(operation.get("id"))
        family = _risk_family(candidate)
        if family not in _RELATION_TYPES_BY_FAMILY:
            # A candidate whose family is not in the adapter's supported set
            # (an unknown or capability-gap family such as audit_trail, or a
            # candidate with no family at all) is dropped HERE, per candidate,
            # with a named reason code -- never allowed to KeyError the whole
            # adaptation loop.  A single unregistered family used to abort the
            # bridge for every engine's hypotheses at once, silently discarding
            # the entire mainline reasoner augmentation (the measured first-loss
            # stage).  The drop is visible and countable; nothing is rewritten
            # into a wrong family, so "visibly blocked beats wrongly compiled".
            coverage_gaps.append(_stable_gap(
                candidate_id=candidate_id,
                code="BLOCKED_RISK_FAMILY_UNSUPPORTED",
                detail=f"family_not_supported:{family}",
            ))
            continue
        allowed_relation_types = _RELATION_TYPES_BY_FAMILY[family]
        semantic_relations = [
            relation
            for relation in relations
            if _text(relation.get("operation_ref")) == operation_ref
            and _text(relation.get("relation_type")) in allowed_relation_types
        ]
        if not semantic_relations:
            coverage_gaps.append(_stable_gap(
                candidate_id=candidate_id,
                code="BLOCKED_MISSING_IR_RELATION",
                detail=f"required_relation_missing:{','.join(sorted(allowed_relation_types))}",
            ))
            continue
        if family == "state" and len(semantic_relations) != 1:
            coverage_gaps.append(_stable_gap(
                candidate_id=candidate_id,
                code="BLOCKED_AMBIGUOUS_IR_RELATION",
                detail="state_transition_join_ambiguous",
            ))
            continue
        compensation_relations = [
            relation
            for relation in relations
            if _text(relation.get("relation_type")) == "compensates"
            and _text(relation.get("to_ref")) == operation_ref
        ]
        joined_relations = semantic_relations + compensation_relations
        grounded = _make_grounded_obligations(
            candidate,
            candidate_id=candidate_id,
            family=family,
            operation=operation,
            relations=joined_relations,
            actors_by_id=actors_by_id,
            states_by_id=states_by_id,
        )
        if not grounded:
            coverage_gaps.append(_stable_gap(
                candidate_id=candidate_id,
                code="BLOCKED_MISSING_IR_RELATION",
                detail=f"required_actor_relation_missing:{family}",
            ))
            continue
        obligations.extend(grounded)
        # Deep comprehension that the single-operation model cannot compile
        # (a cross-entity cascade chain, a multi-step verification plan, or a
        # target entity distinct from the bound operation's subject) is
        # recorded as an explicit gap rather than silently dropped. The
        # single-operation obligation is still emitted above, so no source
        # grounding is lost; the gap names exactly what remains uncompiled.
        if isinstance(candidate.get("depth"), dict) and candidate["depth"]:
            depth_carried_count += 1
        _uncompiled = _depth_uncompiled_detail(candidate)
        if _uncompiled:
            depth_uncompiled_count += 1
            coverage_gaps.append(_stable_gap(
                candidate_id=candidate_id,
                code="BLOCKED_DEEP_COMPREHENSION_UNCOMPILED",
                detail=_uncompiled,
            ))

    deduped = dedupe_obligations(obligations)
    return {
        "schema_version": ADAPTER_SCHEMA,
        "input_count": len(items),
        "obligations": deduped,
        "coverage_gaps": coverage_gaps,
        "depth_carried_count": depth_carried_count,
        "depth_uncompiled_count": depth_uncompiled_count,
    }
