"""Versioned Behavior IR — structured executable fact model for discovery.

Schema: qualibug.behavior-ir.v2

Natural language is for explanation only. Downstream obligation/experiment
compilation must reference IR node IDs. No industry or benchmark hardcoding.
"""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Any


SCHEMA_VERSION = "qualibug.behavior-ir.v2"
V1_SCHEMA_VERSION = "qualibug.behavior-ir.v1"
ALLOWED_RELATION_TYPES = frozenset({
    "produces",
    "consumes",
    "transitions",
    "permits",
    "denies",
    "owns",
    "scopes",
    "conserves",
    "observes",
    "compensates",
    "permission_unknown",
})
_DERIVATIONS = {"explicit", "schema-derived", "runtime-observed", "model-inferred"}
_STATUSES = {"accepted", "conflicting", "unsupported", "unknown"}


class BehaviorIRError(ValueError):
    """Behavior IR is not valid for authoritative runtime compilation."""


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def normalize_relation(value: dict[str, Any]) -> dict[str, Any]:
    """Normalize one typed relation without inventing missing join targets."""

    if not isinstance(value, dict):
        raise BehaviorIRError("relation_not_object")
    relation_type = _text(value.get("relation_type"))
    if relation_type not in ALLOWED_RELATION_TYPES:
        raise BehaviorIRError(f"relation_type_invalid:{relation_type}")
    for field in ("id", "from_ref", "to_ref"):
        if not _text(value.get(field)):
            raise BehaviorIRError(f"relation_field_missing:{field}")
    return {
        **value,
        "relation_type": relation_type,
        "operation_ref": _text(value.get("operation_ref")),
        "actor_ref": _text(value.get("actor_ref")),
        "preconditions": list(value.get("preconditions") or []),
        "effects": list(value.get("effects") or []),
        "source_refs": list(value.get("source_refs") or []),
    }


def _stable_id(*parts: Any) -> str:
    raw = "|".join(_text(part) for part in parts if _text(part))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"bir_{digest}"


def _source_ref(source_id: str = "", *, version: str = "", locator: str = "", quote: str = "", kind: str = "") -> dict[str, Any]:
    quote_text = _text(quote)
    return {
        "source_id": _text(source_id) or "unknown",
        "version": _text(version),
        "locator": _text(locator),
        "kind": _text(kind),
        "quote_hash": hashlib.sha256(quote_text.encode("utf-8")).hexdigest()[:16] if quote_text else "",
    }


_METHOD_ACTIONS = {
    "GET": {"get", "read", "view", "list", "query"},
    "HEAD": {"head", "read", "view"},
    "OPTIONS": {"options", "read"},
    "POST": {"post", "create", "submit", "request", "write"},
    "PUT": {"put", "update", "modify", "write"},
    "PATCH": {"patch", "update", "modify", "write"},
    "DELETE": {"delete", "remove", "write"},
}
_UNIVERSAL_ACTIONS = {
    "*",
    "all",
    "any",
    "full_access",
    "manage",
    "administer",
}
_PERMIT_DECISIONS = {"allow", "allowed", "grant", "granted", "permit", "permitted"}
_DENY_DECISIONS = {
    "deny",
    "denied",
    "forbid",
    "forbidden",
    "not_allow",
    "not_allowed",
    "prohibit",
    "prohibited",
}


def _path_shape(value: Any) -> str:
    path = _text(value).split("?", 1)[0].strip().lower()
    if not path:
        return ""
    segments = []
    for segment in path.strip("/").split("/"):
        if not segment:
            continue
        if (
            (segment.startswith("{") and segment.endswith("}"))
            or segment.startswith(":")
            or segment == "*"
        ):
            segments.append("{}")
        else:
            segments.append(segment)
    return "/" + "/".join(segments)


def _resource_matches_operation(resource: Any, operation: dict[str, Any]) -> bool:
    resource_text = _text(resource).lower()
    operation_path = _text(operation.get("path"))
    if not resource_text or not operation_path:
        return False
    if resource_text.startswith("/"):
        return _path_shape(resource_text) == _path_shape(operation_path)
    resource_tokens = set(re.findall(r"[a-z0-9_]+", resource_text))
    operation_tokens = set(re.findall(r"[a-z0-9_]+", operation_path.lower()))
    for value in _list(operation.get("tags")) + _list(operation.get("entity_refs")):
        operation_tokens.update(re.findall(r"[a-z0-9_]+", _text(value).lower()))
    return bool(resource_tokens and resource_tokens.intersection(operation_tokens))


def _normalize_action(value: Any) -> str:
    return re.sub(r"[\s\-]+", "_", _text(value).lower())


def _actions_match_operation(actions: list[Any], operation: dict[str, Any]) -> bool:
    normalized = {_text(action).lower() for action in actions if _text(action)}
    normalized = {_normalize_action(action) for action in normalized}
    if normalized.intersection(_UNIVERSAL_ACTIONS):
        return True
    method = _text(operation.get("method")).upper()
    return bool(normalized.intersection(_METHOD_ACTIONS.get(method, {method.lower()})))


def _declared_permission_polarity(row: dict[str, Any]) -> str:
    raw_decision = _normalize_action(
        row.get("decision")
        or row.get("effect")
        or row.get("outcome")
        or row.get("access")
    )
    if row.get("allowed") is False:
        return "DENY"
    if row.get("allowed") is True:
        return "PERMIT"
    if raw_decision in _DENY_DECISIONS:
        return "DENY"
    if raw_decision in _PERMIT_DECISIONS:
        return "PERMIT"
    return ""


