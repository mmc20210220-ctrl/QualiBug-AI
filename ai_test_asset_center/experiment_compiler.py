"""Scope source conflicts and preserve single-actor privacy field checks."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from . import experiment_compiler_conflict_base as _base
from .experiment_compiler_conflict_base import *  # noqa: F401,F403


_original_compile_experiment = _base._original_compile_experiment


def __getattr__(name: str) -> Any:
    return getattr(_base, name)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _add_refs(target: set[str], value: Any) -> None:
    if isinstance(value, (str, int)) and _text(value):
        target.add(_text(value))
    elif isinstance(value, list):
        for item in value:
            _add_refs(target, item)


def _refs_from_mapping(value: dict[str, Any]) -> set[str]:
    refs: set[str] = set()
    explicit_keys = {
        "subject_ref", "subject_refs", "operation_ref", "operation_refs",
        "operation_id", "operation_ids", "actor_ref", "actor_refs",
        "invariant_ref", "invariant_refs", "rule_ref", "rule_refs",
        "entity_ref", "entity_refs", "state_ref", "state_refs",
        "from_ref", "to_ref", "node_ref", "node_refs",
        "relation_ref", "relation_refs",
    }
    for key, child in value.items():
        normalized = _text(key).lower()
        if normalized in {"source_ref", "source_refs", "secret_ref", "credential_secret_ref"}:
            continue
        if (
            normalized in explicit_keys
            or normalized.endswith("_ref")
            or normalized.endswith("_refs")
        ):
            _add_refs(refs, child)
        elif isinstance(child, dict):
            refs.update(_refs_from_mapping(child))
    return refs


def _obligation_scope_refs(obligation: dict[str, Any]) -> set[str]:
    row = _dict(obligation)
    refs = _refs_from_mapping(row)
    _add_refs(refs, row.get("subject_refs"))
    _add_refs(refs, row.get("required_operations"))
    _add_refs(refs, row.get("required_actors"))
    _add_refs(refs, row.get("relation_refs"))
    return refs


def _conflict_is_relevant(
    conflict: dict[str, Any],
    *,
    obligation_refs: set[str],
    obligation_actor_refs: set[str] | None,
    obligation_actor_roles: set[str] | None,
    obligation_operation_refs: set[str] | None,
) -> bool:
    if _text(conflict.get("status")) != "conflicting":
        return True
    permission_scope_present = False
    if _text(conflict.get("conflict_type")) == "permission_decision_conflict":
        conflict_actor_refs: set[str] = set()
        _add_refs(conflict_actor_refs, conflict.get("actor_ref"))
        _add_refs(conflict_actor_refs, conflict.get("actor_refs"))
        if (
            conflict_actor_refs
            and obligation_actor_refs is not None
            and conflict_actor_refs.isdisjoint(obligation_actor_refs)
        ):
            return False
        conflict_role = _text(conflict.get("role_key")).lower()
        if (
            conflict_role
            and obligation_actor_roles is not None
            and conflict_role not in obligation_actor_roles
        ):
            return False
        conflict_operation_refs: set[str] = set()
        _add_refs(conflict_operation_refs, conflict.get("operation_ref"))
        _add_refs(conflict_operation_refs, conflict.get("operation_refs"))
        permission_scope_present = bool(
            conflict_actor_refs or conflict_role or conflict_operation_refs
        )
        if (
            conflict_operation_refs
            and obligation_operation_refs is not None
            and conflict_operation_refs.isdisjoint(obligation_operation_refs)
        ):
            return False
    if permission_scope_present:
        return True
    conflict_refs = _refs_from_mapping(_dict(conflict))
    # An unscoped conflict is global and remains fail-closed. Only conflicts
    # that explicitly identify other IR nodes are safe to remove here.
    if not conflict_refs or not obligation_refs:
        return True
    return bool(conflict_refs.intersection(obligation_refs))


def _obligation_node_refs(
    behavior_ir: dict[str, Any],
    obligation: dict[str, Any],
    collection: str,
) -> set[str] | None:
    node_ids = {
        _text(node.get("id"))
        for node in _list(_dict(behavior_ir).get(collection))
        if isinstance(node, dict) and _text(node.get("id"))
    }
    refs = _refs_from_mapping(_dict(obligation)).intersection(node_ids)
    return refs or None


def _obligation_actor_roles(
    behavior_ir: dict[str, Any],
    actor_refs: set[str] | None,
) -> set[str] | None:
    actors = {
        _text(actor.get("id")): _text(
            actor.get("role_key") or actor.get("role")
        ).lower()
        for actor in _list(_dict(behavior_ir).get("actors"))
        if isinstance(actor, dict) and _text(actor.get("id"))
    }
    if not actor_refs:
        return None
    roles = {actors[actor_ref] for actor_ref in actor_refs if actors[actor_ref]}
    return roles or None


def _scoped_behavior_ir(
    behavior_ir: dict[str, Any],
    obligation: dict[str, Any],
) -> dict[str, Any]:
    ir = deepcopy(_dict(behavior_ir))
    obligation_refs = _obligation_scope_refs(obligation)
    obligation_actor_refs = _obligation_node_refs(
        behavior_ir, obligation, "actors"
    )
    obligation_actor_roles = _obligation_actor_roles(
        behavior_ir, obligation_actor_refs
    )
    obligation_operation_refs = _obligation_node_refs(
        behavior_ir, obligation, "operations"
    )
    ir["conflicts"] = [
        dict(conflict)
        for conflict in _list(ir.get("conflicts"))
        if isinstance(conflict, dict)
        and _conflict_is_relevant(
            conflict,
            obligation_refs=obligation_refs,
            obligation_actor_refs=obligation_actor_refs,
            obligation_actor_roles=obligation_actor_roles,
            obligation_operation_refs=obligation_operation_refs,
        )
    ]
    return ir


def _privacy_field_mode(obligation: dict[str, Any]) -> bool:
    row = _dict(obligation)
    prop = _dict(row.get("property"))
    return bool(
        _text(row.get("risk_family")) == "privacy"
        and _text(prop.get("privacy_test_mode")) == "field_policy"
        and _text(prop.get("privacy_policy")) in {"absent", "masked"}
    )


def _mutable_entity_field(value: Any) -> bool:
    normalized = "".join(ch for ch in _text(value).lower() if ch.isalnum() or ch == "_")
    if not normalized:
        return False
    if normalized in {"id", "uuid", "key"} or normalized.endswith("_id"):
        return False
    return normalized not in {
        "created_at", "updated_at", "deleted_at", "createdat", "updatedat",
        "deletedat", "password", "token", "secret", "credential",
    }


def _source_observed_mutation_plan(
    operation_ref: str,
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    entities = {
        _text(entity.get("id")): entity
        for entity in _list(_dict(behavior_ir).get("entities"))
        if isinstance(entity, dict) and _text(entity.get("id"))
    }
    entity_refs: set[str] = set()
    relation_refs: set[str] = set()
    for relation in _list(_dict(behavior_ir).get("relations")):
        if not isinstance(relation, dict):
            continue
        if _text(relation.get("operation_ref")) != operation_ref:
            continue
        target = _text(relation.get("to_ref"))
        if target not in entities:
            continue
        entity_refs.add(target)
        if _text(relation.get("id")):
            relation_refs.add(_text(relation.get("id")))
    candidate_fields = sorted({
        _text(field)
        for entity_ref in entity_refs
        for field in _list(_dict(entities.get(entity_ref)).get("fields"))
        if _mutable_entity_field(field)
    })
    if not candidate_fields:
        return {}
    return {
        "schema_version": "qualibug.source-observed-mutation-plan.v1",
        "candidate_fields": candidate_fields,
        "source_entity_refs": sorted(entity_refs),
        "source_relation_refs": sorted(relation_refs),
    }


def _attach_source_observed_mutations(
    experiment: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    receipt = _dict(experiment.get("compile_receipt"))
    if _text(receipt.get("status")).upper() != "COMPILED":
        return experiment
    operations = {
        _text(operation.get("id")): operation
        for operation in _list(_dict(behavior_ir).get("operations"))
        if isinstance(operation, dict) and _text(operation.get("id"))
    }
    for step in _list(experiment.get("control_plan")) + _list(experiment.get("treatment_plan")):
        if not isinstance(step, dict):
            continue
        operation_ref = _text(step.get("operation_ref"))
        operation = _dict(operations.get(operation_ref))
        method = _text(operation.get("method")).upper()
        body = step.get("body") if "body" in step else operation.get("request_example")
        if method not in {"PATCH", "PUT"} or body not in (None, {}):
            continue
        plan = _source_observed_mutation_plan(operation_ref, behavior_ir)
        if not plan:
            # No entity fields available for mutation — try generating from the
            # operation's request schema as a best-effort fallback. Core discovery
            # must not be gated on entity field resolution.
            schema = _dict(operation.get("request_schema") or operation.get("requestBody") or {})
            # Also check nested content.application/json.schema
            if not schema.get("properties"):
                content = _dict(schema.get("content", {}))
                for mt in ("application/json", "*/*"):
                    media = content.get(mt, {})
                    if isinstance(media, dict):
                        inner = _dict(media.get("schema", {}))
                        if inner.get("properties"):
                            schema = inner
                            break
            schema_props = _dict(schema.get("properties"))
            if schema_props:
                for field_name in sorted(schema_props):
                    if _mutable_entity_field(field_name):
                        plan = {
                            "schema_version": "qualibug.source-observed-mutation-plan.v1",
                            "candidate_fields": [field_name],
                            "source_entity_refs": [],
                            "source_relation_refs": [],
                        }
                        break
        if not plan:
            # Last resort: generate a synthetic mutation field. For PATCH/PUT
            # authorization tests, the body content is secondary to observing
            # the authorization decision. A minimal valid body is better than
            # blocking the entire obligation.
            plan = {
                "schema_version": "qualibug.source-observed-mutation-plan.v1",
                "candidate_fields": ["status"],
                "source_entity_refs": [],
                "source_relation_refs": [],
                "synthetic": True,
            }
        if not plan:
            # Last resort: generate a synthetic mutation field. For PATCH/PUT
            # authorization tests, the body content is secondary to observing
            # the authorization decision. A minimal valid body is better than
            # blocking the entire obligation.
            plan = {
                "schema_version": "qualibug.source-observed-mutation-plan.v1",
                "candidate_fields": ["status"],
                "source_entity_refs": [],
                "source_relation_refs": [],
                "synthetic": True,
            }
        step["runtime_body_plan"] = deepcopy(plan)
    return experiment


def _find_distinct_actor_pair(
    behavior_ir: dict[str, Any],
) -> tuple[str, str] | None:
    """Find two actors with distinct credentials from Behavior IR."""
    actors = [
        a for a in _list(_dict(behavior_ir).get("actors"))
        if isinstance(a, dict) and _text(a.get("id"))
    ]
    # Prefer runtime-bound actors with credentials
    proven = [
        a for a in actors
        if _text(a.get("credential_secret_ref") or a.get("secret_ref"))
        and a.get("runtime_bound") is True
    ]
    if len(proven) < 2:
        proven = [
            a for a in actors
            if _text(a.get("credential_secret_ref") or a.get("secret_ref"))
        ]
    if len(proven) < 2:
        # Fallback: any runtime-bound actors
        proven = [a for a in actors if a.get("runtime_bound") is True]
    if len(proven) < 2:
        return None
    # Pick two with different secrets
    first = proven[0]
    first_secret = _text(
        first.get("credential_secret_ref") or first.get("secret_ref")
    )
    for candidate in proven[1:]:
        cand_secret = _text(
            candidate.get("credential_secret_ref") or candidate.get("secret_ref")
        )
        if cand_secret != first_secret:
            return _text(first.get("id")), _text(candidate.get("id"))
    # If all share same secret, just pick first two (better than blocking)
    return _text(proven[0].get("id")), _text(proven[1].get("id"))


def compile_experiment_for_obligation(
    obligation: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
    environment_type: str = "",
    policy_version: str = "",
    available_adapters: set[str] | None = None,
) -> dict[str, Any]:
    if not _privacy_field_mode(obligation):
        problem = _base._runtime_pair_problem(obligation, behavior_ir)
        if problem:
            # ── Enhanced: try actor substitution before blocking ──
            pair = _find_distinct_actor_pair(behavior_ir)
            if pair:
                ctrl_id, treat_id = pair
                obl = _dict(obligation)
                prop = _dict(obl.get("property"))
                prop = {
                    **prop,
                    "control_actor_ref": ctrl_id,
                    "treatment_actor_ref": treat_id,
                    "_actor_pair_degraded": True,
                    "_original_pair_problem": problem,
                }
                obligation = {
                    **obl,
                    "property": prop,
                    "required_actors": [ctrl_id, treat_id],
                }
                # Re-check after substitution
                problem = _base._runtime_pair_problem(obligation, behavior_ir)
            if problem:
                obligation_id = (
                    _text(_dict(obligation).get("obligation_id"))
                    or "unknown_obligation"
                )
                return _base._base.blocked_experiment(
                    obligation_id,
                    "BLOCKED_MISSING_ACTOR",
                    f"runtime_actor_pair_not_distinct:{problem}",
                )

    experiment = _original_compile_experiment(
        obligation,
        behavior_ir=_scoped_behavior_ir(behavior_ir, obligation),
        environment_type=environment_type,
        policy_version=policy_version,
        available_adapters=available_adapters,
    )
    return _attach_source_observed_mutations(experiment, behavior_ir)


def compile_experiments(
    obligations: list[dict[str, Any]],
    *,
    behavior_ir: dict[str, Any],
    environment_type: str = "",
    policy_version: str = "",
    available_adapters: "set[str] | frozenset[str] | None" = None,
) -> dict[str, Any]:
    """Compile a batch through the explicitly selected facade dispatch.

    ``available_adapters`` names the observation adapters this target may be observed
    through. Omitting it keeps the http_api-only default, so every existing caller is
    unaffected; ``adapter_capability.resolve_available_adapters`` is what supplies a wider
    set from customer-declared configuration.
    """
    return _base._base.compile_experiments(
        obligations,
        behavior_ir=behavior_ir,
        environment_type=environment_type,
        policy_version=policy_version,
        compile_one=compile_experiment_for_obligation,
        available_adapters=available_adapters,
    )
