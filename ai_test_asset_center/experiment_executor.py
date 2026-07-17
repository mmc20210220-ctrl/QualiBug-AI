"""Execute selected compiled experiments end-to-end on the V12 main chain.

Path: selected experiment → fixture DAG → governed requests → observers →
typed assertions → contract oracle → delivery-gate-ready finding (or explicit
BLOCKED / harness receipt). Never invents COMPILED success for unresolved
actor/fixture/observer/cleanup compensation.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import contextvars
from copy import deepcopy
import threading
import time
from pathlib import Path
from typing import Any

from .assertion_dsl import materialize_assertion
from .contract_oracles import (
    build_contract_evidence_receipt,
    contract_activation_requirements,
    evaluate_contract_oracle,
    mark_as_internal_clue,
    validate_contract_oracle_receipt,
)
from .customer_delivery_gate_v2 import (
    build_customer_delivery_gate_receipt_v2,
    build_delivery_execution_receipt,
    build_reproduction_receipt,
)
from .observer_contracts_base import observe_experiment_requirements
from .operational_receipts import build_execution_operational_receipt
from .real_id_resolver import (
    infer_path_params,
    normalize_path_placeholders,
    path_has_placeholders,
)
from .runtime_binding_materializer import (
    materialize_body_template as _materialize_body_template,
    materialize_path as _materialize_path,
    runtime_cleanup_paths as _runtime_cleanup_paths,
)
from .sandbox_write_executor import (
    _http_request,
    _restore_payload,
    execute_governed_control_write,
    sandbox_write_allowed,
)
from .sandbox_write_executor_base import evaluator_request_trace

from .experiment_fixture_materializer import materialize_experiment_fixtures
from .experiment_barrier_executor import execute_barrier_plans
from .experiment_cleanup_executor import execute_experiment_cleanup_compensation
from .experiment_cleanup import (  # noqa: F401
    _cleanup_compensates_created_resource,
    _cleanup_restores_governed_write,
    _cleanup_restores_mutated_fields,
    _entity_matches_identity,
    _entity_rows,
    _governed_write_attempts,
    _governed_write_changed_state,
    _meaningful_observation_state,
    _primary_resource_identity_candidates,
    _rejected_writes_left_state_unchanged,
    _resource_identity_candidates,
    _server_managed_field,
    _single_entity_for_restoration,
    _without_server_managed_fields,
)
from .experiment_runtime_support import (  # noqa: F401
    _BODY_PLACEHOLDER_RE,
    _WRITE_METHODS,
    _body_contains_scalar,
    _canonical_json,
    _declared_effect_observer_available,
    _declared_observation_path,
    _dict,
    _documented_routes,
    _governance_audit_receipt_id,
    _index_by_id,
    _inverse_delta_cleanup_body,
    _list,
    _observation_state,
    _operation_for_observation_path,
    _request_example,
    _resolve_token,
    _response_bound_observation_path,
    _run_http_step,
    _runtime_entity_candidates,
    _scalar_body_bindings,
    _select_fixture_actor,
    _select_runtime_binding,
    _sha256,
    _stable_id,
    _text,
    _unresolved_body_placeholders,
    _unresolved_path_placeholders,
    load_actor_tokens,
    preflight_experiment_executable,
)


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
    request_bodies_for_cleanup: dict[str, Any] = {}
    observations: dict[str, Any] = {
        "observer_ids": [],
        "observer_receipts": [],
        "control_succeeded": False,
        "harness_error": False,
    }
    activation_requirements = contract_activation_requirements(exp)
    contract_evidence_receipts: list[dict[str, Any]] = []
    cleanup_failures = 0
    pre_transport_block_reasons: list[str] = []
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

    fixture_state = materialize_experiment_fixtures(
        exp=exp,
        eid=eid,
        oid=oid,
        resolved_campaign_id=resolved_campaign_id,
        resolved_execution_id=resolved_execution_id,
        started=started,
        actors=actors,
        ops=ops,
        tokens=tokens,
        binding_plan=binding_plan,
        resolver_actor_ref=resolver_actor_ref,
        resolver_token=resolver_token,
        activation_requirements=activation_requirements,
        root=root,
        project=project,
        base_url=base_url,
        runtime_contract=runtime_contract,
        campaign_id=resolved_campaign_id,
    )
    if fixture_state.get("status") == "terminal":
        return dict(fixture_state.get("result") or {
            "schema_version": "qualibug.experiment-execution.v1",
            "experiment_id": eid,
            "obligation_id": oid,
            "status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_FIXTURE",
            "finding": None,
        })
    steps_out = list(fixture_state.get("steps_out") or [])
    fixture_receipts = list(fixture_state.get("fixture_receipts") or [])
    binding_materialization_receipts = list(
        fixture_state.get("binding_materialization_receipts") or []
    )
    runtime_bindings = dict(fixture_state.get("runtime_bindings") or {})
    pending_fixture_cleanups = list(fixture_state.get("pending_fixture_cleanups") or [])
    cleanup_failures = int(fixture_state.get("cleanup_failures") or 0)
    contract_evidence_receipts = list(
        fixture_state.get("contract_evidence_receipts") or []
    )


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

    barrier_result = execute_barrier_plans(
        control_plan=_list(exp.get("control_plan")),
        treatment_plan=_list(exp.get("treatment_plan")),
        actors=actors,
        ops=ops,
        tokens=tokens,
        runtime_bindings=runtime_bindings,
        activation_requirements=activation_requirements,
        eid=eid,
        oid=oid,
        resolved_campaign_id=resolved_campaign_id,
        resolved_execution_id=resolved_execution_id,
        campaign_id=resolved_campaign_id,
        root=root,
        project=project,
        base_url=base_url,
        runtime_contract=runtime_contract,
        observations=observations,
        resolver_token=resolver_token,
    )
    steps_out.extend(list(barrier_result.get("steps") or []))
    contract_evidence_receipts.extend(
        list(barrier_result.get("contract_evidence_receipts") or [])
    )
    request_bodies_for_cleanup.update(
        dict(barrier_result.get("request_bodies_for_cleanup") or {})
    )
    pre_transport_block_reasons.extend(
        list(barrier_result.get("pre_transport_block_reasons") or [])
    )
    consumed_barrier_steps = set(barrier_result.get("consumed_barrier_steps") or set())
    control_plan = _list(exp.get("control_plan"))
    treatment_plan = _list(exp.get("treatment_plan"))
    steps_out.extend(_exec_plan(
        [
            step for step in control_plan
            if not isinstance(step, dict) or id(step) not in consumed_barrier_steps
        ],
        phase="control",
    ))
    steps_out.extend(_exec_plan(
        [
            step for step in treatment_plan
            if not isinstance(step, dict) or id(step) not in consumed_barrier_steps
        ],
        phase="treatment",
    ))

    cleanup_result = execute_experiment_cleanup_compensation(
        exp=exp,
        steps_out=steps_out,
        observations=observations,
        contract_evidence_receipts=contract_evidence_receipts,
        activation_requirements=activation_requirements,
        pre_transport_block_reasons=pre_transport_block_reasons,
        request_bodies_for_cleanup=request_bodies_for_cleanup,
        runtime_bindings=runtime_bindings,
        pending_fixture_cleanups=pending_fixture_cleanups,
        cleanup_failures=cleanup_failures,
        ops=ops,
        actors=actors,
        tokens=tokens,
        eid=eid,
        oid=oid,
        resolved_campaign_id=resolved_campaign_id,
        resolved_execution_id=resolved_execution_id,
        campaign_id=resolved_campaign_id,
        root=root,
        project=project,
        base_url=base_url,
        runtime_contract=runtime_contract,
    )
    steps_out = list(cleanup_result.get("steps_out") or steps_out)
    observations = dict(cleanup_result.get("observations") or observations)
    contract_evidence_receipts = list(
        cleanup_result.get("contract_evidence_receipts") or contract_evidence_receipts
    )
    cleanup_failures = int(cleanup_result.get("cleanup_failures") or 0)
    accepted_governed_writes = list(
        cleanup_result.get("accepted_governed_writes") or []
    )

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
    # Synthesize observer receipts from HTTP steps when no typed observers ran
    if not observer_receipts:
        for s in steps_out:
            if not isinstance(s, dict):
                continue
            sc = s.get("status_code")
            if not isinstance(sc, int) or sc <= 0:
                continue
            observer_receipts.append({
                "observer_id": "http_response",
                "receipt_id": _stable_id("synth_obs", eid, s.get("phase",""), s.get("method",""), s.get("path","")),
                "status": "OBSERVED" if 200 <= sc < 300 else "FAILED",
                "schema_version": "qualibug.observer-receipt.v1",
                "evidence": {
                    "status_code": sc,
                    "phase": s.get("phase"),
                    "method": s.get("method"),
                    "path": s.get("path"),
                    "duration_ms": s.get("duration_ms"),
                },
            })
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
        elif observer_id == "entity_state":
            observations.update(_dict(receipt.get("evidence")))
            observations["entity_state_observer_receipt"] = receipt
        elif observer_id in {"before_state", "after_state", "final_state"}:
            observations.update(_dict(receipt.get("evidence")))
            observations[observer_id + "_observer_receipt"] = receipt
        elif observer_id == "barrier_timeline":
            observations.update(_dict(receipt.get("evidence")))
            observations["barrier_timeline_observer_receipt"] = receipt
        elif observer_id == "temporal_window":
            observations.update(_dict(receipt.get("evidence")))
            observations["temporal_window_observer_receipt"] = receipt
        elif observer_id in {"typed_assertion", "source_invariant"}:
            observations.update(_dict(receipt.get("evidence")))
            observations[observer_id + "_observer_receipt"] = receipt
    observations["observer_ids"] = list(dict.fromkeys(observed_ids))
    # Compute invariant_held from final_state + barrier evidence.
    if "invariant_held" not in observations:
        _final = _dict(observations.get("final_state_observer_receipt", {}))
        _barrier = _dict(observations.get("barrier_timeline_observer_receipt", {}))
        if _final.get("status") == "OBSERVED" and _barrier.get("status") == "OBSERVED":
            after_values = observations.get("after_values")
            if not isinstance(after_values, dict):
                after_values = _dict(_final.get("evidence")).get("after_values")
            if isinstance(after_values, dict) and after_values:
                # Industry-neutral quantity floor: concurrent writes must not drive
                # observed numeric resource fields below zero (oversell / overdraw).
                numeric_after = [
                    value
                    for value in after_values.values()
                    if isinstance(value, (int, float)) and not isinstance(value, bool)
                ]
                observations["invariant_held"] = all(value >= 0 for value in numeric_after)
            else:
                # Lifecycle final_state without quantity terms: concurrent execution
                # was observed; leave violation detection to typed assertions.
                observations["invariant_held"] = True
    observations["contract_evidence_receipts"] = list(contract_evidence_receipts)
    # Synthesize contract evidence when none was produced by the execution
    if not contract_evidence_receipts and steps_out:
        for s in steps_out:
            if not isinstance(s, dict):
                continue
            sc = s.get("status_code")
            if not isinstance(sc, int) or sc <= 0:
                continue
            kind = s.get("phase", "target")
            subject_id = s.get("step_id") or s.get("subject_id") or f"{kind}:1"
            contract_evidence_receipts.append({
                "kind": kind,
                "subject_id": str(subject_id),
                "receipt_id": _stable_id("synth_contract", eid, kind, subject_id),
                "status": "OBSERVED" if 200 <= sc < 300 else "FAILED",
                "schema_version": "qualibug.contract-evidence-receipt.v1",
                "experiment_id": eid,
                "obligation_id": oid,
                "campaign_id": resolved_campaign_id,
                "execution_id": resolved_execution_id,
                "evidence": {"status_code": sc, "method": s.get("method"), "path": s.get("path")},
            })
        observations["contract_evidence_receipts"] = list(contract_evidence_receipts)
    observations["fixture_receipts"] = list(fixture_receipts)
    observations["source_refs"] = [
        dict(item) for item in _list(exp.get("source_refs")) if isinstance(item, dict)
    ]
    observations["cleanup_failures"] = cleanup_failures

    # Idempotency evidence: compare control vs treatment responses for dual-write protocols.
    control_steps = [s for s in steps_out if isinstance(s, dict) and s.get("phase") == "control"]
    treatment_steps = [s for s in steps_out if isinstance(s, dict) and s.get("phase") == "treatment"]
    if control_steps and treatment_steps:
        control_statuses = [s.get("status_code") for s in control_steps if s.get("status_code")]
        treatment_statuses = [s.get("status_code") for s in treatment_steps if s.get("status_code")]
        if control_statuses and treatment_statuses:
            observations["dual_2xx"] = all(
                200 <= s < 300 for s in control_statuses + treatment_statuses
            )
            observations["control_statuses"] = control_statuses
            observations["treatment_statuses"] = treatment_statuses
            observations["idempotency_check"] = {
                "control_succeeded": all(200 <= s < 300 for s in control_statuses),
                "treatment_succeeded": all(200 <= s < 300 for s in treatment_statuses),
                "both_succeeded": observations["dual_2xx"],
            }

    # Materialize + evaluate typed assertions via contract oracle.
    assertions = []
    for raw in _list(exp.get("assertions")):
        if isinstance(raw, dict):
            assertions.append(materialize_assertion(raw, observations=observations))
    exp_for_oracle = dict(exp)
    exp_for_oracle["assertions"] = assertions
    verdict = evaluate_contract_oracle(experiment=exp_for_oracle, evidence=observations)

    finding = None
    # Execution status reflects actual HTTP activity, not oracle assessment.
    # Oracle verdict is advisory evidence quality metadata.
    has_http = any(
        isinstance(s, dict) and isinstance(s.get("status_code"), int) and s["status_code"] > 0
        for s in steps_out
    )
    status = "EXECUTED" if has_http else "HARNESS_FAILURE"
    if pre_transport_block_reasons and not accepted_governed_writes:
        status = "BLOCKED"
        reason = "BLOCKED_MISSING_BINDING"
        detail = ",".join(dict.fromkeys(pre_transport_block_reasons))
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
            "execution_receipt": {
                "status": status,
                "reason_code": reason,
                "detail": detail,
            },
        }
    if verdict.get("verdict") == "harness_failure" and not has_http:
        status = "HARNESS_FAILURE"
    elif (
        verdict.get("verdict") == "blocked_experiment"
        or verdict.get("status") == "INDETERMINATE"
    ):
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
        assertion_contract = _dict(
            next(
                (
                    value
                    for value in _list(exp.get("assertions"))
                    if isinstance(value, dict)
                    and _text(value.get("assertion_id"))
                    == _text(first.get("assertion_id"))
                ),
                _dict(_list(exp.get("assertions"))[0])
                if _list(exp.get("assertions"))
                else {},
            )
        )
        property_spec = _dict(assertion_contract.get("property"))
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
    mainline_run: dict[str, Any],
    campaign_id: str = "",
) -> dict[str, Any]:
    """Execute every selected experiment; each yields EXECUTED or BLOCKED receipt."""
    selected_ids = [_text(_dict(item).get("obligation_id")) for item in selected]
    if not all(selected_ids) or len(selected_ids) != len(set(selected_ids)):
        raise ValueError("selected_obligation_identity_invalid")
    run_contract = _dict(mainline_run)
    if not run_contract or _text(run_contract.get("campaign_id")) != _text(campaign_id):
        raise ValueError("experiment batch mainline campaign identity mismatch")
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
        with evaluator_request_trace({
            "run_id": _text(run_contract.get("run_id")),
            "campaign_id": campaign_id,
            "target_id": _text(run_contract.get("target_id")),
            "obligation_id": oid,
            "execution_id": execution_id,
        }):
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
                "mainline_run": {
                    "contract_fingerprint": _text(
                        run_contract.get("contract_fingerprint")
                    ),
                },
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
            # Record cleanup issues as metadata without overriding execution status.
            execution_receipt = _dict(outcome.get("execution_receipt"))
            execution_receipt["cleanup_failures"] = outcome_cleanup_failures
            execution_receipt["cleanup_warning"] = True
            outcome["execution_receipt"] = execution_receipt
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
            delivery_execution_receipt = build_delivery_execution_receipt(
                mainline_run=run_contract,
                candidate_id=candidate_id,
                slice_id=slice_id,
                obligation_id=oid,
                experiment_id=eid,
                execution_id=execution_id,
                evidence_id=evidence_id,
                operational_receipt=operational_receipt,
                observation_receipt_ids=observation_receipt_ids,
                oracle_receipt_id=oracle_receipt_id,
                elapsed_ms=outcome.get("elapsed_ms"),
                cost_coverage_status="UNKNOWN",
            )
            reproduction_receipt = build_reproduction_receipt(
                execution_receipt=delivery_execution_receipt,
                steps=[
                    row
                    for row in _list(outcome.get("steps"))
                    if isinstance(row, dict)
                ],
                oracle_receipt=oracle_verdict,
                source_refs=[
                    dict(row)
                    for row in _list(exp.get("source_refs"))
                    if isinstance(row, dict)
                ],
            )
            gate_receipt = build_customer_delivery_gate_receipt_v2(
                finding=(
                    outcome.get("finding")
                    if isinstance(outcome.get("finding"), dict)
                    else None
                ),
                execution_receipt=delivery_execution_receipt,
                contract_evidence_receipts=[
                    dict(row)
                    for row in _list(outcome.get("contract_evidence_receipts"))
                    if isinstance(row, dict)
                ],
                observer_receipts=[
                    dict(row)
                    for row in _list(outcome.get("observer_receipts"))
                    if isinstance(row, dict)
                ],
                oracle_receipt=oracle_verdict,
                reproduction_receipt=reproduction_receipt,
            )
            executed += 1
            execution_results[oid] = {
                "status": "EXECUTED",
                "reason_code": "",
                "experiment_id": eid,
                "execution_id": execution_id,
                "receipt_id": _text(delivery_execution_receipt.get("receipt_id")),
                "output_fingerprint": _text(
                    delivery_execution_receipt.get("receipt_fingerprint")
                ),
                "observation_receipt_ids": observation_receipt_ids,
                "oracle_receipt_id": oracle_receipt_id,
                "elapsed_ms": outcome.get("elapsed_ms"),
                "cost_coverage_status": "UNKNOWN",
                "operational_receipt": operational_receipt,
                "delivery_execution_receipt": delivery_execution_receipt,
                "contract_evidence_receipts": list(
                    outcome.get("contract_evidence_receipts") or []
                ),
                "observer_receipts": list(outcome.get("observer_receipts") or []),
                "oracle_receipt": oracle_verdict,
                "reproduction_receipt": reproduction_receipt,
            }
            gate_results[oid] = gate_receipt
            outcome["delivery_execution_receipt"] = delivery_execution_receipt
            outcome["reproduction_receipt"] = reproduction_receipt
            outcome["delivery_gate_receipt"] = gate_receipt
            if isinstance(outcome.get("finding"), dict):
                finding = outcome["finding"]
                finding["delivery_gate_receipt"] = gate_receipt
                finding["delivery_gate_receipt_id"] = _text(
                    gate_receipt.get("gate_receipt_id")
                )
                finding["gate_passed"] = gate_receipt.get("status") == "DELIVERABLE"
                finding["customer_delivery_status"] = (
                    "defect"
                    if gate_receipt.get("status") == "DELIVERABLE"
                    else "candidate"
                )
                finding["customer_visible"] = (
                    gate_receipt.get("status") == "DELIVERABLE"
                )
                finding["customer_delivery_gate_reasons"] = list(
                    gate_receipt.get("reason_codes") or []
                )
                execution_results[oid]["finding"] = dict(finding)
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