def _permission_row_decision(
    row: dict[str, Any],
    operation: dict[str, Any],
) -> str:
    """Return PERMIT, DENY, or UNKNOWN from explicit permission evidence.

    An omitted action is not evidence of a denial. A caller may opt into
    closed-world semantics only through an explicit policy declaration.
    """

    denied_actions = _list(
        row.get("denied_actions")
        or row.get("forbidden_actions")
        or row.get("prohibited_actions")
    )
    if denied_actions and _actions_match_operation(denied_actions, operation):
        return "DENY"

    declared_polarity = _declared_permission_polarity(row)

    actions = _list(row.get("actions"))
    if not actions and _text(row.get("action")):
        actions = [row.get("action")]

    if declared_polarity == "DENY":
        return "DENY" if not actions or _actions_match_operation(actions, operation) else "UNKNOWN"
    raw_decision = _normalize_action(
        row.get("decision")
        or row.get("effect")
        or row.get("outcome")
        or row.get("access")
    )
    if raw_decision and not declared_polarity:
        return "UNKNOWN"
    return "PERMIT" if _actions_match_operation(actions, operation) else "UNKNOWN"


def _relation_node(
    *,
    relation_type: str,
    from_ref: str,
    to_ref: str,
    operation_ref: str = "",
    actor_ref: str = "",
    preconditions: list[Any] | None = None,
    effects: list[Any] | None = None,
    source_refs: list[dict[str, Any]] | None = None,
    confidence: float = 0.8,
    derivation: str = "schema-derived",
    status: str = "accepted",
    permission_decision: str = "",
    source_relationship_ref: str = "",
) -> dict[str, Any]:
    return _fact_node(
        node_id=_stable_id("rel", relation_type, from_ref, to_ref, operation_ref, actor_ref),
        typed_fields={
            "relation_type": relation_type,
            "from_ref": from_ref,
            "to_ref": to_ref,
            "operation_ref": operation_ref,
            "actor_ref": actor_ref,
            "preconditions": list(preconditions or []),
            "effects": list(effects or []),
            "permission_decision": _text(permission_decision),
            "source_relationship_ref": _text(source_relationship_ref),
        },
        source_refs=source_refs,
        confidence=confidence,
        derivation=derivation,
        status=status,
    )


def _fact_node(
    *,
    node_id: str,
    typed_fields: dict[str, Any],
    source_refs: list[dict[str, Any]] | None = None,
    confidence: float = 0.5,
    derivation: str = "explicit",
    status: str = "accepted",
) -> dict[str, Any]:
    der = derivation if derivation in _DERIVATIONS else "model-inferred"
    st = status if status in _STATUSES else "unknown"
    conf = max(0.0, min(1.0, float(confidence)))
    return {
        "id": node_id,
        **typed_fields,
        "source_refs": list(source_refs or []),
        "confidence": conf,
        "derivation": der,
        "status": st,
    }


def _dedupe_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node in nodes:
        node_id = _text(node.get("id"))
        if not node_id or node_id in seen:
            continue
        seen.add(node_id)
        result.append(node)
    return result


def _derive_permission_relations(
    model: dict[str, Any],
    permission_rows: list[Any],
    *,
    closed_world: bool = False,
) -> list[dict[str, Any]]:
    actors_by_role: dict[str, list[dict[str, Any]]] = {}
    for actor in _list(model.get("actors")):
        if not isinstance(actor, dict):
            continue
        role_key = _text(actor.get("role_key") or actor.get("role")).lower()
        if role_key:
            actors_by_role.setdefault(role_key, []).append(actor)

    rows_by_role: dict[str, list[dict[str, Any]]] = {}
    for row in permission_rows:
        if not isinstance(row, dict):
            continue
        role_key = _text(row.get("role") or row.get("actor") or row.get("principal")).lower()
        if role_key:
            rows_by_role.setdefault(role_key, []).append(row)

    relations: list[dict[str, Any]] = []
    for role_key, actors in actors_by_role.items():
        role_rows = rows_by_role.get(role_key, [])
        if not role_rows:
            continue
        for operation in _list(model.get("operations")):
            if not isinstance(operation, dict):
                continue
            matching_rows = [
                row
                for row in role_rows
                if _resource_matches_operation(row.get("resource"), operation)
            ]
            if not matching_rows:
                continue
            row_decisions = {
                _permission_row_decision(row, operation)
                for row in matching_rows
            }
            explicit_decisions = row_decisions.intersection({"PERMIT", "DENY"})
            if len(explicit_decisions) > 1:
                permission_decision = "UNKNOWN"
                relation_type = "permission_unknown"
                relation_status = "conflicting"
            elif explicit_decisions:
                permission_decision = next(iter(explicit_decisions))
                relation_type = "permits" if permission_decision == "PERMIT" else "denies"
                relation_status = "accepted"
            elif closed_world:
                permission_decision = "DENY"
                relation_type = "denies"
                relation_status = "accepted"
            else:
                permission_decision = "UNKNOWN"
                relation_type = "permission_unknown"
                relation_status = "unknown"
            source_refs = [
                _source_ref(
                    _text(row.get("source_id")) or "permission_matrix",
                    locator=f"{role_key}->{_text(row.get('resource'))}",
                    kind="permission_matrix",
                )
                for row in matching_rows
            ]
            actions = sorted({
                _text(action)
                for row in matching_rows
                for action in _list(row.get("actions"))
                if _text(action)
            })
            for actor in actors:
                actor_ref = _text(actor.get("id"))
                operation_ref = _text(operation.get("id"))
                relations.append(_relation_node(
                    relation_type=relation_type,
                    from_ref=actor_ref,
                    to_ref=operation_ref,
                    operation_ref=operation_ref,
                    actor_ref=actor_ref,
                    effects=[{"allowed_actions": actions}],
                    source_refs=source_refs,
                    confidence=(
                        0.82
                        if permission_decision == "PERMIT"
                        else 0.8 if permission_decision == "DENY" else 0.55
                    ),
                    derivation=(
                        "schema-derived"
                        if closed_world and not explicit_decisions
                        else "explicit"
                    ),
                    status=relation_status,
                    permission_decision=permission_decision,
                ))
                owns_scope = permission_decision == "PERMIT" and any(
                    "own" in _text(row.get("scope")).lower()
                    for row in matching_rows
                )
                if owns_scope:
                    relations.append(_relation_node(
                        relation_type="owns",
                        from_ref=actor_ref,
                        to_ref=operation_ref,
                        operation_ref=operation_ref,
                        actor_ref=actor_ref,
                        preconditions=[{"scope": "own"}],
                        source_refs=source_refs,
                        confidence=0.78,
                        derivation="explicit",
                    ))
            if permission_decision == "UNKNOWN":
                gap_id = _stable_id("gap", "permission_unknown", role_key, operation.get("id"))
                model["coverage_gaps"].append(_fact_node(
                    node_id=gap_id,
                    typed_fields={
                        "gap_type": "permission_decision_unknown",
                        "description": "No explicit source fact permits or denies this role-operation pair",
                        "role_key": role_key,
                        "operation_ref": _text(operation.get("id")),
                    },
                    source_refs=source_refs,
                    confidence=1.0,
                    derivation="explicit",
                    status="unsupported" if relation_status != "conflicting" else "conflicting",
                ))
            if relation_status == "conflicting":
                model["conflicts"].append(_fact_node(
                    node_id=_stable_id("conflict", "permission", role_key, operation.get("id")),
                    typed_fields={
                        "conflict_type": "permission_decision_conflict",
                        "role_key": role_key,
                        "operation_ref": _text(operation.get("id")),
                        "decisions": sorted(explicit_decisions),
                    },
                    source_refs=source_refs,
                    confidence=1.0,
                    derivation="explicit",
                    status="conflicting",
                ))
    return relations


