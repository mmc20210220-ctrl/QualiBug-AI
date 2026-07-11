"""Compile TestObligations into ExecutableExperiments or explicit blocks."""
from __future__ import annotations

from typing import Any

from .experiment_contract import blocked_experiment, make_experiment
from .runtime_binding_graph import build_binding_plan, unresolved_placeholders


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
    adapters = available_adapters or {"http_api"}
    env = _text(environment_type).lower()
    if env in {"", "production", "prod", "live", "unknown"}:
        return blocked_experiment(oid, "BLOCKED_UNSUPPORTED_ADAPTER", "non_production_environment_required")

    prop = _dict(obl.get("property"))
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
        secret_ref = _text(actors[actor_id].get("credential_secret_ref"))
        if not secret_ref and _text(actors[actor_id].get("role")).lower() not in {"anonymous", "public"}:
            return blocked_experiment(oid, "BLOCKED_MISSING_ACTOR", f"missing_secret_ref:{actor_id}")

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

    binding_plan = build_binding_plan(
        operation=primary_op,
        obligation=obl,
        actors=[actors[a] for a in required_actors if a in actors],
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
        cleanup_op = _text(
            cleanup_req.get("operation_ref")
            or cleanup_req.get("compensation_operation_ref")
        )
        cleanup_path = _text(_dict(ops.get(cleanup_op)).get("path") or _dict(ops.get(cleanup_op)).get("raw_path"))
        cleanup_method = _text(_dict(ops.get(cleanup_op)).get("method")).upper()
        if (
            not cleanup_op
            or cleanup_op == primary_op_id
            or cleanup_op not in ops
            or cleanup_method not in {"POST", "PUT", "PATCH", "DELETE"}
            or not cleanup_path.startswith("/")
            or ":" in cleanup_path
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

    control_actor = _text(prop.get("control_actor_ref") or prop.get("owner_actor_ref") or (required_actors[0] if required_actors else ""))
    treatment_actor = _text(prop.get("treatment_actor_ref") or prop.get("viewer_actor_ref") or (required_actors[1] if len(required_actors) > 1 else control_actor))
    family = _text(obl.get("risk_family"))

    # Negative tests require a positive control on the same contract.
    needs_control = family in {"authorization", "isolation", "validation", "privacy", "visibility"}
    control_plan: list[dict[str, Any]] = []
    if needs_control:
        if not control_actor:
            return blocked_experiment(oid, "BLOCKED_MISSING_ACTOR", "control_actor")
        control_plan = [{
            "step_id": "control_1",
            "actor_ref": control_actor,
            "operation_ref": primary_op_id,
            "intent": "authorized_control",
        }]

    treatment_plan = [{
        "step_id": "treatment_1",
        "actor_ref": treatment_actor or control_actor,
        "operation_ref": primary_op_id,
        "intent": "treatment",
        "property_template": _text(prop.get("template")),
    }]

    assertions = [{
        "assertion_id": f"assert_{family or 'generic'}",
        "kind": family or "validation",
        "template": _text(prop.get("template")),
        "expected_from": "source_property",
        "property": prop,
        "require_control": needs_control,
    }]
    observers = [{"observer_id": name, "surface": "http_api"} for name in required_observers]

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
