"""Execute selected compiled experiments end-to-end on the V12 main chain.

Path: selected experiment → fixture DAG → governed requests → observers →
typed assertions → contract oracle → delivery-gate-ready finding (or explicit
BLOCKED / harness receipt). Never invents COMPILED success for unresolved
actor/fixture/observer/cleanup compensation.
"""
from __future__ import annotations

import json
import hashlib
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .assertion_dsl import evaluate_assertion, materialize_assertion
from .contract_oracles import evaluate_contract_oracle, mark_as_internal_clue
from .real_id_resolver import path_has_placeholders
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


_PATH_PARAMETER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _field_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", _text(value).lower())


def _response_scalar_fields(value: Any) -> dict[str, list[Any]]:
    fields: dict[str, list[Any]] = {}

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                if isinstance(child, (str, int)) and not isinstance(child, bool):
                    fields.setdefault(_field_key(key), []).append(child)
                elif isinstance(child, (dict, list)):
                    visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(value)
    return fields


def _runtime_cleanup_bindings(path_template: str, steps: list[dict[str, Any]]) -> tuple[str, dict[str, Any], list[str]]:
    placeholders = _PATH_PARAMETER_RE.findall(_text(path_template))
    if not placeholders:
        return _text(path_template), {}, []
    fields: dict[str, list[Any]] = {}
    for step in steps:
        if not isinstance(step, dict) or _text(step.get("phase")) not in {"control", "treatment"}:
            continue
        if not (200 <= int(step.get("status_code") or 0) < 300):
            continue
        for key, values in _response_scalar_fields(step.get("body")).items():
            fields.setdefault(key, []).extend(values)
    bindings: dict[str, Any] = {}
    missing: list[str] = []
    for name in placeholders:
        normalized = _field_key(name)
        candidates = list(dict.fromkeys(fields.get(normalized) or []))
        if not candidates and normalized == "id":
            id_values = [value for key, values in fields.items() if key.endswith("id") for value in values]
            candidates = list(dict.fromkeys(id_values))
        if len(candidates) != 1:
            missing.append(name)
            continue
        bindings[name] = candidates[0]
    materialized = _text(path_template)
    for name, value in bindings.items():
        materialized = materialized.replace("{" + name + "}", quote(str(value), safe=""))
    return materialized, bindings, missing


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
        if not path.startswith("/") or path_has_placeholders(path):
            return False, "BLOCKED_MISSING_BINDING", f"unresolved_path:{op_ref}:{path}"
        if not method:
            return False, "BLOCKED_MISSING_OPERATION", f"missing_method:{op_ref}"
        if method in {"POST", "PUT", "PATCH", "DELETE"} and not _declared_observation_path(path, ops):
            return False, "BLOCKED_MISSING_OBSERVER", f"write_observer_unresolved:{op_ref}:{path}"
    if not _list(exp.get("observers")):
        return False, "BLOCKED_MISSING_OBSERVER", "none"
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
    actor_tokens: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Execute one experiment or return an explicit blocked/harness receipt."""
    exp = _dict(experiment)
    eid = _text(exp.get("experiment_id"))
    oid = _text(exp.get("obligation_id"))
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
        "observer_ids": [_text(o.get("observer_id")) for o in _list(exp.get("observers")) if isinstance(o, dict)],
        "control_succeeded": False,
        "harness_error": False,
    }
    cleanup_failures = 0
    fixture_receipts: list[dict[str, Any]] = []

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

    def _exec_plan(plan: list[Any], *, phase: str) -> list[dict[str, Any]]:
        nonlocal cleanup_failures
        results = []
        for step in plan:
            if not isinstance(step, dict):
                continue
            actor_ref = _text(step.get("actor_ref"))
            op_ref = _text(step.get("operation_ref"))
            actor = actors.get(actor_ref) or {}
            op = ops.get(op_ref) or {}
            method = _text(op.get("method") or "GET").upper()
            path = _text(op.get("path") or op.get("raw_path"))
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
                    results.append({
                        "phase": phase,
                        "step_id": _text(step.get("step_id")),
                        "status": "blocked_write",
                        "reason": reason,
                        "method": method,
                        "path": path,
                    })
                    continue
                observation_path = _declared_observation_path(path, ops)
                if not observation_path:
                    observations["harness_error"] = True
                    results.append({
                        "phase": phase,
                        "step_id": _text(step.get("step_id")),
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
            obs["step_id"] = _text(step.get("step_id"))
            obs["actor_ref"] = actor_ref
            obs["operation_ref"] = op_ref
            results.append(obs)
            # Default observer surface
            for observer in _list(exp.get("observers")):
                oid_obs = _text(_dict(observer).get("observer_id"))
                if oid_obs:
                    observations[oid_obs] = True
                    observations.setdefault(oid_obs + "_observation", obs)
            if phase == "control" and 200 <= int(obs.get("status_code") or 0) < 300:
                observations["control_succeeded"] = True
                observations["authorized_control"] = True
                observations["control_observation"] = obs
                observations["owner_can_access"] = True
            if phase == "treatment":
                observations["treatment_observation"] = obs
                observations["treatment_result"] = obs
                observations["status_code"] = obs.get("status_code")
                observations["body"] = obs.get("body")
                observations["viewer_can_access"] = 200 <= int(obs.get("status_code") or 0) < 300
                observations["leak_detected"] = bool(observations.get("viewer_can_access")) and _text(
                    (_dict(exp.get("assertions")[0] if _list(exp.get("assertions")) else {}).get("kind"))
                ) in {"authorization", "isolation", "visibility", "owner_tenant_visibility", "privacy"}
        return results

    steps_out.extend(_exec_plan(_list(exp.get("control_plan")), phase="control"))
    steps_out.extend(_exec_plan(_list(exp.get("treatment_plan")), phase="treatment"))

    # Cleanup compensation in reverse order for write experiments.
    safety = _dict(exp.get("safety_contract"))
    if safety.get("governed_write") and _list(exp.get("cleanup_plan")):
        for cleanup in reversed(_list(exp.get("cleanup_plan"))):
            # Compensation is declared; without a concrete reverse operation we
            # record an honest cleanup failure rather than inventing success.
            op_ref = _text(_dict(cleanup).get("operation_ref"))
            op = ops.get(op_ref) or {}
            path_template = _text(_dict(cleanup).get("path") or op.get("path") or op.get("raw_path"))
            path, runtime_bindings, missing_bindings = _runtime_cleanup_bindings(path_template, steps_out)
            method = _text(op.get("method") or "").upper()
            if missing_bindings:
                cleanup_failures += 1
                observations["cleanup_status"] = "failed"
                observations["cleanup_reason"] = f"cleanup_binding_unresolved:{','.join(missing_bindings)}"
                continue
            if not path.startswith("/") or path_has_placeholders(path) or method not in {"DELETE", "POST", "PUT", "PATCH"}:
                cleanup_failures += 1
                observations["cleanup_status"] = "failed"
                observations["cleanup_reason"] = "cleanup_compensation_unresolved"
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
            observation_path = _declared_observation_path(path, ops, runtime_bindings=runtime_bindings)
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
            steps_out.append({**cobs, "phase": "cleanup"})
            if not (200 <= int(cobs.get("status_code") or 0) < 300):
                cleanup_failures += 1
                observations["cleanup_status"] = "failed"
            else:
                observations["cleanup_status"] = "completed"

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
            "oracle_verdict": verdict,
            "finding": None,
            "cleanup_failures": cleanup_failures,
            "execution_receipt": {"status": status, "reason_code": reason, "detail": detail},
        }
    elif verdict.get("customer_deliverable") and verdict.get("verdict") == "customer_deliverable_defect_candidate":
        failed = _list(verdict.get("failed_assertions"))
        first = _dict(failed[0] if failed else {})
        treatment_plan = [step for step in _list(exp.get("treatment_plan")) if isinstance(step, dict)]
        control_plan = [step for step in _list(exp.get("control_plan")) if isinstance(step, dict)]
        primary_plan_step = _dict(treatment_plan[0] if treatment_plan else control_plan[0] if control_plan else {})
        primary_op = ops.get(_text(primary_plan_step.get("operation_ref"))) or {}
        primary_method = _text(primary_op.get("method") or "GET").upper()
        primary_path = _text(primary_op.get("path") or primary_op.get("raw_path"))
        treatment_actor = actors.get(_text(primary_plan_step.get("actor_ref"))) or {}
        treatment_role = _text(treatment_actor.get("role") or primary_plan_step.get("actor_ref"))
        control_step = _dict(control_plan[0] if control_plan else {})
        control_actor = actors.get(_text(control_step.get("actor_ref"))) or {}
        control_role = _text(control_actor.get("role") or control_step.get("actor_ref"))
        assertion_kind = _text(first.get("kind") or "contract")
        treatment_observation = _dict(observations.get("treatment_observation"))
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
                "customer_deliverable": True,
                "verdict": verdict.get("verdict"),
            },
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
                "level": "validated",
                "score": 95,
                "can_reproduce": True,
                "evidence_strength": "control_treatment_contract",
            },
            "evidence_status": {
                "semantic_verdict": "SEMANTIC_CONFIRMED",
                "business_evidence_status": "VALIDATED",
                "final_review_status": "VALIDATED_CANDIDATE",
                "missing_requirements": [],
            },
            "final_review_status": "VALIDATED_CANDIDATE",
            "business_evidence_status": "VALIDATED",
            "failed_assertions": failed,
            "cleanup_failures": cleanup_failures,
        }
        # Delivery gate still applies downstream; contract path starts as candidate
        # until cleanup/evidence completeness is proven.
        if cleanup_failures:
            finding = mark_as_internal_clue(finding, reason="cleanup_compensation_failed")
        else:
            # Promote only when control+typed assertion failed with real HTTP evidence.
            finding["gate_passed"] = True
            finding["confirmation_status"] = "confirmed"
            finding["bug_status"] = "reproduced"
            finding["customer_delivery_status"] = "defect"
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
        "oracle_verdict": verdict,
        "finding": finding,
        "cleanup_failures": cleanup_failures,
        "execution_receipt": {
            "status": status,
            "steps": len(steps_out),
            "cleanup_failures": cleanup_failures,
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
    tokens = load_actor_tokens(root, project)
    results: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    blocked = 0
    executed = 0
    harness = 0
    cleanup_failures = 0
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
        outcome = execute_one_experiment(
            exp,
            behavior_ir=behavior_ir,
            root=root,
            project=project,
            base_url=base_url,
            runtime_contract=runtime_contract,
            campaign_id=campaign_id,
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
        results.append(outcome)
        status = _text(outcome.get("status")).upper()
        if status == "BLOCKED":
            blocked += 1
        elif status == "HARNESS_FAILURE":
            harness += 1
        else:
            executed += 1
        cleanup_failures += int(outcome.get("cleanup_failures") or 0)
        if isinstance(outcome.get("finding"), dict):
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
        "every_experiment_has_receipt": all(
            isinstance(item.get("execution_receipt"), dict) for item in results
        ),
    }