def _derive_compensation_relations(model: dict[str, Any]) -> list[dict[str, Any]]:
    operations = [row for row in _list(model.get("operations")) if isinstance(row, dict)]
    relations: list[dict[str, Any]] = []
    for create_operation in operations:
        if _text(create_operation.get("method")).upper() != "POST":
            continue
        create_shape = _path_shape(create_operation.get("path")).rstrip("/")
        if not create_shape or "{}" in create_shape:
            continue
        candidates: list[dict[str, Any]] = []
        for candidate in operations:
            if _text(candidate.get("method")).upper() != "DELETE":
                continue
            delete_shape = _path_shape(candidate.get("path")).rstrip("/")
            segments = delete_shape.split("/")
            if not segments or segments[-1] != "{}":
                continue
            collection_shape = "/".join(segments[:-1]).rstrip("/")
            if collection_shape == create_shape:
                candidates.append(candidate)
        if len(candidates) != 1:
            continue
        compensation = candidates[0]
        relations.append(_relation_node(
            relation_type="compensates",
            from_ref=_text(compensation.get("id")),
            to_ref=_text(create_operation.get("id")),
            operation_ref=_text(compensation.get("id")),
            effects=[{"cleanup_target_operation_ref": _text(create_operation.get("id"))}],
            source_refs=(
                list(compensation.get("source_refs") or [])
                + list(create_operation.get("source_refs") or [])
            )[:5],
            confidence=min(
                float(compensation.get("confidence") or 0.7),
                float(create_operation.get("confidence") or 0.7),
            ),
        ))
    return relations


def _derive_operation_entity_relations(model: dict[str, Any]) -> list[dict[str, Any]]:
    entities = [row for row in _list(model.get("entities")) if isinstance(row, dict)]
    by_key: dict[str, list[dict[str, Any]]] = {}
    for entity in entities:
        for key in {_text(entity.get("id")).lower(), _text(entity.get("name")).lower()}:
            if key:
                by_key.setdefault(key, []).append(entity)
    relation_type_by_method = {
        "GET": "observes",
        "HEAD": "observes",
        "OPTIONS": "observes",
        "POST": "produces",
        "DELETE": "consumes",
        "PUT": "transitions",
        "PATCH": "transitions",
    }
    relations: list[dict[str, Any]] = []
    for operation in _list(model.get("operations")):
        if not isinstance(operation, dict):
            continue
        for entity_hint in _list(operation.get("entity_refs")):
            matches = by_key.get(_text(entity_hint).lower(), [])
            if len(matches) != 1:
                continue
            entity = matches[0]
            operation_ref = _text(operation.get("id"))
            relations.append(_relation_node(
                relation_type=relation_type_by_method.get(
                    _text(operation.get("method")).upper(),
                    "observes",
                ),
                from_ref=operation_ref,
                to_ref=_text(entity.get("id")),
                operation_ref=operation_ref,
                source_refs=(
                    list(operation.get("source_refs") or [])
                    + list(entity.get("source_refs") or [])
                )[:5],
                confidence=min(
                    float(operation.get("confidence") or 0.7),
                    float(entity.get("confidence") or 0.7),
                ),
            ))
    return relations


