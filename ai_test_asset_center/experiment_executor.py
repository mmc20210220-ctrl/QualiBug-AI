"""Execute selected compiled experiments end-to-end on the V12 main chain.

Path: selected experiment → fixture DAG → governed requests → observers →
typed assertions → contract oracle → delivery-gate-ready finding (or explicit
BLOCKED / harness receipt). Never invents COMPILED success for unresolved
actor/fixture/observer/cleanup compensation.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .contract_oracles import (
    contract_activation_requirements,
    validate_contract_oracle_receipt,
)
from .customer_delivery_gate_v2 import (
    build_customer_delivery_gate_receipt_v2,
    build_delivery_execution_receipt,
    build_reproduction_receipt,
)
from .operational_receipts import build_execution_operational_receipt
from .sandbox_write_executor import (  # noqa: F401
    _http_request,
    execute_governed_control_write,
    sandbox_write_allowed,
)
from .sandbox_write_executor_base import evaluator_request_trace

from .experiment_fixture_materializer import materialize_experiment_fixtures
from .experiment_barrier_executor import execute_barrier_plans
from .experiment_plan_executor import execute_non_barrier_plans
from .experiment_outcome_finalizer import finalize_experiment_execution
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
    plan_result = execute_non_barrier_plans(
        control_plan=control_plan,
        treatment_plan=treatment_plan,
        consumed_barrier_steps=consumed_barrier_steps,
        actors=actors,
        ops=ops,
        tokens=tokens,
        runtime_bindings=runtime_bindings,
        activation_requirements=activation_requirements,
        observations=observations,
        eid=eid,
        oid=oid,
        resolved_campaign_id=resolved_campaign_id,
        resolved_execution_id=resolved_execution_id,
        campaign_id=resolved_campaign_id,
        root=root,
        project=project,
        base_url=base_url,
        runtime_contract=runtime_contract,
        cleanup_failures=cleanup_failures,
    )
    steps_out.extend(list(plan_result.get("steps") or []))
    contract_evidence_receipts.extend(
        list(plan_result.get("contract_evidence_receipts") or [])
    )
    request_bodies_for_cleanup.update(
        dict(plan_result.get("request_bodies_for_cleanup") or {})
    )
    pre_transport_block_reasons.extend(
        list(plan_result.get("pre_transport_block_reasons") or [])
    )
    cleanup_failures = int(plan_result.get("cleanup_failures") or cleanup_failures)

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

    return finalize_experiment_execution(
        exp=exp,
        steps_out=steps_out,
        observations=observations,
        contract_evidence_receipts=contract_evidence_receipts,
        fixture_receipts=fixture_receipts,
        binding_materialization_receipts=binding_materialization_receipts,
        pre_transport_block_reasons=pre_transport_block_reasons,
        accepted_governed_writes=accepted_governed_writes,
        cleanup_failures=cleanup_failures,
        runtime_bindings=runtime_bindings,
        ops=ops,
        actors=actors,
        eid=eid,
        oid=oid,
        campaign_id=resolved_campaign_id,
        resolved_campaign_id=resolved_campaign_id,
        resolved_execution_id=resolved_execution_id,
        started=started,
    )



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
