"""Compile TestObligations into ExecutableExperiments or explicit blocks."""
from __future__ import annotations

import re
from typing import Any

from .experiment_contract import blocked_experiment, make_experiment
from .experiment_protocols import compile_family_protocol
from .observer_contracts import compile_observer_requirements
from .runtime_binding_graph import (
    build_binding_plan,
    declared_action_compensators,
    declared_effect_observers,
    unresolved_placeholders,
)
from .real_id_resolver import normalize_path_placeholders


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
        return blocked_experiment(oid, "BLOCKED_UNSUPPORTED_ADAPTER", "non_production_environment_required")

    prop = _dict(obl.get("property"))
    family = _text(obl.get("risk_family"))
    required_ops = [_text(x) for x in _list(obl.get("required_operations")) if _text(x)]
    required_actors = [_text(x) for x in _list(obl.get("required_actors")) if _text(x)]
    required_fixtures = [_text(x) for x in _list(obl.get("required_fixtures")) if _text(x)]
    required_observers = [_text(x) for x in _list(obl.get("required_observers")) if _text(x)]

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
        if not secret_ref and _text(actors[actor_id].get("role")).lower() not in {"anonymous", "public"}:
            return blocked_experiment(oid, "BLOCKED_MISSING_ACTOR", f"missing_secret_ref:{actor_id}")
        if _is_unresolvable_actor_secret_ref(secret_ref):
            return blocked_experiment(oid, "BLOCKED_MISSING_ACTOR", f"unresolved_secret_ref:{actor_id}")

    if "http_api" not in adapters:
        return blocked_experiment(oid, "BLOCKED_UNSUPPORTED_ADAPTER", "http_api")

    # Conflicting IR sources block compilation
    for gap in _list(ir.get("conflicts")):
        if isinstance(gap, dict) and _text(gap.get("status")) == "conflicting":
            return blocked_experiment(oid, "BLOCKED_CONFLICTING_SOURCE", _text(gap.get("id")))

    primary_op_id = required_ops[0] if required_ops else _text(prop.get("operation_ref"))
    if not primary_op_id or primary_op_id not in ops:
        return blocked_experiment(oid, "BLOCKED_MISSING_OPERATION", primary_op_id or "none")
    primary_op = ops[primary_op_id]
    is_write = _text(primary_op.get("read_write")) == "write"
    write_observers = (
        declared_effect_observers(primary_op, behavior_ir=ir)
        if is_write
        else []
    )
    if is_write and not write_observers:
        return blocked_experiment(oid, "BLOCKED_MISSING_OBSERVER", "write_observer")

    binding_plan = build_binding_plan(
        operation=primary_op,
        obligation=obl,
        actors=[actors[a] for a in required_actors if a in actors],
        behavior_ir=ir,
    )
    unresolved = unresolved_placeholders(primary_op, binding_plan)
    if unresolved:
        return blocked_experiment(oid, "BLOCKED_MISSING_BINDING", ",".join(unresolved[:8]))

    if required_fixtures:
        for fixture in required_fixtures:
            concrete = next(
                (
                    item for item in binding_plan
                    if isinstance(item, dict)
                    and (
                        _text(item.get("fixture_id")) == fixture
                        or _text(item.get("name")) == fixture
                        or _text(item.get("target")) == f"fixture:{fixture}"
                    )
                    and _text(item.get("create_path") or item.get("create_operation_ref")).startswith("/")
                    and "{" not in _text(item.get("create_path") or item.get("create_operation_ref"))
                ),
                None,
            )
            if concrete is None:
                return blocked_experiment(oid, "BLOCKED_MISSING_FIXTURE", fixture or "required_fixture")

    if not required_observers:
        return blocked_experiment(oid, "BLOCKED_MISSING_OBSERVER", "none")

    cleanup_req = _dict(obl.get("cleanup_requirement"))
    cleanup_plan: list[dict[str, Any]] = []
    if is_write:
        if cleanup_req.get("required") is False:
            return blocked_experiment(oid, "BLOCKED_NON_REVERSIBLE_WRITE", primary_op_id)
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
                    "runtime_response_binding_required": "{" in _text(compensator.get("path")),
                }]
        if not cleanup_plan:
            cleanup_path = normalize_path_placeholders(
                _text(
                    _dict(ops.get(cleanup_op)).get("path")
                    or _dict(ops.get(cleanup_op)).get("raw_path")
                )
            )
            cleanup_method = _text(_dict(ops.get(cleanup_op)).get("method")).upper()
            if (
                not cleanup_op
                or cleanup_op == primary_op_id
                or cleanup_op not in ops
                or cleanup_method not in {"POST", "PUT", "PATCH", "DELETE"}
                or not cleanup_path.startswith("/")
            ):
                return blocked_experiment(oid, "BLOCKED_NON_REVERSIBLE_WRITE", f"cleanup_unresolved:{cleanup_op}")
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
        or (required_actors[1] if len(required_actors) > 1 else control_actor)
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
    if any(_text(observer.get("observer_id")) == "resource_ownership" for observer in observers):
        ownership_proofs = [
            row
            for row in binding_plan
            if isinstance(row, dict)
            and _text(row.get("status")) == "fixture_proof"
            and _text(row.get("owner_actor_ref"))
            and _text(row.get("binding_target"))
        ]
        if not ownership_proofs:
            return blocked_experiment(oid, "BLOCKED_MISSING_FIXTURE", "ownership_proof")
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
            _text(protocol.get("reason_code")) or "BLOCKED_UNSUPPORTED_ADAPTER",
            _text(protocol.get("detail")),
        )
    control_plan = [row for row in _list(protocol.get("control_plan")) if isinstance(row, dict)]
    treatment_plan = [row for row in _list(protocol.get("treatment_plan")) if isinstance(row, dict)]
    needs_control = bool(control_plan)

    protocol_assertion = _dict(protocol.get("assertion"))
    assertions = [{
        "assertion_id": f"assert_{family or 'generic'}",
        "kind": _text(protocol_assertion.get("kind")) or family or "validation",
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
        setup_plan=[{"action": "resolve_bindings", "bindings": [b.get("target") for b in binding_plan]}],
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
    for obl in obligations:
        if not isinstance(obl, dict):
            continue
        experiment = compile_experiment_for_obligation(
            obl,
            behavior_ir=behavior_ir,
            environment_type=environment_type,
            policy_version=policy_version,
        )
        receipt = _dict(experiment.get("compile_receipt"))
        if _text(receipt.get("status")) == "COMPILED":
            compiled.append(experiment)
            obl["compile_status"] = "COMPILED"
        else:
            blocked.append(experiment)
            obl["compile_status"] = "BLOCKED"
            obl["block_reason"] = receipt.get("reason_code")
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
        code = _text(_dict(item.get("compile_receipt")).get("reason_code")) or "UNKNOWN"
        counts[code] = counts.get(code, 0) + 1
    return counts