def _resolve_operation(
    operations: list[dict[str, Any]],
    relation_source: dict[str, Any],
) -> dict[str, Any] | None:
    operation_hint = _text(
        relation_source.get("operation_ref")
        or relation_source.get("operation_id")
        or relation_source.get("operation")
    )
    method_hint = _text(relation_source.get("method")).upper()
    path_hint = _text(relation_source.get("path"))
    candidates = operations
    if operation_hint:
        candidates = [
            row
            for row in candidates
            if operation_hint in {_text(row.get("id")), _text(row.get("operation_id"))}
        ]
    if method_hint:
        candidates = [row for row in candidates if _text(row.get("method")).upper() == method_hint]
    if path_hint:
        candidates = [row for row in candidates if _path_shape(row.get("path")) == _path_shape(path_hint)]
    return candidates[0] if len(candidates) == 1 else None


def _derive_state_transition_relations(
    model: dict[str, Any],
    data: dict[str, Any],
) -> list[dict[str, Any]]:
    operations = [row for row in _list(model.get("operations")) if isinstance(row, dict)]
    states_by_key = {
        (_text(row.get("entity_ref")).lower(), _text(row.get("name")).lower()): row
        for row in _list(model.get("states"))
        if isinstance(row, dict)
    }
    relations: list[dict[str, Any]] = []
    for machine in _list(data.get("state_machines") or data.get("states")):
        if not isinstance(machine, dict):
            continue
        entity = _text(machine.get("entity") or machine.get("object") or "entity").lower()
        for transition in _list(machine.get("transitions")):
            if not isinstance(transition, dict):
                continue
            from_state = states_by_key.get((entity, _text(transition.get("from") or transition.get("from_state")).lower()))
            to_state = states_by_key.get((entity, _text(transition.get("to") or transition.get("to_state")).lower()))
            operation = _resolve_operation(operations, transition)
            if not from_state or not to_state or not operation:
                continue
            operation_ref = _text(operation.get("id"))
            relations.append(_relation_node(
                relation_type="transitions",
                from_ref=_text(from_state.get("id")),
                to_ref=_text(to_state.get("id")),
                operation_ref=operation_ref,
                preconditions=_list(transition.get("preconditions")),
                effects=_list(transition.get("effects")),
                source_refs=[_source_ref(
                    _text(transition.get("source_id")) or "state_machine",
                    locator=f"{entity}:{_text(from_state.get('name'))}->{_text(to_state.get('name'))}",
                    kind="state_transition",
                )],
                confidence=float(transition.get("confidence") or 0.8),
                derivation="explicit",
            ))
    return relations


def _invariant_relation_type(invariant: dict[str, Any]) -> str:
    kind = _text(_dict(invariant.get("expression")).get("kind")).lower()
    return "conserves" if any(
        token in kind for token in ("conserv", "balance", "amount", "quantity")
    ) else "observes"


def _derive_source_relationship_relations(
    model: dict[str, Any],
    data: dict[str, Any],
) -> list[dict[str, Any]]:
    """Resolve typed knowledge-asset edges by exact source identifiers only."""

    invariants_by_source_ref: dict[str, list[dict[str, Any]]] = {}
    for invariant in _list(model.get("invariants")):
        if not isinstance(invariant, dict):
            continue
        aliases = [_text(invariant.get("id")), *_list(invariant.get("source_rule_refs"))]
        for alias in {_text(value) for value in aliases if _text(value)}:
            invariants_by_source_ref.setdefault(alias, []).append(invariant)

    operations_by_source_ref: dict[str, list[dict[str, Any]]] = {}
    for operation in _list(model.get("operations")):
        if not isinstance(operation, dict):
            continue
        aliases = [_text(operation.get("id")), *_list(operation.get("source_operation_refs"))]
        for alias in {_text(value) for value in aliases if _text(value)}:
            operations_by_source_ref.setdefault(alias, []).append(operation)

    relations: list[dict[str, Any]] = []
    for edge in _list(data.get("relationships")):
        if not isinstance(edge, dict):
            continue
        relationship_type = _text(edge.get("relation") or edge.get("relation_type")).lower()
        if relationship_type != "rule_to_interface":
            continue
        relationship_id = _text(edge.get("edge_id") or edge.get("id")) or _stable_id(
            "source_relationship",
            relationship_type,
            edge.get("from") or edge.get("from_ref"),
            edge.get("to") or edge.get("to_ref"),
        )
        source_rule_ref = _text(edge.get("from") or edge.get("from_ref"))
        source_operation_ref = _text(edge.get("to") or edge.get("to_ref"))
        invariant_matches = invariants_by_source_ref.get(source_rule_ref, [])
        operation_matches = operations_by_source_ref.get(source_operation_ref, [])
        edge_source_ref = _source_ref(
            _text(edge.get("source_id")) or "knowledge_relationships",
            locator=relationship_id,
            kind=relationship_type,
        )
        if len(invariant_matches) != 1 or len(operation_matches) != 1:
            model["coverage_gaps"].append(_fact_node(
                node_id=_stable_id("gap", "source_relationship_unresolved", relationship_id),
                typed_fields={
                    "gap_type": "source_relationship_unresolved",
                    "description": "A typed source relationship could not be joined to exactly one IR rule and operation",
                    "relationship_id": relationship_id,
                    "relationship_type": relationship_type,
                    "source_rule_ref": source_rule_ref,
                    "source_operation_ref": source_operation_ref,
                    "invariant_match_count": len(invariant_matches),
                    "operation_match_count": len(operation_matches),
                },
                source_refs=[edge_source_ref],
                confidence=1.0,
                derivation="explicit",
                status="unsupported",
            ))
            continue

        invariant = invariant_matches[0]
        operation = operation_matches[0]
        operation_ref = _text(operation.get("id"))
        relations.append(_relation_node(
            relation_type=_invariant_relation_type(invariant),
            from_ref=operation_ref,
            to_ref=_text(invariant.get("id")),
            operation_ref=operation_ref,
            source_refs=(
                [edge_source_ref]
                + list(operation.get("source_refs") or [])
                + list(invariant.get("source_refs") or [])
            )[:5],
            confidence=min(
                float(edge.get("confidence") or 0.7),
                float(operation.get("confidence") or 0.7),
                float(invariant.get("confidence") or 0.7),
            ),
            derivation="schema-derived",
            source_relationship_ref=relationship_id,
        ))
    return relations


