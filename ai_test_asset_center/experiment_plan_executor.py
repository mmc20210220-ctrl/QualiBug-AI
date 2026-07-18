"""Sequential control/treatment plan execution for experiments.

Extracted from ``experiment_executor.execute_one_experiment``. Executes
non-barrier plan steps after ``execute_barrier_plans``, preserving
control-body capture for treatment reuse and pre-transport binding blocks.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlencode

from .contract_oracles import build_contract_evidence_receipt
from .experiment_runtime_support import (
    _WRITE_METHODS,
    _declared_observation_path,
    _dict,
    _list,
    _observation_state,
    _resolve_token,
    _response_bound_observation_path,
    _run_http_step,
    _sha256,
    _text,
    _unresolved_body_placeholders,
    _unresolved_path_placeholders,
)
from .real_id_resolver import (
    infer_path_params,
    normalize_path_placeholders,
    path_has_placeholders,
)
from .runtime_binding_materializer import (
    materialize_body_template as _materialize_body_template,
    materialize_path as _materialize_path,
)
from .sandbox_write_executor import (
    _http_request,
    execute_governed_control_write,
    sandbox_write_allowed,
)
from .sandbox_write_executor_base import evaluator_request_trace


def execute_non_barrier_plans(
    *,
    control_plan: list[Any],
    treatment_plan: list[Any],
    consumed_barrier_steps: set[int],
    actors: dict[str, dict[str, Any]],
    ops: dict[str, dict[str, Any]],
    tokens: dict[str, str],
    runtime_bindings: dict[str, Any],
    activation_requirements: dict[str, Any],
    observations: dict[str, Any],
    eid: str,
    oid: str,
    resolved_campaign_id: str,
    resolved_execution_id: str,
    campaign_id: str,
    root: Path,
    project: str,
    base_url: str,
    runtime_contract: dict[str, Any],
    cleanup_failures: int = 0,
) -> dict[str, Any]:
    """Execute sequential control/treatment steps not consumed by barriers.

    Mutates ``observations`` in place. Returns steps and evidence bags for the
    caller to merge before cleanup compensation.
    """
    contract_evidence_receipts: list[dict[str, Any]] = []
    request_bodies_for_cleanup: dict[str, Any] = {}
    pre_transport_block_reasons: list[str] = []

    source_observed_control_bodies: dict[str, Any] = {}
    source_body_control_blocked = False

    def _exec_plan(plan: list[Any], *, phase: str) -> list[dict[str, Any]]:
        nonlocal cleanup_failures, source_body_control_blocked
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
            if phase == "treatment" and source_body_control_blocked:
                reason = "control_body_binding_blocked"
                pre_transport_block_reasons.append(reason)
                contract_evidence_receipts.append(build_contract_evidence_receipt(
                    kind=phase,
                    experiment_id=eid,
                    obligation_id=oid,
                    campaign_id=resolved_campaign_id,
                    execution_id=resolved_execution_id,
                    subject_id=subject_id,
                    status="BLOCKED",
                    evidence={
                        "write_reached_transport": False,
                        "reason_code": reason,
                    },
                ))
                results.append({
                    "phase": phase,
                    "step_id": subject_id,
                    "status": "blocked_write",
                    "reason": "BLOCKED_MISSING_BINDING",
                    "detail": reason,
                    "method": _text(step.get("method") or "POST").upper(),
                    "path": _text(step.get("path") or step.get("path_template")),
                    "status_code": 0,
                })
                continue
            actor = actors.get(actor_ref) or {}
            op = ops.get(op_ref) or {}
            # If op_ref doesn't match, try to find by method+path_template
            if not op and op_ref:
                path_template_candidate = _text(step.get("path") or step.get("path_template"))
                method_candidate = _text(step.get("method") or "POST").upper()
                for _oid, _op in ops.items():
                    if isinstance(_op, dict) and _text(_op.get("method","")).upper() == method_candidate:
                        _op_path = normalize_path_placeholders(_text(_op.get("path") or _op.get("raw_path")))
                        _step_path = normalize_path_placeholders(path_template_candidate)
                        if _op_path == _step_path:
                            op = _op
                            op_ref = _oid
                            break
            method = _text(op.get("method") or "GET").upper()
            path_template = _text(op.get("path") or op.get("raw_path"))
            path = _materialize_path(
                path_template,
                runtime_bindings,
            )
            unresolved_path_tokens = _unresolved_path_placeholders(path)
            query_spec = _dict(step.get("query"))
            if query_spec and not unresolved_path_tokens:
                materialized_query: dict[str, str] = {}
                unresolved_query_tokens: list[str] = []
                for key, raw_value in query_spec.items():
                    name = _text(key)
                    if not name:
                        continue
                    value = raw_value
                    if isinstance(raw_value, str):
                        token_match = re.fullmatch(
                            r"\s*\{([A-Za-z_][A-Za-z0-9_]*)\}\s*",
                            raw_value,
                        )
                        if token_match:
                            token = _text(token_match.group(1))
                            bound = runtime_bindings.get(token)
                            if bound in (None, "", [], {}):
                                unresolved_query_tokens.append(token)
                                continue
                            value = bound
                    if value in (None, "", [], {}):
                        unresolved_query_tokens.append(name)
                        continue
                    materialized_query[name] = str(value)
                if unresolved_query_tokens:
                    unresolved_path_tokens = list(dict.fromkeys(
                        [*unresolved_path_tokens, *unresolved_query_tokens]
                    ))
                elif materialized_query:
                    separator = "&" if "?" in path else "?"
                    path = f"{path}{separator}{urlencode(materialized_query)}"
            if unresolved_path_tokens:
                reason = (
                    "path_placeholder_unresolved:"
                    + ",".join(unresolved_path_tokens)
                )
                pre_transport_block_reasons.append(reason)
                if phase == "control":
                    source_body_control_blocked = True
                evidence: dict[str, Any] = {
                    "request_reached_transport": False,
                    "reason_code": "BLOCKED_MISSING_BINDING",
                    "unresolved_path_tokens": unresolved_path_tokens,
                }
                if method in _WRITE_METHODS:
                    evidence["write_reached_transport"] = False
                contract_evidence_receipts.append(build_contract_evidence_receipt(
                    kind=phase,
                    experiment_id=eid,
                    obligation_id=oid,
                    campaign_id=resolved_campaign_id,
                    execution_id=resolved_execution_id,
                    subject_id=subject_id,
                    status="BLOCKED",
                    evidence=evidence,
                ))
                results.append({
                    "phase": phase,
                    "step_id": subject_id,
                    "status": "blocked_write"
                    if method in _WRITE_METHODS
                    else "blocked_request",
                    "reason": "BLOCKED_MISSING_BINDING",
                    "detail": reason,
                    "method": method,
                    "path": path,
                    "path_template": path_template,
                    "status_code": 0,
                    "actor_ref": actor_ref,
                    "operation_ref": op_ref,
                })
                continue
            request_body = (
                step.get("body")
                if "body" in step
                else op.get("request_example")
                if method in _WRITE_METHODS and op.get("request_example")
                else None
            )
            request_body = _materialize_body_template(
                request_body,
                runtime_bindings,
            )
            unresolved_body_tokens = _unresolved_body_placeholders(
                request_body,
                runtime_bindings,
            )
            if method in _WRITE_METHODS and unresolved_body_tokens:
                reason = (
                    "body_placeholder_unresolved:"
                    + ",".join(unresolved_body_tokens)
                )
                pre_transport_block_reasons.append(reason)
                if phase == "control":
                    source_body_control_blocked = True
                contract_evidence_receipts.append(build_contract_evidence_receipt(
                    kind=phase,
                    experiment_id=eid,
                    obligation_id=oid,
                    campaign_id=resolved_campaign_id,
                    execution_id=resolved_execution_id,
                    subject_id=subject_id,
                    status="BLOCKED",
                    evidence={
                        "write_reached_transport": False,
                        "reason_code": "BLOCKED_MISSING_BINDING",
                        "unresolved_body_tokens": unresolved_body_tokens,
                    },
                ))
                results.append({
                    "phase": phase,
                    "step_id": subject_id,
                    "status": "blocked_write",
                    "reason": "BLOCKED_MISSING_BINDING",
                    "detail": reason,
                    "method": method,
                    "path": path,
                    "status_code": 0,
                })
                continue
            runtime_body_plan = deepcopy(_dict(step.get("runtime_body_plan")))
            if runtime_body_plan:
                identity_fields = infer_path_params(path_template)
                runtime_body_plan["identity_bindings"] = {
                    field: runtime_bindings[field]
                    for field in identity_fields
                    if field in runtime_bindings
                    and runtime_bindings[field] not in (None, "")
                }
                if phase == "treatment" and op_ref in source_observed_control_bodies:
                    request_body = deepcopy(source_observed_control_bodies[op_ref])
                    runtime_body_plan = {}
            mutation = _dict(step.get("mutation"))
            mutation_class = _text(
                mutation.get("class")
                or mutation.get("constraint")
                or mutation.get("operator")
                or step.get("protocol_step")
                or step.get("intent")
                or f"{phase}_request"
            )
            mutation_selector = _text(
                mutation.get("json_path")
                or mutation.get("field_selector")
                or mutation.get("field")
            )
            mutation_operator = _text(
                mutation.get("operator") or mutation.get("constraint")
            )
            request_body_fingerprint = _sha256(request_body)
            request_semantics_fingerprint = _sha256({
                "operation_ref": op_ref,
                "method": method,
                "path_template": path_template,
                "mutation_class": mutation_class,
                "mutation_selector": mutation_selector,
                "mutation_operator": mutation_operator,
                "request_body_fingerprint": request_body_fingerprint,
            })
            token = _resolve_token(actor, tokens)
            is_write = method in _WRITE_METHODS
            response_bound_observation: dict[str, Any] = {}
            runtime_body_blocked = False
            if is_write:
                allowed, reason = sandbox_write_allowed(
                    root=root,
                    project=project,
                    runtime_contract=runtime_contract,
                    actor_token=token,
                    actor_identity=_text(actor.get("role") or actor_ref),
                )
                if not allowed:
                    # Record the sandbox block but DO NOT skip the step.
                    # The HTTP request will still be sent — a real error
                    # response (4xx/5xx) is better than synthetic status_code=0
                    # because it generates observer evidence.
                    observations["sandbox_blocked"] = True
                    observations["sandbox_block_reason"] = _text(reason)
                observation_path = _declared_observation_path(
                    path_template,
                    ops,
                    runtime_bindings=runtime_bindings,
                    request_body=request_body,
                )
                if not observation_path:
                    # No declared effect observer exists for this endpoint.
                    # Fall back to the write path itself as a best-effort
                    # observation target. The before/after GETs may return
                    # 404/405 for POST-only endpoints, but the write itself
                    # still executes and produces observable evidence.
                    observation_path = path
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
                    body=request_body,
                    observation_path=observation_path,
                    runtime_body_plan=runtime_body_plan or None,
                )
                runtime_body_receipt = _dict(governed.get("runtime_body_receipt"))
                runtime_body_blocked = (
                    _text(runtime_body_receipt.get("status")).upper() == "BLOCKED"
                )
                if runtime_body_blocked:
                    reason_code = _text(
                        runtime_body_receipt.get("reason_code")
                        or governed.get("reason")
                    ) or "runtime_body_materialization_blocked"
                    pre_transport_block_reasons.append(reason_code)
                materialized_body = governed.get("materialized_request_body")
                if isinstance(materialized_body, dict) and materialized_body:
                    request_body = deepcopy(materialized_body)
                    request_body_fingerprint = _sha256(request_body)
                    request_semantics_fingerprint = _sha256({
                        "operation_ref": op_ref,
                        "method": method,
                        "path_template": path_template,
                        "mutation_class": mutation_class,
                        "mutation_selector": mutation_selector,
                        "mutation_operator": mutation_operator,
                        "request_body_fingerprint": request_body_fingerprint,
                    })
                    if phase == "control":
                        source_observed_control_bodies[op_ref] = deepcopy(request_body)
                request_bodies_for_cleanup[subject_id] = request_body
                write_receipt = _dict(governed.get("write"))
                if 200 <= int(write_receipt.get("status") or 0) < 300:
                    response_bound_path = _response_bound_observation_path(
                        op,
                        ops,
                        write_receipt.get("body"),
                    )
                    if response_bound_path:
                        response_bound_raw = _http_request(
                            _text(response_bound_path.get("method") or "GET"),
                            base_url.rstrip("/") + _text(response_bound_path.get("path")),
                            token=token,
                        )
                        response_bound_observation = {
                            "method": _text(response_bound_path.get("method") or "GET"),
                            "path": _text(response_bound_path.get("path")),
                            "path_template": _text(response_bound_path.get("path_template")),
                            "status_code": int(response_bound_raw.get("status") or 0),
                            "status": int(response_bound_raw.get("status") or 0),
                            "body": response_bound_raw.get("body"),
                            "headers": response_bound_raw.get("headers") or {},
                            "duration_ms": response_bound_raw.get("duration_ms"),
                            "phase": f"{phase}_response_bound_effect_observation",
                            "step_id": f"{subject_id}:response_bound_effect",
                            "actor_ref": actor_ref,
                            "operation_ref": _text(response_bound_path.get("operation_ref")),
                            "source_operation_ref": op_ref,
                        }
                        governed["response_bound_after"] = {
                            "method": _text(response_bound_path.get("method") or "GET"),
                            "url": base_url.rstrip("/") + _text(response_bound_path.get("path")),
                            "status": int(response_bound_raw.get("status") or 0),
                            "body": response_bound_raw.get("body"),
                            "headers": response_bound_raw.get("headers") or {},
                            "duration_ms": response_bound_raw.get("duration_ms"),
                        }
                        governed["response_bound_after_ref"] = (
                            "response_bound_after:"
                            f"{_text(response_bound_path.get('path'))}:"
                            f"{int(response_bound_raw.get('status') or 0)}"
                        )
                        governed["response_bound_observer_operation_ref"] = _text(
                            response_bound_path.get("operation_ref")
                        )
                obs = {
                    "method": method,
                    "path": path,
                    "status_code": int(write_receipt.get("status") or 0),
                    "body": write_receipt.get("body"),
                    "headers": write_receipt.get("headers") or {},
                    "duration_ms": write_receipt.get("duration_ms"),
                    "error": write_receipt.get("error") or governed.get("reason") or "",
                    "governance_receipt": governed,
                    "observation_path": observation_path,
                }
                if response_bound_observation:
                    obs["response_bound_observation"] = response_bound_observation
            else:
                obs = _run_http_step(base_url=base_url, method=method, path=path, token=token)
            obs["phase"] = phase
            obs["step_id"] = subject_id
            obs["actor_ref"] = actor_ref
            obs["operation_ref"] = op_ref
            obs["path_template"] = path_template
            obs["request_body_fingerprint"] = request_body_fingerprint
            obs["request_semantics_fingerprint"] = (
                request_semantics_fingerprint
            )
            obs["mutation_class"] = mutation_class
            obs["mutation_selector"] = mutation_selector
            obs["mutation_operator"] = mutation_operator
            if runtime_body_blocked:
                obs["status"] = "blocked_write"
                obs["reason"] = _text(
                    _dict(obs.get("governance_receipt")).get("reason")
                ) or "runtime_body_materialization_blocked"
            observed_status = int(obs.get("status_code") or 0)
            if _text(step.get("protocol_step")) == "temporal_write":
                temporal_elapsed = int(obs.get("duration_ms") or 0)
                after_state = _observation_state(
                    _dict(obs.get("governance_receipt")).get("after")
                )
                observations.setdefault("temporal_timeline", []).extend([
                    {
                        "event": "trigger",
                        "phase": phase,
                        "step_id": subject_id,
                        "at_ms": 0,
                        "status_code": observed_status,
                    },
                    {
                        "event": "final_observed",
                        "phase": phase,
                        "step_id": subject_id,
                        "at_ms": temporal_elapsed,
                        "status_code": int(after_state.get("status") or 0),
                    },
                ])
            contract_status = (
                "BLOCKED"
                if runtime_body_blocked
                else "OBSERVED"
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
                    "path_template": path_template,
                    "request_body_fingerprint": request_body_fingerprint,
                    "request_semantics_fingerprint": (
                        request_semantics_fingerprint
                    ),
                    "mutation_class": mutation_class,
                    "mutation_selector": mutation_selector,
                    "mutation_operator": mutation_operator,
                    "response_observed": observed_status > 0,
                    "control_succeeded": (
                        200 <= observed_status < 300
                        if phase == "control"
                        else None
                    ),
                },
            ))
            results.append(obs)
            if response_bound_observation:
                results.append(response_bound_observation)
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

    steps: list[dict[str, Any]] = []
    steps.extend(_exec_plan(
        [
            step for step in control_plan
            if not isinstance(step, dict) or id(step) not in consumed_barrier_steps
        ],
        phase="control",
    ))
    steps.extend(_exec_plan(
        [
            step for step in treatment_plan
            if not isinstance(step, dict) or id(step) not in consumed_barrier_steps
        ],
        phase="treatment",
    ))
    return {
        "steps": steps,
        "contract_evidence_receipts": contract_evidence_receipts,
        "request_bodies_for_cleanup": request_bodies_for_cleanup,
        "pre_transport_block_reasons": pre_transport_block_reasons,
        "cleanup_failures": cleanup_failures,
    }
