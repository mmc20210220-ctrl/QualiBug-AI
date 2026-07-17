"""Observer synthesis, contract oracle, and finding packaging for experiments.

Extracted from ``experiment_executor.execute_one_experiment``. Runs after
fixture/barrier/plan/cleanup and always returns the terminal
``qualibug.experiment-execution.v1`` outcome. Never authors an independent
customer-delivery decision.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .assertion_dsl import materialize_assertion
from .contract_oracles import (
    build_contract_evidence_receipt,
    evaluate_contract_oracle,
    mark_as_internal_clue,
)
from .experiment_runtime_support import (
    _dict,
    _list,
    _stable_id,
    _text,
)
from .observer_contracts_base import observe_experiment_requirements
from .runtime_binding_materializer import materialize_path as _materialize_path


def finalize_experiment_execution(
    *,
    exp: dict[str, Any],
    steps_out: list[dict[str, Any]],
    observations: dict[str, Any],
    contract_evidence_receipts: list[dict[str, Any]],
    fixture_receipts: list[dict[str, Any]],
    binding_materialization_receipts: list[dict[str, Any]],
    pre_transport_block_reasons: list[str],
    accepted_governed_writes: list[dict[str, Any]],
    cleanup_failures: int,
    runtime_bindings: dict[str, Any],
    ops: dict[str, dict[str, Any]],
    actors: dict[str, dict[str, Any]],
    eid: str,
    oid: str,
    campaign_id: str,
    resolved_campaign_id: str,
    resolved_execution_id: str,
    started: float,
) -> dict[str, Any]:
    """Synthesize observers, evaluate contract oracle, and package the outcome."""
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
    if pre_transport_block_reasons and not has_http:
        # Pre-transport blocks (binding placeholders, unresolved paths) mean the
        # request never reached the target — this is a BLOCKED outcome, not a
        # harness failure in the evidence infrastructure.
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
        description = _text(
            first.get("error")
            or f"control={control_role or 'unspecified'} succeeded; treatment={treatment_role or 'unspecified'} violated the typed assertion"
        )
        actual_payload = first.get("actual") if isinstance(first.get("actual"), dict) else {}
        after_values = actual_payload.get("after_values")
        before_values = actual_payload.get("before_values")
        if isinstance(after_values, dict) and after_values:
            description = (
                f"{description}; observed entity numerics before={before_values!s} "
                f"after={after_values!s}"
            )
        finding = {
            "severity": "P1",
            "title": f"[ContractOracle] {assertion_kind}: {treatment_role or 'actor'} {primary_method} {primary_path}",
            "category": assertion_kind,
            "risk_family": (
                _text(exp.get("risk_family"))
                or (
                    "isolation"
                    if _text(first.get("assertion_id") or assertion_contract.get("assertion_id"))
                    == "assert_isolation"
                    or _text(property_spec.get("template") or assertion_contract.get("template"))
                    == "owner_viewer_isolation"
                    else ""
                )
                or (
                    "authorization"
                    if assertion_kind == "owner_tenant_visibility"
                    else assertion_kind
                )
            ),
            "source": "experiment_contract_oracle",
            "description": description,
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