def _derive_invariant_relations(model: dict[str, Any]) -> list[dict[str, Any]]:
    operations = [row for row in _list(model.get("operations")) if isinstance(row, dict)]
    relations: list[dict[str, Any]] = []
    for invariant in _list(model.get("invariants")):
        if not isinstance(invariant, dict):
            continue
        relation_type = _invariant_relation_type(invariant)
        for hint in _list(invariant.get("operation_refs")):
            operation = _resolve_operation(operations, {"operation_ref": hint})
            if not operation:
                continue
            operation_ref = _text(operation.get("id"))
            relations.append(_relation_node(
                relation_type=relation_type,
                from_ref=operation_ref,
                to_ref=_text(invariant.get("id")),
                operation_ref=operation_ref,
                source_refs=(
                    list(operation.get("source_refs") or [])
                    + list(invariant.get("source_refs") or [])
                )[:5],
                confidence=min(
                    float(operation.get("confidence") or 0.7),
                    float(invariant.get("confidence") or 0.7),
                ),
                derivation="explicit",
            ))
    return relations


def empty_behavior_ir(*, project_id: str = "", source_snapshot_hash: str = "") -> dict[str, Any]:
    model = {
        "schema_version": SCHEMA_VERSION,
        "project_id": _text(project_id) or "opaque-project-id",
        "source_snapshot_hash": _text(source_snapshot_hash),
        "sources": [],
        "entities": [],
        "operations": [],
        "actors": [],
        "states": [],
        "relations": [],
        "invariants": [],
        "observation_surfaces": [],
        "capabilities": [],
        "conflicts": [],
        "coverage_gaps": [],
    }
    model["model_id"] = _content_addressed_id(model)
    return model


def _content_addressed_id(model: dict[str, Any]) -> str:
    payload = {k: v for k, v in model.items() if k != "model_id"}
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return f"bir_model_{hashlib.sha256(blob.encode('utf-8')).hexdigest()[:24]}"


def validate_behavior_ir(
    model: dict[str, Any],
    *,
    require_explicit_relations: bool = True,
) -> list[str]:
    errors: list[str] = []
    if _text(model.get("schema_version")) != SCHEMA_VERSION:
        errors.append("schema_version_mismatch")
    for collection in (
        "sources", "entities", "operations", "actors", "states", "relations",
        "invariants", "observation_surfaces", "capabilities", "conflicts", "coverage_gaps",
    ):
        if not isinstance(model.get(collection), list):
            errors.append(f"missing_collection:{collection}")
            continue
        for item in model[collection]:
            if not isinstance(item, dict) or not _text(item.get("id")):
                errors.append(f"invalid_node:{collection}")
                continue
            if _text(item.get("derivation")) and _text(item.get("derivation")) not in _DERIVATIONS:
                errors.append(f"bad_derivation:{item.get('id')}")
            if _text(item.get("status")) and _text(item.get("status")) not in _STATUSES:
                errors.append(f"bad_status:{item.get('id')}")
            if "ground_truth" in json.dumps(item, ensure_ascii=False, default=str).lower():
                errors.append(f"forbidden_ground_truth_ref:{item.get('id')}")
            if collection == "relations":
                try:
                    normalize_relation(item)
                except BehaviorIRError as exc:
                    errors.append(str(exc))
                    continue
                if require_explicit_relations:
                    for field in (
                        "operation_ref",
                        "actor_ref",
                        "preconditions",
                        "effects",
                        "source_refs",
                    ):
                        if field not in item:
                            errors.append(f"relation_field_missing:{field}:{item.get('id')}")
    return errors


def migrate_behavior_ir_v1_to_v2(value: dict[str, Any]) -> dict[str, Any]:
    """Explicitly migrate a persisted V1 diagnostic artifact to V2 shape."""

    if not isinstance(value, dict) or _text(value.get("schema_version")) != V1_SCHEMA_VERSION:
        raise BehaviorIRError("behavior_ir_v1_required")
    migrated = deepcopy(value)
    migrated["schema_version"] = SCHEMA_VERSION
    migrated["relations"] = [normalize_relation(row) for row in _list(value.get("relations"))]
    errors = validate_behavior_ir(migrated, require_explicit_relations=True)
    if errors:
        raise BehaviorIRError("behavior_ir_v2_invalid:" + ",".join(errors))
    migrated["model_id"] = _content_addressed_id(migrated)
    return migrated


