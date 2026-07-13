"""Compile TestObligations into ExecutableExperiments or explicit blocks."""
from __future__ import annotations

import re
import hashlib
from typing import Any

from .experiment_protocols import compile_family_protocol
from .observer_contracts_base import compile_observer_requirements
from .runtime_binding_graph import (
    build_binding_plan,
    declared_action_compensators,
    declared_effect_observers,
    unresolved_placeholders,
)
from .real_id_resolver import normalize_path_placeholders
from .validation_obligation_expander import expand_validation_obligation


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _index_by_id(nodes: list[Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for node in nodes:
        if isinstance(node, dict) and _text(node.get("id")):
            out[_text(node.get("id"))] = node
    return out


def _is_unresolvable_actor_secret_ref(secret_ref: str) -> bool:
    return _text(secret_ref).lower().startswith("secret_ref:actor:")


def _actor_is_executable(actor: dict[str, Any]) -> bool:
    role = _text(actor.get("role")).lower()
    if role in {"anonymous", "public"}:
        return True
    secret_ref = _text(
        actor.get("credential_secret_ref")
        or actor.get("secret_ref")
    )
    return bool(secret_ref and not _is_unresolvable_actor_secret_ref(secret_ref))


def _state_semantic_value(state: dict[str, Any]) -> str:
    for field in ("value", "name", "state", "status", "label", "code"):
        value = _text(state.get(field))
        if value:
            return value
    return ""


def _state_match_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", _text(value).casefold())


def _resolve_state_compile_context(
    *,
    behavior_ir: dict[str, Any],
    property_spec: dict[str, Any],
    operation_ref: str,
    required_actors: list[str],
    required_fixtures: list[str],
) -> tuple[dict[str, Any], list[str], list[str], str]:
    """Resolve state refs and one source-permitted runtime actor.

    State obligations are emitted from an explicit transition relation, but the
    generic compiler historically received only state node IDs, no actor, and a
    synthetic ``entity_in_state:*`` fixture that the runtime cannot construct.
    This resolver converts only source-backed IR facts into executable inputs.
    It never invents a state, account, or fixture.
    """

    prop = dict(_dict(property_spec))
    states = _index_by_id(_list(_dict(behavior_ir).get("states")))
    for ref_field, value_field in (
        ("from_state_ref", "from_state"),
        ("to_state_ref", "to_state"),
    ):
        if _text(prop.get(value_field)):
            continue
        state_ref = _text(prop.get(ref_field))
        state_value = _state_semantic_value(states.get(state_ref) or {})
        if not state_value:
            return prop, required_actors, required_fixtures, (
                f"state_value_unresolved:{state_ref or ref_field}"
            )
        prop[value_field] = state_value

    if _text(prop.get("from_state")).casefold() == _text(prop.get("to_state")).casefold():
        return prop, required_actors, required_fixtures, "state_transition_no_change"

    actors = _index_by_id(_list(_dict(behavior_ir).get("actors")))
    resolved_actors = [
        actor_id
        for actor_id in required_actors
        if actor_id in actors and _actor_is_executable(actors[actor_id])
    ]
    if not resolved_actors:
        permitted_actor_ids = {
            _text(relation.get("actor_ref") or relation.get("from_ref"))
            for relation in _list(_dict(behavior_ir).get("relations"))
            if isinstance(relation, dict)
            and _text(relation.get("relation_type")) == "permits"
            and _text(relation.get("operation_ref")) == _text(operation_ref)
            and _text(relation.get("status")) not in {"conflicting", "unsupported"}
            and _text(relation.get("actor_ref") or relation.get("from_ref"))
        }
        ranked = sorted(
            (
                (
                    0 if _text(actor.get("account_ref")) else 1,
                    0 if actor.get("runtime_bound") is True else 1,
                    actor_id,
                )
                for actor_id, actor in actors.items()
                if actor_id in permitted_actor_ids and _actor_is_executable(actor)
            )
        )
        if ranked:
            resolved_actors = [ranked[0][2]]
    if not resolved_actors:
        return prop, [], required_fixtures, "state_transition_actor_unresolved"

    prop.setdefault("actor_ref", resolved_actors[0])
    effective_fixtures = [
        fixture
        for fixture in required_fixtures
        if not _text(fixture).startswith("entity_in_state:")
    ]
    return prop, resolved_actors, effective_fixtures, ""


def _field_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", _text(value).lower())


def _source_request_example(operation: dict[str, Any]) -> dict[str, Any]:
    direct = _dict(operation).get("request_example")
    if isinstance(direct, dict) and direct:
        return dict(direct)
    request_schema = _dict(_dict(operation).get("request_schema"))
    content = _dict(request_schema.get("content"))
    for media in content.values():
        if not isinstance(media, dict):
            continue
        example = media.get("example")
        if isinstance(example, dict) and example:
            return dict(example)
        examples = _dict(media.get("examples"))
        for row in examples.values():
            value = _dict(row).get("value")
            if isinstance(value, dict) and value:
                return dict(value)
    return {}


def _post_action_can_restore_named_terminal_field(operation: dict[str, Any]) -> bool:
    if _text(operation.get("method")).upper() != "POST":
        return False
    path = normalize_path_placeholders(
        _text(operation.get("path") or operation.get("raw_path"))
    ).split("?", 1)[0].rstrip("/")
    segments = [segment for segment in path.split("/") if segment]
    if len(segments) < 2:
        return False
    terminal = segments[-1]
    if terminal.startswith("{") and terminal.endswith("}"):
        return False
    request = _source_request_example(operation)
    if not request:
        return False
    terminal_key = _field_key(terminal)
    body_keys = {
        _field_key(key)
        for key, value in request.items()
        if value not in (None, "", [], {}) and not isinstance(value, (dict, list))
    }
    return bool(terminal_key and terminal_key in body_keys)


def _inverse_delta_cleanup_spec(operation: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    if _text(operation.get("method")).upper() not in {"POST", "PUT", "PATCH"}:
        return "", {}
    request = _source_request_example(operation)
    if not request:
        return "", {}
    matches = [
        (key, value)
        for key, value in request.items()
        if _field_key(key) == "delta"
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
    ]
    if len(matches) != 1:
        return "", {}
    key, value = matches[0]
    cleanup_body = dict(request)
    cleanup_body[key] = -value
    return _text(key), cleanup_body


def compile_experiment_for_obligation(
    obligation: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
    environment_type: str = "",
    policy_version: str = "",
    available_adapters: set[str] | None = None,
) -> dict[str, Any]:
    """Compile one obligation. Never silently degrade to a different probe."""

    obl = _dict(obligation)
    ir = _dict(behavior_ir)
    oid = _text(obl.get("obligation_id")) or "unknown_obligation"
    ops = _index_by_id(_list(ir.get("operations")))
    actors = _index_by_id(_list(ir.get("actors")))
    adapters = {"http_api"} if available_adapters is None else set(available_adapters)
    env = _text(environment_type).lower()
    if env in {"", "production", "prod", "live", "unknown"}:
        return blocked_experiment(
            oid,
            "BLOCKED_UNSUPPORTED_ADAPTER",
            "non_production_environment_required",
        )

    prop = _dict(obl.get("property"))
    family = _text(obl.get("risk_family"))
    required_ops = [
        _text(x) for x in _list(obl.get("required_operations")) if _text(x)
    ]
    required_actors = [
        _text(x) for x in _list(obl.get("required_actors")) if _text(x)
    ]
    required_fixtures = [
        _text(x) for x in _list(obl.get("required_fixtures")) if _text(x)
    ]
    required_observers = [
        _text(x) for x in _list(obl.get("required_observers")) if _text(x)
    ]
    primary_op_id = required_ops[0] if required_ops else _text(prop.get("operation_ref"))

    if family == "state":
        prop, required_actors, required_fixtures, state_reason = (
            _resolve_state_compile_context(
                behavior_ir=ir,
                property_spec=prop,
                operation_ref=primary_op_id,
                required_actors=required_actors,
                required_fixtures=required_fixtures,
            )
        )
        if state_reason:
            return blocked_experiment(
                oid,
                "BLOCKED_MISSING_BINDING"
                if "actor" not in state_reason
                else "BLOCKED_MISSING_ACTOR",
                state_reason,
            )
        obl = {
            **obl,
            "property": prop,
            "required_actors": required_actors,
            "required_fixtures": required_fixtures,
        }

    for op_id in required_ops:
        if op_id not in ops:
            return blocked_experiment(oid, "BLOCKED_MISSING_OPERATION", op_id)
    for actor_id in required_actors:
        if actor_id not in actors:
            return blocked_experiment(oid, "BLOCKED_MISSING_ACTOR", actor_id)
        secret_ref = _text(
            actors[actor_id].get("credential_secret_ref")
            or actors[actor_id].get("secret_ref")
        )
        if (
            not secret_ref
            and _text(actors[actor_id].get("role")).lower()
            not in {"anonymous", "public"}
        ):
            return blocked_experiment(
                oid,
                "BLOCKED_MISSING_ACTOR",
                f"missing_secret_ref:{actor_id}",
            )
        if _is_unresolvable_actor_secret_ref(secret_ref):
            return blocked_experiment(
                oid,
                "BLOCKED_MISSING_ACTOR",
                f"unresolved_secret_ref:{actor_id}",
            )

    if "http_api" not in adapters:
        return blocked_experiment(oid, "BLOCKED_UNSUPPORTED_ADAPTER", "http_api")

    for gap in _list(ir.get("conflicts")):
        if isinstance(gap, dict) and _text(gap.get("status")) == "conflicting":
            return blocked_experiment(
                oid,
                "BLOCKED_CONFLICTING_SOURCE",
                _text(gap.get("id")),
            )

    if not primary_op_id or primary_op_id not in ops:
        return blocked_experiment(
            oid,
            "BLOCKED_MISSING_OPERATION",
            primary_op_id or "none",
        )
    primary_op = ops[primary_op_id]
    is_write = _text(primary_op.get("read_write")) == "write"
    write_observers = (
        declared_effect_observers(primary_op, behavior_ir=ir)
        if is_write
        else []
    )
    if is_write and not write_observers:
        return blocked_experiment(
            oid,
            "BLOCKED_MISSING_OBSERVER",
            "write_observer",
        )

    binding_plan = build_binding_plan(
        operation=primary_op,
        obligation=obl,
        actors=[actors[a] for a in required_actors if a in actors],
        behavior_ir=ir,
    )
    if family == "state":
        state_token = _state_match_token(prop.get("from_state"))
        normalized_path = normalize_path_placeholders(
            _text(primary_op.get("path") or primary_op.get("raw_path"))
        )
        for binding in binding_plan:
            if not isinstance(binding, dict):
                continue
            target = _text(binding.get("target"))
            if (
                state_token
                and _text(binding.get("status")) == "runtime_resolvable"
                and (
                    "{" + target + "}" in normalized_path
                    or ":" + target in normalized_path
                )
            ):
                target_path = _text(binding.get("target_path")) or f"/{{{target}}}"
                binding["target_path"] = f"@state={state_token}@{target_path}"
                binding["selection_semantics"] = "source_state_precondition"
                binding["required_state"] = _text(prop.get("from_state"))
    unresolved = unresolved_placeholders(primary_op, binding_plan)
    if unresolved:
        return blocked_experiment(
            oid,
            "BLOCKED_MISSING_BINDING",
            ",".join(unresolved[:8]),
        )

    for fixture in required_fixtures:
        concrete = next(
            (
                item
                for item in binding_plan
                if isinstance(item, dict)
                and (
                    _text(item.get("fixture_id")) == fixture
                    or _text(item.get("name")) == fixture
                    or _text(item.get("target")) == f"fixture:{fixture}"
                )
                and _text(
                    item.get("create_path")
                    or item.get("create_operation_ref")
                ).startswith("/")
                and "{" not in _text(
                    item.get("create_path")
                    or item.get("create_operation_ref")
                )
            ),
            None,
        )
        if concrete is None:
            return blocked_experiment(
                oid,
                "BLOCKED_MISSING_FIXTURE",
                fixture or "required_fixture",
            )

    if not required_observers:
        return blocked_experiment(oid, "BLOCKED_MISSING_OBSERVER", "none")

    cleanup_req = _dict(obl.get("cleanup_requirement"))
    cleanup_plan: list[dict[str, Any]] = []
    if is_write:
        if cleanup_req.get("required") is False:
            return blocked_experiment(
                oid,
                "BLOCKED_NON_REVERSIBLE_WRITE",
                primary_op_id,
            )
        primary_method = _text(primary_op.get("method")).upper()
        primary_path = normalize_path_placeholders(
            _text(primary_op.get("path") or primary_op.get("raw_path"))
        )
        cleanup_op = _text(
            cleanup_req.get("operation_ref")
            or cleanup_req.get("compensation_operation_ref")
        )
        delta_field, inverse_delta_body = _inverse_delta_cleanup_spec(primary_op)
        if not cleanup_op and primary_path.startswith("/") and delta_field:
            cleanup_plan = [{
                "action": "inverse_delta_compensation",
                "mode": "delta_inverse",
                "operation_ref": primary_op_id,
                "path": primary_path,
                "method": primary_method,
                "delta_field": delta_field,
                "body": inverse_delta_body,
                "runtime_response_binding_required": "{" in primary_path,
            }]
        elif (
            not cleanup_op
            and primary_path.startswith("/")
            and (
                primary_method in {"PUT", "PATCH"}
                or _post_action_can_restore_named_terminal_field(primary_op)
            )
        ):
            cleanup_plan = [{
                "action": "restore_before_snapshot",
                "mode": "snapshot_restore",
                "operation_ref": primary_op_id,
                "path": primary_path,
                "method": primary_method,
                "runtime_response_binding_required": "{" in primary_path,
            }]
        if not cleanup_plan and not cleanup_op:
            action_compensators = declared_action_compensators(
                primary_op,
                behavior_ir=ir,
            )
            if len(action_compensators) == 1:
                compensator = action_compensators[0]
                cleanup_plan = [{
                    "action": "source_declared_compensation",
                    "mode": "reverse_order",
                    "operation_ref": _text(compensator.get("operation_ref")),
                    "compensates_operation_ref": primary_op_id,
                    "path": _text(compensator.get("path")),
                    "method": _text(compensator.get("method")).upper(),
                    "body_from_original_request": True,
                    "runtime_response_binding_required": (
                        "{" in _text(compensator.get("path"))
                    ),
                }]
        if not cleanup_plan:
            cleanup_path = normalize_path_placeholders(
                _text(
                    _dict(ops.get(cleanup_op)).get("path")
                    or _dict(ops.get(cleanup_op)).get("raw_path")
                )
            )
            cleanup_method = _text(
                _dict(ops.get(cleanup_op)).get("method")
            ).upper()
            if (
                not cleanup_op
                or cleanup_op == primary_op_id
                or cleanup_op not in ops
                or cleanup_method not in {"POST", "PUT", "PATCH", "DELETE"}
                or not cleanup_path.startswith("/")
            ):
                return blocked_experiment(
                    oid,
                    "BLOCKED_NON_REVERSIBLE_WRITE",
                    f"cleanup_unresolved:{cleanup_op}",
                )
            cleanup_plan = [{
                "action": "reverse_order_compensation",
                "mode": _text(cleanup_req.get("mode") or "reverse_order"),
                "operation_ref": cleanup_op,
                "path": cleanup_path,
                "method": cleanup_method,
                "runtime_response_binding_required": "{" in cleanup_path,
            }]

    control_actor = _text(
        prop.get("control_actor_ref")
        or prop.get("owner_actor_ref")
        or prop.get("actor_ref")
        or (required_actors[0] if required_actors else "")
    )
    treatment_actor = _text(
        prop.get("treatment_actor_ref")
        or prop.get("viewer_actor_ref")
        or prop.get("actor_ref")
        or (
            required_actors[1]
            if len(required_actors) > 1
            else control_actor
        )
    )
    observer_requirements = list(required_observers)
    if is_write and family in {"authorization", "isolation", "visibility"}:
        observer_requirements.append("business_effect")
    observers, observer_reason, observer_detail = compile_observer_requirements(
        observer_requirements,
        risk_family=family,
        available_adapters=adapters,
    )
    if observer_reason:
        return blocked_experiment(oid, observer_reason, observer_detail)

    if any(
        _text(observer.get("observer_id")) == "resource_ownership"
        for observer in observers
    ):
        ownership_proofs = [
            row
            for row in binding_plan
            if isinstance(row, dict)
            and _text(row.get("status")) == "fixture_proof"
            and _text(row.get("owner_actor_ref"))
            and _text(row.get("binding_target"))
        ]
        if not ownership_proofs:
            return blocked_experiment(
                oid,
                "BLOCKED_MISSING_FIXTURE",
                "ownership_proof",
            )
        for observer in observers:
            if _text(observer.get("observer_id")) == "resource_ownership":
                observer["fixture_proof_refs"] = [
                    _text(row.get("fixture_id"))
                    for row in ownership_proofs
                    if _text(row.get("fixture_id"))
                ]

    effect_observer_ids = {
        _text(observer.get("observer_id"))
        for observer in observers
        if _text(observer.get("observer_id")) in {
            "after_state",
            "before_state",
            "business_effect",
            "entity_state",
            "final_state",
        }
    }
    if effect_observer_ids:
        if not write_observers:
            return blocked_experiment(
                oid,
                "BLOCKED_MISSING_OBSERVER",
                ",".join(sorted(effect_observer_ids)),
            )
        for observer in observers:
            if _text(observer.get("observer_id")) in effect_observer_ids:
                observer["resolver_operations"] = write_observers

    protocol = compile_family_protocol(
        risk_family=family,
        operation=primary_op,
        operation_ref=primary_op_id,
        control_actor_ref=control_actor,
        treatment_actor_ref=treatment_actor,
        property_spec=prop,
    )
    if _text(protocol.get("status")) != "COMPILED":
        return blocked_experiment(
            oid,
            _text(protocol.get("reason_code"))
            or "BLOCKED_UNSUPPORTED_ADAPTER",
            _text(protocol.get("detail")),
        )

    if family == "state":
        treatment_rows = [
            row
            for row in _list(protocol.get("treatment_plan"))
            if isinstance(row, dict)
        ]
        if len(treatment_rows) != 1:
            return blocked_experiment(
                oid,
                "BLOCKED_UNSUPPORTED_ADAPTER",
                "state_transition_protocol_shape_invalid",
            )
        treatment_rows[0] = {
            **treatment_rows[0],
            "intent": "state_transition",
            "protocol_step": "state_transition_write",
            "from_state_ref": _text(prop.get("from_state_ref")),
            "to_state_ref": _text(prop.get("to_state_ref")),
        }
        protocol = {
            **protocol,
            "control_plan": [],
            "treatment_plan": treatment_rows,
            "assertion": {
                "kind": "state_transition",
                "from_state": _text(prop.get("from_state")),
                "to_state": _text(prop.get("to_state")),
                "from_state_ref": _text(prop.get("from_state_ref")),
                "to_state_ref": _text(prop.get("to_state_ref")),
            },
        }

    control_plan = [
        row
        for row in _list(protocol.get("control_plan"))
        if isinstance(row, dict)
    ]
    treatment_plan = [
        row
        for row in _list(protocol.get("treatment_plan"))
        if isinstance(row, dict)
    ]
    needs_control = bool(control_plan)

    protocol_assertion = _dict(protocol.get("assertion"))
    assertions = [{
        "assertion_id": f"assert_{family or 'generic'}",
        "kind": (
            _text(protocol_assertion.get("kind"))
            or family
            or "validation"
        ),
        "template": _text(prop.get("template")),
        "expected_from": "source_property",
        "property": prop,
        "require_control": needs_control,
        **{
            key: value
            for key, value in protocol_assertion.items()
            if key != "kind"
        },
    }]
    return make_experiment(
        obligation_id=oid,
        policy_version=policy_version,
        control_plan=control_plan,
        treatment_plan=treatment_plan,
        binding_plan=binding_plan,
        setup_plan=[{
            "action": "resolve_bindings",
            "bindings": [b.get("target") for b in binding_plan],
        }],
        assertions=assertions,
        observers=observers,
        cleanup_plan=cleanup_plan,
        safety_contract={
            "environment_type": env,
            "non_production_required": True,
            "governed_write": is_write,
        },
        source_refs=list(obl.get("source_refs") or [])[:5],
        compile_receipt={
            "status": "COMPILED",
            "reason_code": "",
            "unresolved_placeholders": 0,
            "control_present": bool(control_plan),
            "treatment_present": bool(treatment_plan),
            "cleanup_present": bool(cleanup_plan) or not is_write,
        },
    )


def compile_experiments(
    obligations: list[dict[str, Any]],
    *,
    behavior_ir: dict[str, Any],
    environment_type: str = "",
    policy_version: str = "",
) -> dict[str, Any]:
    compiled: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    operations = _index_by_id(_list(_dict(behavior_ir).get("operations")))
    for obl in obligations:
        if not isinstance(obl, dict):
            continue
        prop = _dict(obl.get("property"))
        operation_ref = (
            next(
                (
                    _text(value)
                    for value in _list(obl.get("required_operations"))
                    if _text(value)
                ),
                "",
            )
            or _text(prop.get("operation_ref"))
        )
        variants = expand_validation_obligation(
            obl,
            operation=operations.get(operation_ref) or {},
        )
        variant_compiled = 0
        variant_blocked = 0
        for variant in variants:
            experiment = compile_experiment_for_obligation(
                variant,
                behavior_ir=behavior_ir,
                environment_type=environment_type,
                policy_version=policy_version,
            )
            receipt = _dict(experiment.get("compile_receipt"))
            if _text(receipt.get("status")) == "COMPILED":
                compiled.append(experiment)
                variant["compile_status"] = "COMPILED"
                variant_compiled += 1
            else:
                blocked.append(experiment)
                variant["compile_status"] = "BLOCKED"
                variant["block_reason"] = receipt.get("reason_code")
                variant_blocked += 1
        obl["compile_status"] = "COMPILED" if variant_compiled else "BLOCKED"
        obl["expanded_experiment_count"] = len(variants)
        obl["compiled_experiment_count"] = variant_compiled
        obl["blocked_experiment_count"] = variant_blocked
        if not variant_compiled and blocked:
            obl["block_reason"] = _dict(blocked[-1].get("compile_receipt")).get(
                "reason_code"
            )
    return {
        "schema_version": "qualibug.experiment-compile.v1",
        "compiled_count": len(compiled),
        "blocked_count": len(blocked),
        "experiments": compiled,
        "blocked_experiments": blocked,
        "block_reason_counts": _count_reasons(blocked),
    }


def _count_reasons(blocked: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in blocked:
        code = (
            _text(_dict(item.get("compile_receipt")).get("reason_code"))
            or "UNKNOWN"
        )
        counts[code] = counts.get(code, 0) + 1
    return counts


# ── Experiment Contract (merged from experiment_contract.py) ──────────

SCHEMA_VERSION = "qualibug.experiment.v1"

BLOCK_REASONS = (
    "BLOCKED_MISSING_OPERATION",
    "BLOCKED_MISSING_ACTOR",
    "BLOCKED_MISSING_FIXTURE",
    "BLOCKED_MISSING_BINDING",
    "BLOCKED_MISSING_OBSERVER",
    "BLOCKED_NON_REVERSIBLE_WRITE",
    "BLOCKED_CONFLICTING_SOURCE",
    "BLOCKED_UNSUPPORTED_ADAPTER",
)


def stable_experiment_id(obligation_id: str, *parts: Any) -> str:
    raw = "|".join([_text(obligation_id), *(_text(p) for p in parts if _text(p))])
    return f"exp_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]}"


def make_experiment(
    *,
    obligation_id: str,
    policy_version: str = "",
    control_plan: list[dict[str, Any]] | None = None,
    treatment_plan: list[dict[str, Any]] | None = None,
    binding_plan: list[dict[str, Any]] | None = None,
    setup_plan: list[dict[str, Any]] | None = None,
    assertions: list[dict[str, Any]] | None = None,
    observers: list[dict[str, Any]] | None = None,
    async_observation_policy: dict[str, Any] | None = None,
    cleanup_plan: list[dict[str, Any]] | None = None,
    safety_contract: dict[str, Any] | None = None,
    source_refs: list[dict[str, Any]] | None = None,
    compile_receipt: dict[str, Any] | None = None,
    experiment_id: str | None = None,
) -> dict[str, Any]:
    eid = _text(experiment_id) or stable_experiment_id(obligation_id, "v1")
    return {
        "schema_version": SCHEMA_VERSION,
        "experiment_id": eid,
        "obligation_id": _text(obligation_id),
        "policy_version": _text(policy_version),
        "control_plan": list(control_plan or []),
        "treatment_plan": list(treatment_plan or []),
        "binding_plan": list(binding_plan or []),
        "setup_plan": list(setup_plan or []),
        "assertions": list(assertions or []),
        "observers": list(observers or []),
        "async_observation_policy": dict(async_observation_policy or {"mode": "bounded_backoff"}),
        "cleanup_plan": list(cleanup_plan or []),
        "safety_contract": dict(safety_contract or {"environment": "non_production_required"}),
        "source_refs": list(source_refs or []),
        "compile_receipt": dict(compile_receipt or {"status": "COMPILED"}),
    }


def blocked_experiment(obligation_id: str, reason_code: str, detail: str = "") -> dict[str, Any]:
    code = reason_code if reason_code in BLOCK_REASONS else "BLOCKED_UNSUPPORTED_ADAPTER"
    return make_experiment(
        obligation_id=obligation_id,
        compile_receipt={
            "status": "BLOCKED",
            "reason_code": code,
            "detail": _text(detail),
        },
        experiment_id=stable_experiment_id(obligation_id, code),
    )
