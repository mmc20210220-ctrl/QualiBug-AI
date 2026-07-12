"""Execute selected compiled experiments end-to-end on the V12 main chain.

Path: selected experiment → fixture DAG → governed requests → observers →
typed assertions → contract oracle → delivery-gate-ready finding (or explicit
BLOCKED / harness receipt). Never invents COMPILED success for unresolved
actor/fixture/observer/cleanup compensation.
"""
from __future__ import annotations

import json
import hashlib
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .assertion_dsl import materialize_assertion
from .contract_oracles import (
    build_contract_evidence_receipt,
    contract_activation_requirements,
    evaluate_contract_oracle,
    mark_as_internal_clue,
    validate_contract_oracle_receipt,
)
from .customer_delivery_gate import build_customer_delivery_gate_receipt
from .observer_contracts import (
    observe_experiment_requirements,
    validate_observer_declarations,
)
from .operational_receipts import build_execution_operational_receipt
from .real_id_resolver import (
    bind_entity_fields,
    infer_path_params,
    normalize_path_placeholders,
    path_has_placeholders,
)
from .runtime_binding_materializer import (
    materialize_body_template as _materialize_body_template,
    materialize_path as _materialize_path,
    runtime_binding_contract_ready as _runtime_binding_contract_ready,
    runtime_cleanup_paths as _runtime_cleanup_paths,
    runtime_setup_value_from_response as _runtime_setup_value_from_response,
    runtime_value_from_response as _runtime_value_from_response,
    validated_fixture_setup as _validated_fixture_setup,
    validated_runtime_resolvers as _validated_runtime_resolvers,
)
from .sandbox_write_executor import (
    _http_request,
    execute_governed_control_write,
    sandbox_write_allowed,
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _stable_id(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(_text(part) for part in parts)
    return f"{prefix}_{hashlib.sha256(material.encode('utf-8')).hexdigest()[:20]}"


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _observation_state(value: Any) -> dict[str, Any]:
    row = _dict(value)
    return {
        "status": int(row.get("status") or row.get("status_code") or 0),
        "body": row.get("body"),
    }


def _governance_audit_receipt_id(governed: dict[str, Any]) -> str:
    row = _dict(governed)
    audit_record = _dict(row.get("audit_record"))
    audit_path = _text(row.get("audit_path"))
    if not audit_record and not audit_path:
        return ""
    material = {
        "audit_record": audit_record,
        "audit_path": audit_path,
        "before_ref": _text(row.get("before_ref")),
        "after_ref": _text(row.get("after_ref")),
        "accepted": row.get("accepted") is True,
    }
    return "audit_" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:24]


def _resource_identity_candidates(value: Any) -> set[str]:
    identities: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                normalized = "".join(ch for ch in str(key).lower() if ch.isalnum())
                if (
                    normalized in {"id", "uuid", "key"}
                    or normalized.endswith("id")
                ) and not isinstance(child, (dict, list)) and _text(child):
                    identities.add(_text(child))
                elif isinstance(child, (dict, list)):
                    walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    return identities


def _cleanup_restores_governed_write(
    original: dict[str, Any],
    cleanup: dict[str, Any],
) -> bool:
    original_row = _dict(original)
    cleanup_row = _dict(cleanup)
    if original_row.get("accepted") is not True or cleanup_row.get("accepted") is not True:
        return False
    if not _governance_audit_receipt_id(original_row) or not _governance_audit_receipt_id(cleanup_row):
        return False
    original_before = _observation_state(original_row.get("before"))
    cleanup_after = _observation_state(cleanup_row.get("after"))
    if original_before == cleanup_after:
        return True
    original_method = _text(original_row.get("method")).upper()
    cleanup_path = _text(cleanup_row.get("path"))
    created_identities = _resource_identity_candidates(
        _dict(original_row.get("write")).get("body")
    )
    identity_bound = any(
        identity and identity in cleanup_path for identity in created_identities
    )
    cleanup_before = _observation_state(cleanup_row.get("before"))
    return bool(
        original_method == "POST"
        and identity_bound
        and 200 <= int(cleanup_before.get("status") or 0) < 300
        and int(cleanup_after.get("status") or 0) in {404, 410}
    )


def _governed_write_attempts(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        _dict(step.get("governance_receipt"))
        for step in steps
        if _text(step.get("phase")) in {"control", "treatment"}
        and isinstance(step.get("governance_receipt"), dict)
    ]


def _rejected_writes_left_state_unchanged(
    attempts: list[dict[str, Any]],
) -> bool:
    return bool(attempts) and all(
        attempt.get("accepted") is not True
        and bool(_governance_audit_receipt_id(attempt))
        and _observation_state(attempt.get("before"))
        == _observation_state(attempt.get("after"))
        for attempt in attempts
    )


def _body_contains_scalar(value: Any, expected: Any) -> bool:
    if isinstance(value, dict):
        return any(_body_contains_scalar(child, expected) for child in value.values())
    if isinstance(value, list):
        return any(_body_contains_scalar(child, expected) for child in value)
    return value == expected


def _index_by_id(nodes: list[Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for node in nodes:
        if isinstance(node, dict) and _text(node.get("id")):
            out[_text(node.get("id"))] = node
    return out


def load_actor_tokens(root: Path, project: str) -> dict[str, str]:
    """Map role / secret_ref → bearer token from declared test accounts only."""
    path = Path(root) / "platform_inputs" / str(project) / "test_accounts.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, json.JSONDecodeError):
        return {}
    tokens: dict[str, str] = {}
    rows: list[Any] = []
    if isinstance(payload, dict):
        rows = list(payload.get("accounts") or payload.get("actors") or payload.get("users") or [])
        if not rows:
            rows = [
                {**(value if isinstance(value, dict) else {}), "account_ref": key}
                for key, value in payload.items()
                if isinstance(value, dict) and key not in {"schema", "schema_version", "meta"}
            ]
    elif isinstance(payload, list):
        rows = payload
    for row in rows:
        if not isinstance(row, dict):
            continue
        role = _text(row.get("role") or row.get("name") or row.get("id"))
        account_ref = _text(row.get("account_ref") or row.get("name") or row.get("id") or row.get("email"))
        token = _text(row.get("token") or row.get("access_token") or row.get("jwt"))
        if not role or not token:
            continue
        status = _text(row.get("status") or row.get("account_status") or row.get("state") or "active").upper()
        if account_ref:
            tokens[account_ref] = token
            tokens[f"secret_ref:test_accounts:{account_ref}"] = token
        if status not in {"DISABLED", "LOCKED"}:
            tokens.setdefault(role, token)
            tokens.setdefault(f"secret_ref:test_accounts:{role}", token)
            tokens.setdefault(f"secret_ref:context:{role}", token)
    return tokens


def preflight_experiment_executable(
    experiment: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
    actor_tokens: dict[str, str],
) -> tuple[bool, str, str]:
    """Return (ok, reason_code, detail). Fail closed — never COMPILED-at-runtime."""
    exp = _dict(experiment)
    receipt = _dict(exp.get("compile_receipt"))
    if _text(receipt.get("status")).upper() != "COMPILED":
        return False, _text(receipt.get("reason_code")) or "BLOCKED_UNSUPPORTED_ADAPTER", "not_compiled"
    dag = _dict(exp.get("fixture_dag"))
    if dag and _text(dag.get("status")).upper() == "BLOCKED":
        reasons = _list(dag.get("blocked_reasons"))
        code = _text(_dict(reasons[0] if reasons else {}).get("reason_code")) or "BLOCKED_MISSING_FIXTURE"
        return False, code, _text(_dict(reasons[0] if reasons else {}).get("detail"))
    ir = _dict(behavior_ir)
    actors = _index_by_id(_list(ir.get("actors")))
    ops = _index_by_id(_list(ir.get("operations")))
    for step in _list(exp.get("control_plan")) + _list(exp.get("treatment_plan")):
        if not isinstance(step, dict):
            continue
        actor_ref = _text(step.get("actor_ref"))
        op_ref = _text(step.get("operation_ref"))
        if not actor_ref or actor_ref not in actors:
            return False, "BLOCKED_MISSING_ACTOR", actor_ref or "missing"
        actor = actors[actor_ref]
        role = _text(actor.get("role"))
        secret = _text(actor.get("credential_secret_ref") or actor.get("secret_ref"))
        if role.lower() not in {"anonymous", "public"}:
            if not secret:
                return False, "BLOCKED_MISSING_ACTOR", f"unresolved_secret:{actor_ref}"
            if secret not in actor_tokens and role not in actor_tokens:
                return False, "BLOCKED_MISSING_ACTOR", f"token_unresolved:{actor_ref}"
        if not op_ref or op_ref not in ops:
            return False, "BLOCKED_MISSING_OPERATION", op_ref or "missing"
        op = ops[op_ref]
        path = _text(op.get("path") or op.get("raw_path"))
        method = _text(op.get("method") or "GET").upper()
        if not path.startswith("/"):
            return False, "BLOCKED_MISSING_BINDING", f"unresolved_path:{op_ref}:{path}"
        if path_has_placeholders(path) and not _runtime_binding_contract_ready(
            path,
            binding_plan=_list(exp.get("binding_plan")),
            fixture_dag=dag,
            operations=ops,
        ):
            return False, "BLOCKED_MISSING_BINDING", f"runtime_resolver_unavailable:{op_ref}:{path}"
        if not method:
            return False, "BLOCKED_MISSING_OPERATION", f"missing_method:{op_ref}"
        if method in {"POST", "PUT", "PATCH", "DELETE"} and not _declared_observation_path(path, ops):
            return False, "BLOCKED_MISSING_OBSERVER", f"write_observer_unresolved:{op_ref}:{path}"
    if not _list(exp.get("observers")):
        return False, "BLOCKED_MISSING_OBSERVER", "none"
    assertion = _dict(_list(exp.get("assertions"))[0] if _list(exp.get("assertions")) else {})
    risk_family = _text(assertion.get("kind") or assertion.get("type"))
    if risk_family == "owner_tenant_visibility":
        risk_family = "authorization"
    observer_reason, observer_detail = validate_observer_declarations(
        [row for row in _list(exp.get("observers")) if isinstance(row, dict)],
        risk_family=risk_family,
        available_adapters={"http_api"},
    )
    if observer_reason:
        return False, observer_reason, observer_detail
    safety = _dict(exp.get("safety_contract"))
    is_write = bool(safety.get("governed_write"))
    if is_write and not _list(exp.get("cleanup_plan")):
        return False, "BLOCKED_NON_REVERSIBLE_WRITE", "cleanup_compensation_unresolved"
    # Fixture nodes that require constructible disposable fixtures must be READY.
    for node in _list(dag.get("nodes")):
        if not isinstance(node, dict):
            continue
        if node.get("constructible") is False:
            return False, "BLOCKED_MISSING_FIXTURE", _text(node.get("node_id"))
        if _text(node.get("kind")) == "disposable_fixture" and not _text(node.get("fixture_id")):
            return False, "BLOCKED_MISSING_FIXTURE", _text(node.get("node_id"))
    return True, "", ""


def _resolve_token(actor: dict[str, Any], tokens: dict[str, str]) -> str:
    role = _text(actor.get("role"))
    secret = _text(actor.get("credential_secret_ref") or actor.get("secret_ref"))
    if role.lower() in {"anonymous", "public"}:
        return ""
    return tokens.get(secret) or tokens.get(role) or ""


def _declared_observation_path(
    path: str,
    operations: dict[str, dict[str, Any]],
    *,
    runtime_bindings: dict[str, Any] | None = None,
) -> str:
    """Return an exact source-declared read observer; never invent one."""
    target = _text(path).split("?", 1)[0].rstrip("/") or "/"
    for operation in operations.values():
        method = _text(operation.get("method")).upper()
        candidate = _text(operation.get("path") or operation.get("raw_path")).split("?", 1)[0].rstrip("/") or "/"
        if method not in {"GET", "HEAD"}:
            continue
        if candidate == target and not path_has_placeholders(candidate):
            return candidate
        materialized = candidate
        for name, value in (runtime_bindings or {}).items():
            materialized = materialized.replace("{" + name + "}", quote(str(value), safe=""))
        if materialized == target and not path_has_placeholders(materialized):
            return materialized
    return ""


def _run_http_step(
    *,
    base_url: str,
    method: str,
    path: str,
    token: str,
    body: Any = None,
) -> dict[str, Any]:
    url = base_url.rstrip("/") + path
    resp = _http_request(method, url, token=token, body=body)
    return {
        "method": method,
        "path": path,
        "status_code": int(resp.get("status") or 0),
        "body": resp.get("body"),
        "headers": resp.get("headers") or {},
        "duration_ms": resp.get("duration_ms"),
        "error": resp.get("error") or "",
        "raw": resp,
    }


def execute_one_experiment(
    experiment: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
    root: Path,
    project: str,
    base_url: str,
    runtime_contract: dict[str, Any],
    campaign_id: str,
    execution_id: str,
    actor_tokens: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Execute one experiment or return an explicit blocked/harness receipt."""
    exp = _dict(experiment)
    eid = _text(exp.get("experiment_id"))
    oid = _text(exp.get("obligation_id"))
    resolved_campaign_id = _text(campaign_id)
    resolved_execution_id = _text(execution_id)
    if not resolved_campaign_id or not resolved_execution_id:
        raise ValueError("experiment execution campaign_id and execution_id are required")
    exp["campaign_id"] = resolved_campaign_id
    exp["execution_id"] = resolved_execution_id
    tokens = actor_tokens if actor_tokens is not None else load_actor_tokens(root, project)
    ok, reason, detail = preflight_experiment_executable(exp, behavior_ir=behavior_ir, actor_tokens=tokens)
    started = time.time()
    if not ok:
        return {
            "schema_version": "qualibug.experiment-execution.v1",
            "experiment_id": eid,
            "obligation_id": oid,
            "status": "BLOCKED",
            "reason_code": reason,
            "detail": detail,
            "elapsed_ms": int((time.time() - started) * 1000),
            "finding": None,
            "execution_receipt": {"status": "BLOCKED", "reason_code": reason, "detail": detail},
        }

    ir = _dict(behavior_ir)
    actors = _index_by_id(_list(ir.get("actors")))
    ops = _index_by_id(_list(ir.get("operations")))
    steps_out: list[dict[str, Any]] = []
    observations: dict[str, Any] = {
        "observer_ids": [],
        "observer_receipts": [],
        "control_succeeded": False,
        "harness_error": False,
    }
    activation_requirements = contract_activation_requirements(exp)
    contract_evidence_receipts: list[dict[str, Any]] = []
    cleanup_failures = 0
    fixture_receipts: list[dict[str, Any]] = []
    binding_materialization_receipts: list[dict[str, Any]] = []
    runtime_bindings: dict[str, Any] = {}
    pending_fixture_cleanups: list[dict[str, Any]] = []
    binding_plan = {
        _text(item.get("target")): item
        for item in _list(exp.get("binding_plan"))
        if isinstance(item, dict) and _text(item.get("target"))
    }
    resolver_actor_ref = ""
    for planned in _list(exp.get("control_plan")) + _list(exp.get("treatment_plan")):
        if isinstance(planned, dict) and _text(planned.get("actor_ref")):
            resolver_actor_ref = _text(planned.get("actor_ref"))
            break
    resolver_actor = actors.get(resolver_actor_ref) or {}
    resolver_token = _resolve_token(resolver_actor, tokens)

    # Fixture DAG: actor contexts are resolved via tokens; disposable fixtures
    # without a concrete create path remain BLOCKED (already caught in preflight
    # when constructible=false). Record READY nodes as resolved receipts.
    dag = _dict(exp.get("fixture_dag"))
    for node_id in _list(dag.get("setup_order")):
        node = next((n for n in _list(dag.get("nodes")) if _text(_dict(n).get("node_id")) == node_id), {})
        kind = _text(_dict(node).get("kind"))
        if kind == "actor_context":
            actor_ref = _text(_dict(node).get("actor_ref"))
            actor = actors.get(actor_ref) or {}
            token = _resolve_token(actor, tokens)
            if _text(actor.get("role")).lower() not in {"anonymous", "public"} and not token:
                return {
                    "schema_version": "qualibug.experiment-execution.v1",
                    "experiment_id": eid,
                    "obligation_id": oid,
                    "status": "BLOCKED",
                    "reason_code": "BLOCKED_MISSING_ACTOR",
                    "detail": f"fixture_actor_unresolved:{actor_ref}",
                    "elapsed_ms": int((time.time() - started) * 1000),
                    "finding": None,
                    "execution_receipt": {"status": "BLOCKED", "reason_code": "BLOCKED_MISSING_ACTOR"},
                }
            fixture_receipts.append({"node_id": node_id, "kind": kind, "status": "resolved"})
        elif kind == "runtime_read_binding":
            target = _text(_dict(node).get("target"))
            binding = binding_plan.get(target) or {}
            resolvers = _validated_runtime_resolvers(binding, ops)
            force_fixture_setup = binding.get("force_fixture_setup") is True
            target_path = _text(binding.get("target_path"))
            value: Any = None
            fixture_setup_accepted = False
            receipt: dict[str, Any] = {
                "target": target,
                "status": "BLOCKED",
                "source_priority": "same_actor_list_read",
                "resolver_path": "",
                "resolver_operation_ref": "",
                "status_code": 0,
                "value_fingerprint": "",
            }
            for index, resolver in enumerate([] if force_fixture_setup else resolvers):
                obs = _run_http_step(
                    base_url=base_url,
                    method=resolver["method"],
                    path=resolver["path"],
                    token=resolver_token,
                )
                obs.update({
                    "phase": "binding_materialization",
                    "step_id": f"bind:{target}:{index}",
                    "actor_ref": resolver_actor_ref,
                    "operation_ref": resolver["operation_ref"],
                })
                steps_out.append(obs)
                receipt.update({
                    "resolver_path": resolver["path"],
                    "resolver_operation_ref": resolver["operation_ref"],
                    "status_code": int(obs.get("status_code") or 0),
                })
                if not (200 <= int(obs.get("status_code") or 0) < 300):
                    if int(obs.get("status_code") or 0) == 0:
                        break
                    continue
                extracted = bind_entity_fields(obs.get("body"), target_path)
                value = extracted.get(target)
                if value in (None, "", [], {}):
                    continue
                runtime_bindings[target] = value
                receipt.update({
                    "status": "BOUND",
                    "value_fingerprint": hashlib.sha256(
                        str(value).encode("utf-8")
                    ).hexdigest()[:12],
                })
                break
            if value in (None, "", [], {}):
                fixture_setup = _validated_fixture_setup(binding, ops, actors)
                fixture_actor_ref = ""
                fixture_actor: dict[str, Any] = {}
                fixture_token = ""
                for actor_ref in _list(fixture_setup.get("actor_refs")):
                    actor = actors.get(_text(actor_ref)) or {}
                    token = _resolve_token(actor, tokens)
                    if _text(actor.get("role")).lower() in {"anonymous", "public"} or token:
                        fixture_actor_ref = _text(actor_ref)
                        fixture_actor = actor
                        fixture_token = token
                        break
                if fixture_setup and not fixture_actor_ref:
                    fixture_setup = {}
                token_values: dict[str, Any] = {}
                dependency_blocked = False
                for dependency in _list(fixture_setup.get("body_bindings")):
                    dependency_target = _text(_dict(dependency).get("target"))
                    dependency_token = _text(_dict(dependency).get("template_token"))
                    dependency_value: Any = None
                    for index, resolver in enumerate(_list(_dict(dependency).get("resolver_operations"))):
                        if not isinstance(resolver, dict):
                            continue
                        obs = _run_http_step(
                            base_url=base_url,
                            method=_text(resolver.get("method")).upper(),
                            path=_text(resolver.get("path")),
                            token=fixture_token,
                        )
                        obs.update({
                            "phase": "binding_materialization_dependency",
                            "step_id": f"bind:{target}:dependency:{dependency_token}:{index}",
                            "actor_ref": fixture_actor_ref,
                            "operation_ref": _text(resolver.get("operation_ref")),
                        })
                        steps_out.append(obs)
                        if not (200 <= int(obs.get("status_code") or 0) < 300):
                            if int(obs.get("status_code") or 0) == 0:
                                break
                            continue
                        dependency_leaf = dependency_target.split(".")[-1].split("[")[0]
                        dependency_value = _runtime_value_from_response(
                            obs.get("body"),
                            dependency_leaf,
                            f"/{{{dependency_leaf}}}",
                        )
                        if dependency_value not in (None, "", [], {}):
                            token_values[dependency_token] = dependency_value
                            break
                    if dependency_value in (None, "", [], {}):
                        dependency_blocked = True
                        break
                if fixture_setup and not dependency_blocked:
                    setup_body = _materialize_body_template(
                        fixture_setup.get("body_template"),
                        token_values,
                    )
                    observation_path = _text(resolvers[0].get("path")) if resolvers else ""
                    governed_setup = execute_governed_control_write(
                        root=root,
                        project=project,
                        base_url=base_url,
                        runtime_contract=runtime_contract,
                        campaign_id=campaign_id,
                        operation_phase="experiment_fixture_setup",
                        actor_identity=_text(fixture_actor.get("role") or fixture_actor_ref),
                        actor_token=fixture_token,
                        method=_text(fixture_setup.get("method")).upper(),
                        path=_text(fixture_setup.get("path")),
                        body=setup_body,
                        observation_path=observation_path,
                    )
                    setup_write = _dict(governed_setup.get("write"))
                    setup_status = int(setup_write.get("status") or 0)
                    fixture_setup_accepted = bool(
                        governed_setup.get("accepted") is True
                        or 200 <= setup_status < 300
                    )
                    steps_out.append({
                        "phase": "fixture_setup",
                        "method": _text(fixture_setup.get("method")).upper(),
                        "path": _text(fixture_setup.get("path")),
                        "status_code": setup_status,
                        "operation_ref": _text(fixture_setup.get("operation_ref")),
                        "governance_receipt": governed_setup,
                    })
                    receipt["fixture_setup_status"] = (
                        "completed" if 200 <= setup_status < 300 else "failed"
                    )
                    if 200 <= setup_status < 300:
                        value = _runtime_setup_value_from_response(
                            setup_write.get("body"),
                            target,
                        )
                    if value not in (None, "", [], {}):
                        ownership_required = force_fixture_setup and bool(
                            _text(binding.get("fixture_owner_actor_ref"))
                        )
                        setup_after = _dict(governed_setup.get("after"))
                        ownership_observed = bool(
                            ownership_required
                            and fixture_actor_ref == _text(binding.get("fixture_owner_actor_ref"))
                            and 200 <= int(setup_after.get("status") or 0) < 300
                            and _body_contains_scalar(setup_after.get("body"), value)
                        )
                        runtime_bindings[target] = value
                        receipt.update({
                            "status": "BOUND",
                            "source_priority": "experiment_setup_response",
                            "resolver_path": _text(fixture_setup.get("path")),
                            "resolver_operation_ref": _text(fixture_setup.get("operation_ref")),
                            "status_code": setup_status,
                            "value_fingerprint": hashlib.sha256(
                                str(value).encode("utf-8")
                            ).hexdigest()[:12],
                            "fixture_cleanup_status": "pending",
                            "fixture_id": _text(binding.get("required_fixture_id")),
                            "owner_actor_ref": _text(binding.get("fixture_owner_actor_ref")),
                            "ownership_proof_status": (
                                "OBSERVED"
                                if ownership_observed
                                else "NOT_REQUIRED"
                                if not ownership_required
                                else "INDETERMINATE"
                            ),
                            "ownership_proof_ref": _text(governed_setup.get("after_ref")),
                        })
                        pending_fixture_cleanups.append({
                            "target": target,
                            "value": value,
                            "observation_path": observation_path,
                            "cleanup": dict(_list(fixture_setup.get("cleanup_operations"))[0]),
                            "receipt": receipt,
                            "actor_ref": fixture_actor_ref,
                            "actor_identity": _text(fixture_actor.get("role") or fixture_actor_ref),
                            "actor_token": fixture_token,
                            "governed_setup": governed_setup,
                        })
            binding_materialization_receipts.append(receipt)
            if value in (None, "", [], {}):
                if fixture_setup_accepted:
                    cleanup_failures += 1
                    receipt.update({
                        "status": "HARNESS_FAILURE",
                        "reason_code": "FIXTURE_SETUP_IDENTITY_UNRESOLVED",
                        "fixture_cleanup_status": "failed",
                    })
                    return {
                        "schema_version": "qualibug.experiment-execution.v1",
                        "experiment_id": eid,
                        "obligation_id": oid,
                        "status": "HARNESS_FAILURE",
                        "reason_code": "FIXTURE_SETUP_IDENTITY_UNRESOLVED",
                        "detail": f"accepted_fixture_identity_unresolved:{target}",
                        "elapsed_ms": int((time.time() - started) * 1000),
                        "steps": steps_out,
                        "fixture_receipts": fixture_receipts,
                        "binding_materialization_receipts": binding_materialization_receipts,
                        "finding": None,
                        "cleanup_failures": cleanup_failures,
                        "execution_receipt": {
                            "status": "HARNESS_FAILURE",
                            "reason_code": "FIXTURE_SETUP_IDENTITY_UNRESOLVED",
                            "cleanup_failures": cleanup_failures,
                        },
                    }
                fixture_receipts.append({
                    "node_id": node_id,
                    "kind": kind,
                    "status": "BLOCKED",
                    "reason_code": "BLOCKED_MISSING_BINDING",
                    "detail": f"runtime_read_binding_unresolved:{target}",
                })
                return {
                    "schema_version": "qualibug.experiment-execution.v1",
                    "experiment_id": eid,
                    "obligation_id": oid,
                    "status": "BLOCKED",
                    "reason_code": "BLOCKED_MISSING_BINDING",
                    "detail": f"runtime_read_binding_unresolved:{target}",
                    "elapsed_ms": int((time.time() - started) * 1000),
                    "steps": steps_out,
                    "fixture_receipts": fixture_receipts,
                    "binding_materialization_receipts": binding_materialization_receipts,
                    "finding": None,
                    "execution_receipt": {
                        "status": "BLOCKED",
                        "reason_code": "BLOCKED_MISSING_BINDING",
                    },
                }
            fixture_receipts.append({
                "node_id": node_id,
                "kind": kind,
                "status": "resolved",
                "target": target,
                "value_fingerprint": receipt["value_fingerprint"],
            })
        elif kind == "disposable_fixture":
            # Without a concrete create operation binding, refuse to invent IDs.
            fixture_receipts.append({
                "node_id": node_id,
                "kind": kind,
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_FIXTURE",
                "detail": "disposable_fixture_create_path_unresolved",
            })
            return {
                "schema_version": "qualibug.experiment-execution.v1",
                "experiment_id": eid,
                "obligation_id": oid,
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_FIXTURE",
                "detail": f"fixture_unresolved:{node_id}",
                "elapsed_ms": int((time.time() - started) * 1000),
                "finding": None,
                "fixture_receipts": fixture_receipts,
                "execution_receipt": {"status": "BLOCKED", "reason_code": "BLOCKED_MISSING_FIXTURE"},
            }
        else:
            fixture_receipts.append({"node_id": node_id, "kind": kind or "unknown", "status": "resolved"})

    for actor_ref in activation_requirements["actor"]:
        actor = actors.get(actor_ref) or {}
        role = _text(actor.get("role"))
        token = _resolve_token(actor, tokens)
        actor_observed = role.lower() in {"anonymous", "public"} or bool(token)
        contract_evidence_receipts.append(build_contract_evidence_receipt(
            kind="actor",
            experiment_id=eid,
            obligation_id=oid,
            campaign_id=resolved_campaign_id,
            execution_id=resolved_execution_id,
            subject_id=actor_ref,
            status="OBSERVED" if actor_observed else "FAILED",
            evidence={
                "role": role,
                "credential_secret_ref_present": bool(
                    _text(actor.get("credential_secret_ref"))
                ),
                "credential_material_observed": bool(token),
            },
        ))
    for fixture_id in activation_requirements["fixture"]:
        fixture = next(
            (
                row for row in fixture_receipts
                if _text(_dict(row).get("node_id")) == fixture_id
            ),
            {},
        )
        fixture_status = _text(_dict(fixture).get("status")).lower()
        observed = fixture_status in {"bound", "completed", "ready", "resolved"}
        contract_evidence_receipts.append(build_contract_evidence_receipt(
            kind="fixture",
            experiment_id=eid,
            obligation_id=oid,
            campaign_id=resolved_campaign_id,
            execution_id=resolved_execution_id,
            subject_id=fixture_id,
            status="OBSERVED" if observed else "FAILED",
            evidence={
                "fixture_kind": _text(_dict(fixture).get("kind")),
                "value_fingerprint": _text(
                    _dict(fixture).get("value_fingerprint")
                ),
            },
        ))

    def _exec_plan(plan: list[Any], *, phase: str) -> list[dict[str, Any]]:
        nonlocal cleanup_failures
        results = []
        planned_subjects = activation_requirements.get(phase) or []
        for index, step in enumerate(plan):
            if not isinstance(step, dict):
                continue
            actor_ref = _text(step.get("actor_ref"))
            op_ref = _text(step.get("operation_ref"))
            subject_id = (
                planned_subjects[index]
                if index < len(planned_subjects)
                else _text(step.get("step_id"))
                or f"{phase}:{op_ref or 'operation'}:{index + 1}"
            )
            actor = actors.get(actor_ref) or {}
            op = ops.get(op_ref) or {}
            method = _text(op.get("method") or "GET").upper()
            path = _materialize_path(
                _text(op.get("path") or op.get("raw_path")),
                runtime_bindings,
            )
            token = _resolve_token(actor, tokens)
            is_write = method in {"POST", "PUT", "PATCH", "DELETE"}
            if is_write:
                allowed, reason = sandbox_write_allowed(
                    root=root,
                    project=project,
                    runtime_contract=runtime_contract,
                    actor_token=token,
                    actor_identity=_text(actor.get("role") or actor_ref),
                )
                if not allowed:
                    observations["harness_error"] = True
                    contract_evidence_receipts.append(
                        build_contract_evidence_receipt(
                            kind=phase,
                            experiment_id=eid,
                            obligation_id=oid,
                            campaign_id=resolved_campaign_id,
                            execution_id=resolved_execution_id,
                            subject_id=subject_id,
                            status="FAILED",
                            evidence={"reason_code": _text(reason)},
                        )
                    )
                    results.append({
                        "phase": phase,
                        "step_id": subject_id,
                        "status": "blocked_write",
                        "reason": reason,
                        "method": method,
                        "path": path,
                    })
                    continue
                observation_path = _declared_observation_path(
                    path,
                    ops,
                    runtime_bindings=runtime_bindings,
                )
                if not observation_path:
                    observations["harness_error"] = True
                    contract_evidence_receipts.append(
                        build_contract_evidence_receipt(
                            kind=phase,
                            experiment_id=eid,
                            obligation_id=oid,
                            campaign_id=resolved_campaign_id,
                            execution_id=resolved_execution_id,
                            subject_id=subject_id,
                            status="FAILED",
                            evidence={"reason_code": "BLOCKED_MISSING_OBSERVER"},
                        )
                    )
                    results.append({
                        "phase": phase,
                        "step_id": subject_id,
                        "status": "blocked_write",
                        "reason": "BLOCKED_MISSING_OBSERVER",
                        "method": method,
                        "path": path,
                    })
                    continue
                governed = execute_governed_control_write(
                    root=root,
                    project=project,
                    base_url=base_url,
                    runtime_contract=runtime_contract,
                    campaign_id=campaign_id,
                    operation_phase=f"experiment_{phase}",
                    actor_identity=_text(actor.get("role") or actor_ref),
                    actor_token=token,
                    method=method,
                    path=path,
                    body=step.get("body") if "body" in step else op.get("request_example"),
                    observation_path=observation_path,
                )
                write_receipt = _dict(governed.get("write"))
                obs = {
                    "method": method,
                    "path": path,
                    "status_code": int(write_receipt.get("status") or 0),
                    "body": write_receipt.get("body"),
                    "headers": write_receipt.get("headers") or {},
                    "duration_ms": write_receipt.get("duration_ms"),
                    "error": write_receipt.get("error") or governed.get("reason") or "",
                    "governance_receipt": governed,
                }
            else:
                obs = _run_http_step(base_url=base_url, method=method, path=path, token=token)
            obs["phase"] = phase
            obs["step_id"] = subject_id
            obs["actor_ref"] = actor_ref
            obs["operation_ref"] = op_ref
            observed_status = int(obs.get("status_code") or 0)
            contract_status = (
                "OBSERVED"
                if phase == "control" and 200 <= observed_status < 300
                else "BLOCKED"
                if phase == "control" and observed_status > 0
                else "OBSERVED"
                if phase == "treatment" and observed_status > 0
                else "FAILED"
            )
            contract_evidence_receipts.append(build_contract_evidence_receipt(
                kind=phase,
                experiment_id=eid,
                obligation_id=oid,
                campaign_id=resolved_campaign_id,
                execution_id=resolved_execution_id,
                subject_id=subject_id,
                status=contract_status,
                evidence={
                    "method": method,
                    "path": path,
                    "status_code": observed_status,
                    "operation_ref": op_ref,
                    "response_observed": observed_status > 0,
                    "control_succeeded": (
                        200 <= observed_status < 300
                        if phase == "control"
                        else None
                    ),
                },
            ))
            results.append(obs)
            if phase == "control":
                observations["control_observation"] = obs
                observations["control_actor_ref"] = actor_ref
                if 200 <= int(obs.get("status_code") or 0) < 300:
                    observations["control_succeeded"] = True
                    observations["authorized_control"] = True
            if phase == "treatment":
                observations["treatment_observation"] = obs
                observations["treatment_result"] = obs
                observations["treatment_actor_ref"] = actor_ref
                observations["status_code"] = obs.get("status_code")
                observations["body"] = obs.get("body")
        return results

    steps_out.extend(_exec_plan(_list(exp.get("control_plan")), phase="control"))
    steps_out.extend(_exec_plan(_list(exp.get("treatment_plan")), phase="treatment"))

    for pending in reversed(pending_fixture_cleanups):
        cleanup = _dict(pending.get("cleanup"))
        cleanup_bindings = dict(runtime_bindings)
        cleanup_placeholders = infer_path_params(_text(cleanup.get("path")))
        if len(cleanup_placeholders) == 1:
            cleanup_bindings.setdefault(cleanup_placeholders[0], pending.get("value"))
        cleanup_path = _materialize_path(_text(cleanup.get("path")), cleanup_bindings)
        governed_cleanup = execute_governed_control_write(
            root=root,
            project=project,
            base_url=base_url,
            runtime_contract=runtime_contract,
            campaign_id=campaign_id,
            operation_phase="experiment_fixture_cleanup",
            actor_identity=_text(pending.get("actor_identity")),
            actor_token=_text(pending.get("actor_token")),
            method=_text(cleanup.get("method")).upper(),
            path=cleanup_path,
            body=None,
            observation_path=_text(pending.get("observation_path")),
        )
        cleanup_write = _dict(governed_cleanup.get("write"))
        cleanup_status = int(cleanup_write.get("status") or 0)
        governed_setup = _dict(pending.get("governed_setup"))
        restoration_verified = _cleanup_restores_governed_write(
            governed_setup,
            governed_cleanup,
        )
        audit_receipt_ids = sorted({
            receipt_id
            for receipt_id in (
                _governance_audit_receipt_id(governed_setup),
                _governance_audit_receipt_id(governed_cleanup),
            )
            if receipt_id
        })
        completed = bool(
            200 <= cleanup_status < 300
            and restoration_verified
            and audit_receipt_ids
        )
        _dict(pending.get("receipt"))["fixture_cleanup_status"] = (
            "completed" if completed else "failed"
        )
        if not completed:
            cleanup_failures += 1
        contract_evidence_receipts.append(build_contract_evidence_receipt(
            kind="cleanup",
            experiment_id=eid,
            obligation_id=oid,
            campaign_id=resolved_campaign_id,
            execution_id=resolved_execution_id,
            subject_id=f"fixture_cleanup:{_text(pending.get('target'))}",
            status="COMPLETED" if completed else "FAILED",
            evidence={
                "method": _text(cleanup.get("method")).upper(),
                "path": cleanup_path,
                "status_code": cleanup_status,
                "operation_ref": _text(cleanup.get("operation_ref")),
                "accepted_write_count": 1,
                "cleanup_write_count": 1 if governed_cleanup.get("accepted") is True else 0,
                "restoration_verified": restoration_verified,
                "state_unchanged": restoration_verified,
                "audit_receipt_ids": audit_receipt_ids,
            },
        ))
        steps_out.append({
            "phase": "fixture_cleanup",
            "cleanup_subject_id": f"fixture_cleanup:{_text(pending.get('target'))}",
            "method": _text(cleanup.get("method")).upper(),
            "path": cleanup_path,
            "status_code": cleanup_status,
            "operation_ref": _text(cleanup.get("operation_ref")),
            "governance_receipt": governed_cleanup,
        })
    if pending_fixture_cleanups:
        observations["cleanup_status"] = "failed" if cleanup_failures else "completed"

    # Cleanup compensation in reverse order for write experiments.
    safety = _dict(exp.get("safety_contract"))
    governed_write_attempts = _governed_write_attempts(steps_out)
    accepted_governed_writes = [
        attempt
        for attempt in governed_write_attempts
        if attempt.get("accepted") is True
    ]
    if (
        safety.get("governed_write")
        and _list(exp.get("cleanup_plan"))
        and not accepted_governed_writes
    ):
        rejected_state_unchanged = _rejected_writes_left_state_unchanged(
            governed_write_attempts
        )
        rejected_audit_ids = sorted({
            receipt_id
            for receipt_id in (
                _governance_audit_receipt_id(attempt)
                for attempt in governed_write_attempts
            )
            if receipt_id
        })
        for cleanup_subject in activation_requirements["cleanup"]:
            contract_evidence_receipts.append(build_contract_evidence_receipt(
                kind="cleanup",
                experiment_id=eid,
                obligation_id=oid,
                campaign_id=resolved_campaign_id,
                execution_id=resolved_execution_id,
                subject_id=cleanup_subject,
                status="NOT_REQUIRED" if rejected_state_unchanged else "FAILED",
                evidence={
                    "accepted_write_count": 0,
                    "cleanup_write_count": 0,
                    "state_unchanged": rejected_state_unchanged,
                    "audit_receipt_ids": rejected_audit_ids,
                    "reason_code": (
                        "NO_ACCEPTED_WRITE"
                        if rejected_state_unchanged
                        else "REJECTED_WRITE_STATE_NOT_PROVEN_UNCHANGED"
                    ),
                },
            ))
        observations["cleanup_status"] = (
            "not_required" if rejected_state_unchanged else "failed"
        )
        if not rejected_state_unchanged:
            cleanup_failures += 1
    if (
        safety.get("governed_write")
        and _list(exp.get("cleanup_plan"))
        and accepted_governed_writes
    ):
        cleanup_plan = _list(exp.get("cleanup_plan"))
        cleanup_subjects = activation_requirements.get("cleanup") or []
        for cleanup_index in reversed(range(len(cleanup_plan))):
            cleanup = cleanup_plan[cleanup_index]
            cleanup_subject_id = (
                cleanup_subjects[cleanup_index]
                if cleanup_index < len(cleanup_subjects)
                else f"cleanup:operation:{cleanup_index + 1}"
            )
            # Compensation is declared; without a concrete reverse operation we
            # record an honest cleanup failure rather than inventing success.
            op_ref = _text(_dict(cleanup).get("operation_ref"))
            op = ops.get(op_ref) or {}
            path_template = _text(_dict(cleanup).get("path") or op.get("path") or op.get("raw_path"))
            method = _text(op.get("method") or "").upper()
            cleanup_targets, missing_bindings = _runtime_cleanup_paths(path_template, steps_out)
            if missing_bindings or not cleanup_targets:
                cleanup_failures += 1
                observations["cleanup_status"] = "failed"
                observations["cleanup_reason"] = (
                    f"cleanup_binding_unresolved:{','.join(missing_bindings)}"
                    if missing_bindings
                    else "cleanup_accepted_write_missing"
                )
                continue
            # Prefer first control/treatment actor token for cleanup.
            actor_ref = ""
            for step in _list(exp.get("control_plan")) + _list(exp.get("treatment_plan")):
                if isinstance(step, dict) and _text(step.get("actor_ref")):
                    actor_ref = _text(step.get("actor_ref"))
                    break
            actor = actors.get(actor_ref) or {}
            token = _resolve_token(actor, tokens)
            cleanup_method = method
            allowed, reason = sandbox_write_allowed(
                root=root,
                project=project,
                runtime_contract=runtime_contract,
                actor_token=token,
                actor_identity=_text(actor.get("role") or actor_ref),
            )
            if not allowed:
                cleanup_failures += 1
                observations["cleanup_status"] = "failed"
                observations["cleanup_reason"] = reason
                continue
            for path, target_bindings in reversed(cleanup_targets):
                if not path.startswith("/") or path_has_placeholders(path) or method not in {"DELETE", "POST", "PUT", "PATCH"}:
                    cleanup_failures += 1
                    observations["cleanup_status"] = "failed"
                    observations["cleanup_reason"] = "cleanup_compensation_unresolved"
                    continue
                observation_path = _declared_observation_path(
                    path,
                    ops,
                    runtime_bindings={**runtime_bindings, **target_bindings},
                )
                if not observation_path:
                    cleanup_failures += 1
                    observations["cleanup_status"] = "failed"
                    observations["cleanup_reason"] = "cleanup_observer_unresolved"
                    continue
                governed_cleanup = execute_governed_control_write(
                    root=root,
                    project=project,
                    base_url=base_url,
                    runtime_contract=runtime_contract,
                    campaign_id=campaign_id,
                    operation_phase="experiment_cleanup",
                    actor_identity=_text(actor.get("role") or actor_ref),
                    actor_token=token,
                    method=cleanup_method,
                    path=path,
                    body=_dict(cleanup).get("body"),
                    observation_path=observation_path,
                )
                cleanup_write = _dict(governed_cleanup.get("write"))
                cobs = {
                    "method": cleanup_method,
                    "path": path,
                    "status_code": int(cleanup_write.get("status") or 0),
                    "body": cleanup_write.get("body"),
                    "headers": cleanup_write.get("headers") or {},
                    "duration_ms": cleanup_write.get("duration_ms"),
                    "error": cleanup_write.get("error") or governed_cleanup.get("reason") or "",
                    "governance_receipt": governed_cleanup,
                }
                steps_out.append({
                    **cobs,
                    "phase": "cleanup",
                    "operation_ref": op_ref,
                    "cleanup_subject_id": cleanup_subject_id,
                })
                if not (200 <= int(cobs.get("status_code") or 0) < 300):
                    cleanup_failures += 1
                    observations["cleanup_status"] = "failed"
                elif not cleanup_failures:
                    observations["cleanup_status"] = "completed"

    recorded_cleanup_subjects = {
        _text(receipt.get("subject_id"))
        for receipt in contract_evidence_receipts
        if _text(receipt.get("kind")) == "cleanup"
    }
    for cleanup_subject in activation_requirements["cleanup"]:
        if cleanup_subject in recorded_cleanup_subjects:
            continue
        matching_steps = [
            step for step in steps_out
            if _text(_dict(step).get("cleanup_subject_id")) == cleanup_subject
        ]
        cleanup_governance_receipts = [
            _dict(step.get("governance_receipt"))
            for step in matching_steps
            if isinstance(step.get("governance_receipt"), dict)
        ]
        restoration_verified = bool(accepted_governed_writes) and all(
            any(
                _cleanup_restores_governed_write(original, cleanup)
                for cleanup in cleanup_governance_receipts
            )
            for original in accepted_governed_writes
        )
        audit_receipt_ids = sorted({
            receipt_id
            for receipt_id in (
                _governance_audit_receipt_id(governed)
                for governed in [
                    *accepted_governed_writes,
                    *cleanup_governance_receipts,
                ]
            )
            if receipt_id
        })
        cleanup_statuses_succeeded = bool(matching_steps) and all(
            200 <= int(_dict(step).get("status_code") or 0) < 300
            for step in matching_steps
        )
        completed = (
            cleanup_statuses_succeeded
            and restoration_verified
            and bool(audit_receipt_ids)
        )
        if cleanup_statuses_succeeded and not completed:
            cleanup_failures += 1
            observations["cleanup_status"] = "failed"
        contract_evidence_receipts.append(build_contract_evidence_receipt(
            kind="cleanup",
            experiment_id=eid,
            obligation_id=oid,
            campaign_id=resolved_campaign_id,
            execution_id=resolved_execution_id,
            subject_id=cleanup_subject,
            status="COMPLETED" if completed else "FAILED",
            evidence={
                "step_count": len(matching_steps),
                "status_codes": [
                    int(_dict(step).get("status_code") or 0)
                    for step in matching_steps
                ],
                "accepted_write_count": len(accepted_governed_writes),
                "cleanup_write_count": sum(
                    1
                    for receipt in cleanup_governance_receipts
                    if receipt.get("accepted") is True
                ),
                "restoration_verified": restoration_verified,
                "state_unchanged": restoration_verified,
                "audit_receipt_ids": audit_receipt_ids,
            },
        ))

    # A declared observer is satisfied only by an OBSERVED typed receipt.
    observations["execution_steps"] = steps_out
    observations["campaign_id"] = resolved_campaign_id
    observations["execution_id"] = resolved_execution_id
    observations["binding_materialization_receipts"] = binding_materialization_receipts
    observer_receipts = observe_experiment_requirements(
        exp,
        observations=observations,
        campaign_id=resolved_campaign_id,
        execution_id=resolved_execution_id,
    )
    observations["observer_receipts"] = observer_receipts
    observed_ids: list[str] = []
    for receipt in observer_receipts:
        observer_id = _text(receipt.get("observer_id"))
        if _text(receipt.get("status")).upper() == "OBSERVED" and observer_id:
            observed_ids.append(observer_id)
            observations[observer_id] = True
            observations[observer_id + "_observation"] = receipt
        if observer_id == "authorization_comparison":
            observations.update(_dict(receipt.get("evidence")))
            observations["observer_receipt"] = receipt
        elif observer_id == "business_effect":
            observations.update(_dict(receipt.get("evidence")))
            observations["business_effect_observer_receipt"] = receipt
    observations["observer_ids"] = list(dict.fromkeys(observed_ids))
    observations["contract_evidence_receipts"] = list(contract_evidence_receipts)
    observations["fixture_receipts"] = list(fixture_receipts)
    observations["source_refs"] = [
        dict(item) for item in _list(exp.get("source_refs")) if isinstance(item, dict)
    ]
    observations["cleanup_failures"] = cleanup_failures

    # Materialize + evaluate typed assertions via contract oracle.
    assertions = []
    for raw in _list(exp.get("assertions")):
        if isinstance(raw, dict):
            assertions.append(materialize_assertion(raw, observations=observations))
    exp_for_oracle = dict(exp)
    exp_for_oracle["assertions"] = assertions
    verdict = evaluate_contract_oracle(experiment=exp_for_oracle, evidence=observations)

    finding = None
    status = "EXECUTED"
    if verdict.get("verdict") == "harness_failure":
        status = "HARNESS_FAILURE"
    elif verdict.get("verdict") == "blocked_experiment":
        status = "BLOCKED"
        reason = "BLOCKED_MISSING_OBSERVER"
        detail = ",".join(_list(verdict.get("missing_requirements"))[:8])
        return {
            "schema_version": "qualibug.experiment-execution.v1",
            "experiment_id": eid,
            "obligation_id": oid,
            "status": status,
            "reason_code": reason,
            "detail": detail,
            "elapsed_ms": int((time.time() - started) * 1000),
            "steps": steps_out,
            "fixture_receipts": fixture_receipts,
            "binding_materialization_receipts": binding_materialization_receipts,
            "observer_receipts": observer_receipts,
            "contract_evidence_receipts": contract_evidence_receipts,
            "oracle_verdict": verdict,
            "finding": None,
            "cleanup_failures": cleanup_failures,
            "execution_receipt": {"status": status, "reason_code": reason, "detail": detail},
        }
    elif (
        verdict.get("status") == "VIOLATION"
        and verdict.get("customer_deliverable_candidate") is True
        and verdict.get("verdict") == "customer_deliverable_defect_candidate"
    ):
        failed = _list(verdict.get("failed_assertions"))
        first = _dict(failed[0] if failed else {})
        treatment_plan = [step for step in _list(exp.get("treatment_plan")) if isinstance(step, dict)]
        control_plan = [step for step in _list(exp.get("control_plan")) if isinstance(step, dict)]
        primary_plan_step = _dict(treatment_plan[0] if treatment_plan else control_plan[0] if control_plan else {})
        primary_op = ops.get(_text(primary_plan_step.get("operation_ref"))) or {}
        primary_method = _text(primary_op.get("method") or "GET").upper()
        treatment_observation = _dict(observations.get("treatment_observation"))
        primary_path = _text(
            treatment_observation.get("path")
            or _materialize_path(
                _text(primary_op.get("path") or primary_op.get("raw_path")),
                runtime_bindings,
            )
        )
        treatment_actor = actors.get(_text(primary_plan_step.get("actor_ref"))) or {}
        treatment_role = _text(treatment_actor.get("role") or primary_plan_step.get("actor_ref"))
        control_step = _dict(control_plan[0] if control_plan else {})
        control_actor = actors.get(_text(control_step.get("actor_ref"))) or {}
        control_role = _text(control_actor.get("role") or control_step.get("actor_ref"))
        assertion_kind = _text(first.get("kind") or "contract")
        control_observation = _dict(observations.get("control_observation"))
        observed_status = int(treatment_observation.get("status_code") or 0)
        observed_body = treatment_observation.get("body")
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        reproduction_steps = [
            f"{_text(step.get('method')).upper()} {_text(step.get('path'))} -> HTTP {int(step.get('status_code') or 0)}"
            for step in steps_out
            if isinstance(step, dict) and _text(step.get("method")) and _text(step.get("path"))
        ]
        finding = {
            "severity": "P1",
            "title": f"[ContractOracle] {assertion_kind}: {treatment_role or 'actor'} {primary_method} {primary_path}",
            "category": assertion_kind,
            "source": "experiment_contract_oracle",
            "description": _text(
                first.get("error")
                or f"control={control_role or 'unspecified'} succeeded; treatment={treatment_role or 'unspecified'} violated the typed assertion"
            ),
            "confidence_score": 0.85,
            "experiment_id": eid,
            "obligation_id": oid,
            "campaign_id": campaign_id,
            "source_refs": [dict(item) for item in _list(exp.get("source_refs")) if isinstance(item, dict)],
            "timestamp": timestamp,
            "execution_status": "executed",
            "confirmation_status": "candidate",
            "gate_passed": False,
            "bug_status": "suspected",
            "customer_delivery_status": "candidate",
            "oracle": {
                "oracle_name": "ContractOracle",
                "oracle_tier": "contract",
                "customer_deliverable": False,
                "customer_deliverable_candidate": True,
                "verdict": verdict.get("verdict"),
                "status": verdict.get("status"),
                "receipt_id": verdict.get("receipt_id"),
                "activation_receipt_id": verdict.get("activation_receipt_id"),
            },
            "oracle_receipt_id": verdict.get("receipt_id"),
            "activation_receipt_id": verdict.get("activation_receipt_id"),
            "expected": first.get("expected"),
            "actual": first.get("actual"),
            "evidence": {
                "request": f"{primary_method} {primary_path}",
                "response": f"HTTP {observed_status}",
                "target": primary_path,
                "actor": treatment_role,
                "timestamp": timestamp,
                "reproduction_steps": reproduction_steps,
                "execution_semantics": "read_only" if primary_method in {"GET", "HEAD", "OPTIONS"} else "governed_write",
                "control_succeeded": observations.get("control_succeeded"),
                "effect_count": observations.get("effect_count"),
                "invariant_held": observations.get("invariant_held"),
                "assertion": first,
                "cleanup_status": observations.get("cleanup_status"),
            },
            "raw_evidence": {
                "has_real_evidence": True,
                "timestamp": timestamp,
                "request_raw": {"method": primary_method, "path": primary_path, "actor": treatment_role},
                "response_raw": {"status_code": observed_status, "body": observed_body},
                "db_snapshot": {
                    "before": control_observation.get("body"),
                    "after": observed_body,
                    "assertion": first,
                },
                "control_actor": control_role,
                "treatment_actor": treatment_role,
                "steps": steps_out[:20],
                "observations": {
                    k: observations[k]
                    for k in (
                        "status_code",
                        "control_succeeded",
                        "viewer_can_access",
                        "leak_detected",
                        "cleanup_status",
                    )
                    if k in observations
                },
            },
            "reproduction": {
                "method": primary_method,
                "path": primary_path,
                "actor": treatment_role,
                "reproduction_steps": reproduction_steps,
            },
            "reproduction_steps": reproduction_steps,
            "evidence_quality": {
                "level": "executed_candidate",
                "score": 0,
                "can_reproduce": False,
                "evidence_strength": "typed_contract_violation_pending_gate",
            },
            "evidence_status": {
                "semantic_verdict": "ASSERTION_VIOLATION",
                "business_evidence_status": "PENDING_DELIVERY_GATE",
                "final_review_status": "PENDING_DELIVERY_GATE",
                "missing_requirements": ["independent_delivery_gate_receipt"],
            },
            "final_review_status": "PENDING_DELIVERY_GATE",
            "business_evidence_status": "PENDING_DELIVERY_GATE",
            "failed_assertions": failed,
            "cleanup_failures": cleanup_failures,
            "contract_evidence_receipt_ids": [
                _text(receipt.get("receipt_id"))
                for receipt in contract_evidence_receipts
            ],
        }
        # The executor authors evidence and a candidate only.  It never authors
        # the independent delivery decision that consumes this receipt chain.
        if cleanup_failures:
            finding = mark_as_internal_clue(finding, reason="cleanup_compensation_failed")
    elif verdict.get("verdict") == "executed_clue":
        finding = mark_as_internal_clue(
            {
                "severity": "P2",
                "title": f"[ContractOracle clue] {oid}",
                "source": "experiment_contract_oracle",
                "experiment_id": eid,
                "obligation_id": oid,
                "campaign_id": campaign_id,
                "execution_status": "executed",
                "oracle": {"oracle_name": "ContractOracle", "oracle_tier": "internal_clue"},
                "evidence": {"demotion_reason": verdict.get("demotion_reason")},
                "raw_evidence": {"has_real_evidence": True, "steps": steps_out[:10]},
            },
            reason=_text(verdict.get("demotion_reason") or "executed_clue"),
        )

    return {
        "schema_version": "qualibug.experiment-execution.v1",
        "experiment_id": eid,
        "obligation_id": oid,
        "status": status,
        "reason_code": "",
        "detail": "",
        "elapsed_ms": int((time.time() - started) * 1000),
        "steps": steps_out,
        "fixture_receipts": fixture_receipts,
        "binding_materialization_receipts": binding_materialization_receipts,
        "observer_receipts": observer_receipts,
        "contract_evidence_receipts": contract_evidence_receipts,
        "oracle_verdict": verdict,
        "finding": finding,
        "cleanup_failures": cleanup_failures,
        "execution_receipt": {
            "status": status,
            "steps": len(steps_out),
            "cleanup_failures": cleanup_failures,
            "binding_materialization_receipts": len(binding_materialization_receipts),
            "verdict": verdict.get("verdict"),
        },
    }


def execute_selected_experiments(
    selected: list[Any],
    *,
    experiments_by_obligation: dict[str, dict[str, Any]],
    behavior_ir: dict[str, Any],
    root: Path,
    project: str,
    base_url: str,
    runtime_contract: dict[str, Any],
    campaign_id: str = "",
) -> dict[str, Any]:
    """Execute every selected experiment; each yields EXECUTED or BLOCKED receipt."""
    selected_ids = [_text(_dict(item).get("obligation_id")) for item in selected]
    if not all(selected_ids) or len(selected_ids) != len(set(selected_ids)):
        raise ValueError("selected_obligation_identity_invalid")
    tokens = load_actor_tokens(root, project)
    results: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    blocked = 0
    executed = 0
    harness = 0
    cleanup_failures = 0
    compile_results: dict[str, dict[str, Any]] = {}
    execution_results: dict[str, dict[str, Any]] = {}
    gate_results: dict[str, dict[str, Any]] = {}
    batch_nonce = str(time.time_ns())
    for index, item in enumerate(selected):
        row = _dict(item)
        oid = _text(row.get("obligation_id"))
        eid = _text(row.get("experiment_id"))
        candidate_id = _text(row.get("candidate_id")) or _stable_id("cand", project, oid or index)
        slice_id = _text(row.get("slice_id") or row.get("behavior_slice_id")) or _stable_id("slice", project, oid or candidate_id)
        execution_id = _stable_id("exec", project, campaign_id, eid, oid, batch_nonce, index)
        evidence_id = _stable_id("evidence", execution_id)
        exp = experiments_by_obligation.get(oid)
        if not isinstance(exp, dict):
            blocked += 1
            compile_results[oid] = {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_OPERATION",
                "experiment_id": eid,
                "receipt_id": _stable_id("compile", project, campaign_id, oid, batch_nonce),
            }
            missing_outcome = {
                "schema_version": "qualibug.experiment-execution.v1",
                "candidate_id": candidate_id,
                "slice_id": slice_id,
                "obligation_id": oid,
                "experiment_id": eid,
                "execution_id": execution_id,
                "evidence_id": evidence_id,
                "campaign_id": campaign_id,
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_OPERATION",
                "detail": "selected_obligation_has_no_compiled_experiment",
                "finding": None,
                "execution_receipt": {
                    "status": "BLOCKED",
                    "candidate_id": candidate_id,
                    "slice_id": slice_id,
                    "obligation_id": oid,
                    "experiment_id": eid,
                    "execution_id": execution_id,
                    "evidence_id": evidence_id,
                    "campaign_id": campaign_id,
                },
            }
            results.append(missing_outcome)
            continue
        compile_receipt = _dict(exp.get("compile_receipt"))
        compile_status = _text(compile_receipt.get("status")).upper()
        if compile_status != "COMPILED":
            terminal_status = (
                compile_status
                if compile_status in {"BLOCKED", "DEFERRED", "HARNESS_FAILED"}
                else "HARNESS_FAILED"
            )
            reason_code = _text(compile_receipt.get("reason_code")) or (
                "COMPILE_RECEIPT_MISSING"
                if not compile_status
                else f"COMPILE_STATUS_INVALID:{compile_status}"
            )
            compile_results[oid] = {
                "status": terminal_status,
                "reason_code": reason_code,
                "experiment_id": _text(exp.get("experiment_id") or eid),
                "receipt_id": _text(compile_receipt.get("receipt_id"))
                or _stable_id("compile", project, campaign_id, oid, batch_nonce),
            }
            results.append({
                "schema_version": "qualibug.experiment-execution.v1",
                "candidate_id": candidate_id,
                "slice_id": slice_id,
                "obligation_id": oid,
                "experiment_id": _text(exp.get("experiment_id") or eid),
                "execution_id": execution_id,
                "evidence_id": evidence_id,
                "campaign_id": campaign_id,
                "status": terminal_status,
                "reason_code": reason_code,
                "detail": "experiment_compile_receipt_not_executable",
                "finding": None,
                "execution_receipt": {
                    "status": terminal_status,
                    "reason_code": reason_code,
                    "obligation_id": oid,
                    "experiment_id": _text(exp.get("experiment_id") or eid),
                    "campaign_id": campaign_id,
                },
            })
            if terminal_status == "BLOCKED":
                blocked += 1
            else:
                harness += 1
            continue
        compile_results[oid] = {
            "status": "COMPILED",
            "reason_code": "",
            "experiment_id": _text(exp.get("experiment_id") or eid),
            "receipt_id": _text(compile_receipt.get("receipt_id"))
            or _stable_id("compile", project, campaign_id, oid, batch_nonce),
            "input_fingerprint": _text(compile_receipt.get("input_fingerprint")),
        }
        outcome = execute_one_experiment(
            exp,
            behavior_ir=behavior_ir,
            root=root,
            project=project,
            base_url=base_url,
            runtime_contract=runtime_contract,
            campaign_id=campaign_id,
            execution_id=execution_id,
            actor_tokens=tokens,
        )
        eid = _text(outcome.get("experiment_id")) or eid
        outcome.update({
            "candidate_id": candidate_id,
            "slice_id": slice_id,
            "obligation_id": oid,
            "experiment_id": eid,
            "execution_id": execution_id,
            "evidence_id": evidence_id,
            "campaign_id": campaign_id,
        })
        receipt = _dict(outcome.get("execution_receipt"))
        receipt.update({
            "candidate_id": candidate_id,
            "slice_id": slice_id,
            "obligation_id": oid,
            "experiment_id": eid,
            "execution_id": execution_id,
            "evidence_id": evidence_id,
            "campaign_id": campaign_id,
        })
        outcome["execution_receipt"] = receipt
        if isinstance(outcome.get("finding"), dict):
            finding = outcome["finding"]
            finding_id = _text(finding.get("id") or finding.get("finding_id")) or _stable_id("finding", evidence_id)
            finding.update({
                "id": finding_id,
                "finding_id": finding_id,
                "candidate_id": candidate_id,
                "behavior_slice_id": slice_id,
                "slice_id": slice_id,
                "obligation_id": oid,
                "experiment_id": eid,
                "execution_id": execution_id,
                "evidence_id": evidence_id,
                "campaign_id": campaign_id,
            })
            finding_evidence = _dict(finding.get("evidence"))
            finding_evidence["evidence_id"] = evidence_id
            finding_evidence["execution_id"] = execution_id
            finding["evidence"] = finding_evidence
        observation_receipt_ids: list[str] = []
        for step_index, step in enumerate(_list(outcome.get("steps"))):
            if not isinstance(step, dict):
                continue
            observation_receipt_id = _stable_id(
                "observation",
                execution_id,
                step_index,
                step.get("phase"),
                step.get("method"),
                step.get("path"),
            )
            step["observation_receipt_id"] = observation_receipt_id
            observation_receipt_ids.append(observation_receipt_id)
        for observer_receipt in _list(outcome.get("observer_receipts")):
            if not isinstance(observer_receipt, dict):
                continue
            observer_receipt_id = _text(observer_receipt.get("receipt_id"))
            if observer_receipt_id:
                observation_receipt_ids.append(observer_receipt_id)
        for contract_receipt in _list(outcome.get("contract_evidence_receipts")):
            if not isinstance(contract_receipt, dict):
                continue
            contract_receipt_id = _text(contract_receipt.get("receipt_id"))
            if contract_receipt_id:
                observation_receipt_ids.append(contract_receipt_id)
        observation_receipt_ids = list(dict.fromkeys(observation_receipt_ids))
        oracle_verdict = _dict(outcome.get("oracle_verdict"))
        oracle_receipt_id = ""
        if oracle_verdict:
            validated_oracle = validate_contract_oracle_receipt(oracle_verdict)
            oracle_receipt_id = _text(validated_oracle.get("receipt_id"))
            outcome["oracle_verdict"] = validated_oracle
        status = _text(outcome.get("status")).upper()
        if status not in {"EXECUTED", "BLOCKED", "HARNESS_FAILURE", "HARNESS_FAILED"}:
            raise ValueError(f"experiment_execution_status_invalid:{status or 'MISSING'}")
        outcome_cleanup_failures = int(outcome.get("cleanup_failures") or 0)
        if outcome_cleanup_failures:
            status = "HARNESS_FAILURE"
            outcome["status"] = status
            outcome["reason_code"] = "CLEANUP_COMPENSATION_FAILED"
            execution_receipt = _dict(outcome.get("execution_receipt"))
            execution_receipt["status"] = status
            execution_receipt["reason_code"] = "CLEANUP_COMPENSATION_FAILED"
            execution_receipt["cleanup_failures"] = outcome_cleanup_failures
            outcome["execution_receipt"] = execution_receipt
        elif status == "HARNESS_FAILED":
            status = "HARNESS_FAILURE"
            outcome["status"] = status
        operational_receipt = build_execution_operational_receipt(
            receipt_id=_stable_id("operational", execution_id),
            execution_status=status,
            steps=[
                row
                for row in _list(outcome.get("steps"))
                if isinstance(row, dict)
            ],
            cleanup_failures=outcome_cleanup_failures,
        )
        outcome["operational_receipt"] = operational_receipt
        outcome_execution_receipt = _dict(outcome.get("execution_receipt"))
        outcome_execution_receipt["operational_receipt_id"] = _text(
            operational_receipt.get("receipt_id")
        )
        outcome["execution_receipt"] = outcome_execution_receipt
        results.append(outcome)
        if status == "BLOCKED":
            blocked += 1
            execution_results[oid] = {
                "status": "BLOCKED",
                "reason_code": _text(outcome.get("reason_code"))
                or "BLOCKED_EXECUTION",
                "experiment_id": eid,
                "execution_id": execution_id,
                "receipt_id": execution_id,
                "observation_receipt_ids": observation_receipt_ids,
                "oracle_receipt_id": oracle_receipt_id,
                "elapsed_ms": outcome.get("elapsed_ms"),
                "operational_receipt": operational_receipt,
            }
        elif status == "HARNESS_FAILURE":
            harness += 1
            execution_results[oid] = {
                "status": "HARNESS_FAILED",
                "reason_code": _text(outcome.get("reason_code"))
                or "HARNESS_FAILURE",
                "experiment_id": eid,
                "execution_id": execution_id,
                "receipt_id": execution_id,
                "observation_receipt_ids": observation_receipt_ids,
                "oracle_receipt_id": oracle_receipt_id,
                "elapsed_ms": outcome.get("elapsed_ms"),
                "operational_receipt": operational_receipt,
            }
        else:
            executed += 1
            execution_results[oid] = {
                "status": "EXECUTED",
                "reason_code": "",
                "experiment_id": eid,
                "execution_id": execution_id,
                "receipt_id": execution_id,
                "observation_receipt_ids": observation_receipt_ids,
                "oracle_receipt_id": oracle_receipt_id,
                "elapsed_ms": outcome.get("elapsed_ms"),
                "cost_coverage_status": "UNKNOWN",
                "operational_receipt": operational_receipt,
            }
            gate_results[oid] = build_customer_delivery_gate_receipt(
                outcome.get("finding") if isinstance(outcome.get("finding"), dict) else None,
                obligation_id=oid,
                execution_id=execution_id,
            )
        cleanup_failures += outcome_cleanup_failures
        if status == "EXECUTED" and isinstance(outcome.get("finding"), dict):
            findings.append(outcome["finding"])
    return {
        "schema_version": "qualibug.experiment-execution-batch.v1",
        "selected_count": len(selected),
        "executed_count": executed,
        "blocked_count": blocked,
        "harness_failure_count": harness,
        "cleanup_failures": cleanup_failures,
        "findings": findings,
        "results": results,
        "compile_results": compile_results,
        "execution_results": execution_results,
        "gate_results": gate_results,
        "every_experiment_has_receipt": all(
            isinstance(item.get("execution_receipt"), dict) for item in results
        ),
    }