def build_behavior_ir_from_knowledge_asset(
    asset: dict[str, Any] | None,
    *,
    project_id: str = "",
    source_snapshot_hash: str = "",
    api_operations: list[dict[str, Any]] | None = None,
    runtime_actors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build Behavior IR from enterprise knowledge asset + optional OpenAPI ops.

    Fully generic: binds only to structured fields present in the asset.
    """
    model = empty_behavior_ir(project_id=project_id, source_snapshot_hash=source_snapshot_hash)
    data = _dict(asset)
    if not data and not api_operations and not runtime_actors:
        model["coverage_gaps"].append(_fact_node(
            node_id=_stable_id("gap", "no_sources"),
            typed_fields={"gap_type": "missing_sources", "description": "No knowledge asset or operations available"},
            confidence=1.0,
            derivation="explicit",
            status="unsupported",
        ))
        model["model_id"] = _content_addressed_id(model)
        return model

    # Sources
    for src in _list(data.get("sources") or data.get("source_inventory")):
        if not isinstance(src, dict):
            continue
        sid = _text(src.get("source_id") or src.get("id")) or _stable_id("src", src.get("filename"))
        model["sources"].append(_fact_node(
            node_id=sid if sid.startswith("bir_") else _stable_id("src", sid),
            typed_fields={
                "name": _text(src.get("filename") or src.get("original_name") or src.get("name") or sid),
                "source_type": _text(src.get("source_type") or src.get("type")),
                "hash": _text(src.get("text_hash") or src.get("content_hash") or src.get("hash")),
            },
            source_refs=[_source_ref(sid)],
            confidence=0.9,
            derivation="explicit",
        ))

    # Operations from OpenAPI / asset interfaces
    seen_ops: set[str] = set()
    for op in list(api_operations or []) + _list(data.get("operations") or data.get("interfaces")):
        if not isinstance(op, dict):
            continue
        method = _text(op.get("method") or op.get("http_method") or "GET").upper()
        path = _text(op.get("path") or op.get("endpoint") or op.get("url"))
        if not path:
            continue
        op_id = _text(op.get("operation_id") or op.get("operationId") or op.get("id")) or _stable_id("op", method, path)
        if op_id in seen_ops:
            continue
        seen_ops.add(op_id)
        side_effect = "write" if method in {"POST", "PUT", "PATCH", "DELETE"} else "read"
        model["operations"].append(_fact_node(
            node_id=op_id if op_id.startswith("bir_") else _stable_id("op", method, path),
            typed_fields={
                "operation_id": op_id,
                "method": method,
                "path": path,
                "request_schema": _dict(op.get("request_schema") or op.get("requestBody")),
                "response_schema": _dict(op.get("response_schema") or op.get("responses")),
                "parameters": _list(op.get("parameters")),
                "security": _list(op.get("security")),
                "summary": _text(op.get("summary") or op.get("title")),
                "description": _text(op.get("description")),
                "tags": _list(op.get("tags")),
                "side_effect_class": _text(op.get("side_effect_class") or side_effect),
                "read_write": side_effect,
                "entity_refs": [_text(x) for x in _list(op.get("entity_refs")) if _text(x)],
                "examples": _list(op.get("examples")),
                "source_operation_refs": list(dict.fromkeys(
                    _text(value)
                    for value in (
                        op.get("interface_id"),
                        op.get("operation_id"),
                        op.get("operationId"),
                        op.get("id"),
                    )
                    if _text(value)
                )),
            },
            source_refs=[_source_ref(_text(op.get("source_id")) or "api_spec", locator=f"{method} {path}", kind="api_operation")],
            confidence=0.85 if op.get("operation_id") else 0.7,
            derivation="schema-derived" if not op.get("operation_id") else "explicit",
        ))

    # Entities from every structured asset vocabulary. The knowledge asset
    # exposes business_objects/data_tables, while other callers may use the
    # shorter objects/entities/tables aliases; merge them instead of choosing
    # only the first non-empty collection.
    entity_rows: list[Any] = []
    for key in ("objects", "entities", "tables", "business_objects", "data_tables"):
        entity_rows.extend(_list(data.get(key)))
    seen_entities: set[str] = set()
    for ent in entity_rows:
        if isinstance(ent, str):
            name = _text(ent)
            if not name or name.lower() in seen_entities:
                continue
            seen_entities.add(name.lower())
            model["entities"].append(_fact_node(
                node_id=_stable_id("ent", name),
                typed_fields={"name": name, "kind": "resource"},
                confidence=0.6,
                derivation="schema-derived",
            ))
            continue
        if not isinstance(ent, dict):
            continue
        name = _text(ent.get("name") or ent.get("object") or ent.get("table") or ent.get("entity"))
        if not name or name.lower() in seen_entities:
            continue
        seen_entities.add(name.lower())
        model["entities"].append(_fact_node(
            node_id=_text(ent.get("entity_id") or ent.get("id")) or _stable_id("ent", name),
            typed_fields={
                "name": name,
                "kind": _text(ent.get("kind") or "resource"),
                "fields": _list(ent.get("fields") or ent.get("columns")),
            },
            source_refs=[_source_ref(_text(ent.get("source_id")), locator=name)],
            confidence=float(ent.get("confidence") or 0.7),
            derivation="explicit",
        ))

    # Actors from permission matrix / roles / runtime actors (secret_ref only)
    actor_names: set[str] = set()
    actor_ids: set[str] = set()
    permission_rows = _list(data.get("permission_matrix") or data.get("permissions"))
    permission_by_role: dict[str, dict[str, Any]] = {}
    for perm in permission_rows:
        if not isinstance(perm, dict):
            continue
        role = _text(perm.get("role") or perm.get("actor") or perm.get("principal"))
        if not role:
            continue
        role_key = role.lower()
        aggregate = permission_by_role.setdefault(role_key, {
            "role": role,
            "resources": [],
            "actions": [],
            "denied_actions": [],
            "scopes": [],
            "source_ids": [],
        })
        declared_polarity = _declared_permission_polarity(perm)
        resource = _text(perm.get("resource"))
        if declared_polarity != "DENY" and resource and resource not in aggregate["resources"]:
            aggregate["resources"].append(resource)
        declared_actions = _list(perm.get("actions"))
        for action in declared_actions if declared_polarity != "DENY" else []:
            value = _text(action)
            if value and value not in aggregate["actions"]:
                aggregate["actions"].append(value)
        denied_actions = (
            declared_actions if declared_polarity == "DENY" else []
        ) + _list(
            perm.get("denied_actions")
            or perm.get("forbidden_actions")
            or perm.get("prohibited_actions")
        )
        for action in denied_actions:
            value = _text(action)
            if value and value not in aggregate["denied_actions"]:
                aggregate["denied_actions"].append(value)
        scope = _text(perm.get("scope"))
        if scope and scope not in aggregate["scopes"]:
            aggregate["scopes"].append(scope)
        source_id = _text(perm.get("source_id"))
        if source_id and source_id not in aggregate["source_ids"]:
            aggregate["source_ids"].append(source_id)
    for role_key, aggregate in permission_by_role.items():
        role = _text(aggregate.get("role") or role_key)
        actor_names.add(role_key)
        actor_id = _stable_id("actor", role)
        actor_ids.add(actor_id)
        model["actors"].append(_fact_node(
            node_id=actor_id,
            typed_fields={
                "role": role,
                "role_key": role_key,
                "tenant_scope": ",".join(aggregate["scopes"]) or "unspecified",
                "credential_secret_ref": f"secret_ref:actor:{role}",
                "account_status": "active",
                "allowed_resources": aggregate["resources"],
                "allowed_actions": aggregate["actions"],
                "denied_actions": aggregate["denied_actions"],
            },
            source_refs=[
                _source_ref(source_id or "permission_matrix", locator=role, kind="permission_matrix")
                for source_id in (aggregate["source_ids"] or [""])
            ],
            confidence=0.8,
            derivation="explicit",
        ))
    for declared_role in _list(data.get("roles")):
        if not isinstance(declared_role, dict):
            continue
        role = _text(declared_role.get("role") or declared_role.get("name") or declared_role.get("id"))
        role_key = role.lower()
        if not role or role_key in actor_names:
            continue
        actor_names.add(role_key)
        actor_id = _stable_id("actor", role)
        actor_ids.add(actor_id)
        model["actors"].append(_fact_node(
            node_id=actor_id,
            typed_fields={
                "role": role,
                "role_key": role_key,
                "tenant_scope": _text(declared_role.get("scope") or "unspecified"),
                "credential_secret_ref": f"secret_ref:actor:{role}",
                "account_status": "active",
                "allowed_resources": [],
                "allowed_actions": [],
                "denied_actions": [],
            },
            source_refs=[_source_ref(_text(declared_role.get("source_id")) or "roles", locator=role, kind="role_catalog")],
            confidence=float(declared_role.get("confidence") or 0.75),
            derivation="explicit",
        ))
    for actor in _list(runtime_actors):
        if not isinstance(actor, dict):
            continue
        role = _text(actor.get("role") or actor.get("name") or actor.get("id"))
        if not role:
            continue
        role_key = role.lower()
        aggregate = permission_by_role.get(role_key, {})
        if role_key not in actor_names:
            actor_names.add(role_key)
            actor_id = _stable_id("actor", role)
            actor_ids.add(actor_id)
            source_refs = [
                _source_ref(source_id or "permission_matrix", locator=role, kind="permission_matrix")
                for source_id in (aggregate.get("source_ids") or [])
            ]
            source_refs.append(_source_ref("runtime_actors", locator=role, kind="runtime_actor"))
            model["actors"].append(_fact_node(
                node_id=actor_id,
                typed_fields={
                    "role": role,
                    "role_key": role_key,
                    "tenant_scope": _text(actor.get("tenant") or actor.get("scope") or "unspecified"),
                    "credential_secret_ref": _text(actor.get("secret_ref") or f"secret_ref:actor:{role}"),
                    "account_status": _text(actor.get("status") or "active"),
                    "allowed_resources": list(aggregate.get("resources") or []),
                    "allowed_actions": list(aggregate.get("actions") or []),
                    "denied_actions": list(aggregate.get("denied_actions") or []),
                    "runtime_bound": True,
                },
                source_refs=source_refs,
                confidence=0.9,
                derivation="runtime-observed",
            ))
        account_ref = _text(actor.get("account_ref") or actor.get("email") or actor.get("username") or actor.get("id"))
        if not account_ref:
            continue
        account_id = _stable_id("actor_account", account_ref)
        if account_id in actor_ids:
            continue
        actor_ids.add(account_id)
        source_refs = [
            _source_ref(source_id or "permission_matrix", locator=role, kind="permission_matrix")
            for source_id in (aggregate.get("source_ids") or [])
        ]
        source_refs.append(_source_ref("runtime_actors", locator=f"{role}:{account_ref}", kind="runtime_actor"))
        model["actors"].append(_fact_node(
            node_id=account_id,
            typed_fields={
                "role": role,
                "role_key": role_key,
                "account_ref": account_ref,
                "tenant_scope": _text(actor.get("tenant") or actor.get("scope") or "unspecified"),
                "credential_secret_ref": _text(actor.get("secret_ref") or f"secret_ref:test_accounts:{account_ref}"),
                "account_status": _text(actor.get("status") or "active"),
                "allowed_resources": list(aggregate.get("resources") or []),
                "allowed_actions": list(aggregate.get("actions") or []),
                "denied_actions": list(aggregate.get("denied_actions") or []),
                "runtime_bound": True,
            },
            source_refs=source_refs,
            confidence=0.9,
            derivation="runtime-observed",
        ))

    # States from state machines
    for sm in _list(data.get("state_machines") or data.get("states")):
        if not isinstance(sm, dict):
            continue
        entity = _text(sm.get("entity") or sm.get("object") or "entity")
        for state_name in _list(sm.get("states") or ([sm.get("name")] if sm.get("name") else [])):
            name = _text(state_name)
            if not name:
                continue
            model["states"].append(_fact_node(
                node_id=_stable_id("state", entity, name),
                typed_fields={"entity_ref": entity, "name": name},
                source_refs=[_source_ref(_text(sm.get("source_id")) or "state_machine", locator=f"{entity}:{name}")],
                confidence=0.75,
                derivation="explicit",
            ))

    # Invariants from rule library (typed expression + description)
    for rule in _list(data.get("rule_library") or data.get("rules")):
        if not isinstance(rule, dict):
            continue
        statement = _text(rule.get("statement") or rule.get("expression") or rule.get("title"))
        if not statement:
            continue
        rid = _text(rule.get("rule_id") or rule.get("id")) or _stable_id("inv", statement)
        model["invariants"].append(_fact_node(
            node_id=rid if rid.startswith("bir_") else _stable_id("inv", rid),
            typed_fields={
                "description": statement,
                "expression": {
                    "kind": _text(rule.get("kind") or rule.get("risk_type") or "business_rule"),
                    "operator": _text(rule.get("operator") or "must_hold"),
                    "operands": _list(rule.get("operands")),
                    "raw": statement,
                },
                "operation_refs": [
                    _text(value)
                    for value in _list(
                        rule.get("operation_refs")
                        or ([rule.get("operation_ref") or rule.get("operation_id")]
                            if rule.get("operation_ref") or rule.get("operation_id") else [])
                    )
                    if _text(value)
                ],
                "source_rule_refs": list(dict.fromkeys(
                    _text(value)
                    for value in (rule.get("rule_id"), rule.get("id"))
                    if _text(value)
                )),
            },
            source_refs=[_source_ref(_text(rule.get("source_id")) or "rule_library", quote=statement[:200])],
            confidence=float(rule.get("confidence") or 0.7),
            derivation="explicit",
        ))

    # Runtime V2 relations are the only semantic joins used by the compiler.
    permission_policy_mode = _text(
        data.get("permission_policy_mode")
        or data.get("permission_matrix_mode")
    ).lower()
    permission_matrix_complete = bool(data.get("permission_matrix_complete")) or permission_policy_mode in {
        "closed_world",
        "complete",
    }
    model["relations"].extend(_derive_permission_relations(
        model,
        permission_rows,
        closed_world=permission_matrix_complete,
    ))
    model["relations"].extend(_derive_operation_entity_relations(model))
    model["relations"].extend(_derive_state_transition_relations(model, data))
    model["relations"].extend(_derive_source_relationship_relations(model, data))
    model["relations"].extend(_derive_invariant_relations(model))
    model["relations"].extend(_derive_compensation_relations(model))

    # Default observation surfaces based on available capabilities
    surfaces = [("http_api", "HTTP/API"), ("ui_browser", "Browser/UI"), ("db_snapshot", "DB read snapshot")]
    for surface_id, label in surfaces:
        model["observation_surfaces"].append(_fact_node(
            node_id=_stable_id("surface", surface_id),
            typed_fields={"surface": surface_id, "label": label, "available": surface_id == "http_api"},
            confidence=1.0 if surface_id == "http_api" else 0.3,
            derivation="schema-derived",
            status="accepted" if surface_id == "http_api" else "unknown",
        ))
    model["capabilities"].append(_fact_node(
        node_id=_stable_id("cap", "http_execute"),
        typed_fields={"capability": "http_execute", "adapter": "http_api"},
        confidence=1.0,
        derivation="schema-derived",
    ))

    if not model["operations"]:
        model["coverage_gaps"].append(_fact_node(
            node_id=_stable_id("gap", "no_operations"),
            typed_fields={"gap_type": "missing_operations", "description": "No operations derived from sources"},
            confidence=1.0,
            derivation="explicit",
            status="unsupported",
        ))
    if not model["actors"]:
        model["coverage_gaps"].append(_fact_node(
            node_id=_stable_id("gap", "no_actors"),
            typed_fields={"gap_type": "missing_actors", "description": "No actors/roles derived from sources"},
            confidence=1.0,
            derivation="explicit",
            status="unsupported",
        ))

    model["relations"] = [normalize_relation(row) for row in _dedupe_nodes(model["relations"])]
    model["coverage_gaps"] = _dedupe_nodes(model["coverage_gaps"])
    model["conflicts"] = _dedupe_nodes(model["conflicts"])
    model["model_id"] = _content_addressed_id(model)
    return model


def behavior_ir_summary(model: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": _text(model.get("schema_version")),
        "model_id": _text(model.get("model_id")),
        "project_id": _text(model.get("project_id")),
        "counts": {
            key: len(_list(model.get(key)))
            for key in (
                "sources", "entities", "operations", "actors", "states",
                "relations", "invariants", "observation_surfaces", "capabilities",
                "conflicts", "coverage_gaps",
            )
        },
        "validation_errors": validate_behavior_ir(model),
    }
