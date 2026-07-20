"""Discovery execution helpers: terminals, authority findings, campaign finalize.

Extracted from ``discovery_runtime_execution``. Symbols are re-exported from
``discovery_runtime_execution`` and ``discovery_runtime`` for compatibility.
"""
from __future__ import annotations

import re
from typing import Any

from .discovery_mainline import DiscoveryPlanningBundle
from .discovery_mainline_contract import MainlineContractError, MainlineRunContract
from .discovery_runtime_planning import (
    _campaign_object,
    _campaign_store,
)
from .operational_receipts import (
    aggregate_execution_operational_receipts,
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _governed_write_block_reason(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    normalized = text
    if normalized.lower().startswith("runtimeerror:"):
        normalized = normalized.split(":", 1)[1].strip()
    for prefix in (
        "write_cleanup_operation_not_declared",
        "identity_mutation_requires_disposable_fixture",
        "protected_runtime_identity_mutation_blocked",
        "governed_write_blocked:",
        "multi_write_executor_missing_per_write_governance_hook",
        "invalid_governed_write_event:",
        "DELETE_SAFETY_GUARD",
    ):
        if normalized == prefix or normalized.startswith(prefix):
            return normalized.split("\n", 1)[0][:240]
    return ""


def _legacy_execution_terminal(
    *,
    cleanup_failed: bool,
    observation_receipt_ids: list[str],
    trace_errors: list[Any],
    skipped_reasons: list[str],
    trace_present: bool,
) -> tuple[str, str]:
    """Classify a legacy attempt without hiding policy blocks as failures.

    Cleanup compensation failure after real target observations is not a
    harness crash: the attempt executed. Preserve the cleanup reason for the
    delivery gate instead of mislabeling the terminal as ``HARNESS_FAILED``.
    """
    if observation_receipt_ids:
        if cleanup_failed:
            return "EXECUTED", "CLEANUP_COMPENSATION_FAILED"
        return "EXECUTED", ""
    if cleanup_failed:
        return "HARNESS_FAILED", "CLEANUP_COMPENSATION_FAILED"
    if trace_errors:
        for raw_error in trace_errors:
            block = _governed_write_block_reason(raw_error)
            if not block and str(raw_error or "").startswith("failed_after_retries:"):
                block = _governed_write_block_reason(
                    str(raw_error).split(":", 1)[1]
                )
            if block:
                reason = re.sub(r"[^A-Za-z0-9]+", "_", block).strip("_").upper()
                return (
                    "BLOCKED",
                    reason if reason.startswith("BLOCKED_") else f"BLOCKED_{reason}",
                )
    if skipped_reasons:
        for raw_reason in skipped_reasons:
            reason = re.sub(r"[^A-Za-z0-9]+", "_", _text(raw_reason)).strip("_").upper()
            if reason:
                return (
                    "BLOCKED",
                    reason if reason.startswith("BLOCKED_") else f"BLOCKED_{reason}",
                )
        return "BLOCKED", "LEGACY_EXECUTION_BLOCKED"
    if trace_errors:
        return "HARNESS_FAILED", "LEGACY_EXECUTION_ERROR"
    if trace_present:
        return "BLOCKED", "LEGACY_EXECUTION_BLOCKED"
    return "BLOCKED", "LEGACY_EXECUTION_RECEIPT_MISSING"


def _operational_summary_from_attempt_ledger(
    ledger: dict[str, Any],
) -> dict[str, Any]:
    attempts = [
        row
        for row in _list(_dict(ledger).get("attempts"))
        if isinstance(row, dict)
    ]
    execution_attempts = [
        row
        for row in attempts
        if any(
            _text(stage.get("stage")) == "execution"
            for stage in _list(row.get("stages"))
            if isinstance(stage, dict)
        )
    ]
    receipts = [
        dict(row["operational_receipt"])
        for row in execution_attempts
        if isinstance(row.get("operational_receipt"), dict)
    ]
    summary = aggregate_execution_operational_receipts(receipts)
    missing = [
        _text(row.get("obligation_id"))
        for row in execution_attempts
        if not isinstance(row.get("operational_receipt"), dict)
    ]
    return {
        **summary,
        "complete": not missing and len(receipts) == len(execution_attempts),
        "missing_obligation_ids": missing,
    }


def _legacy_experiment_execution_batch(
    *,
    selected_rows: list[dict[str, Any]],
    execution_results: dict[str, dict[str, Any]],
    normalized_findings: list[dict[str, Any]],
    campaign_id: str,
) -> dict[str, Any]:
    """Project legacy adapter attempts into experiment_execution.results."""

    finding_by_obligation = {
        _text(item.get("obligation_id")): item
        for item in normalized_findings
        if _text(item.get("obligation_id"))
    }
    results: list[dict[str, Any]] = []
    executed_count = 0
    blocked_count = 0
    harness_failure_count = 0
    for row in selected_rows:
        obligation_id = _text(row.get("obligation_id"))
        exec_row = _dict(execution_results.get(obligation_id))
        finding = finding_by_obligation.get(obligation_id)
        status = _text(exec_row.get("status")).upper() or "BLOCKED"
        if status == "EXECUTED":
            executed_count += 1
        elif status == "HARNESS_FAILED":
            harness_failure_count += 1
        elif status == "BLOCKED":
            blocked_count += 1
        operational_receipt = _dict(exec_row.get("operational_receipt"))
        execution_id = _text(exec_row.get("execution_id"))
        experiment_id = _text(row.get("experiment_id"))
        results.append({
            "schema_version": "qualibug.experiment-execution.v1",
            "candidate_id": _text(row.get("candidate_id")),
            "slice_id": _text(row.get("behavior_slice_id")),
            "obligation_id": obligation_id,
            "experiment_id": experiment_id,
            "execution_id": execution_id,
            "evidence_id": _text(finding.get("evidence_id")) if finding else "",
            "campaign_id": campaign_id,
            "status": status,
            "reason_code": _text(exec_row.get("reason_code")),
            "detail": "",
            "elapsed_ms": 0,
            "finding": finding if finding and status == "EXECUTED" else None,
            "execution_receipt": {
                **operational_receipt,
                "execution_id": execution_id,
                "status": status,
                "reason_code": _text(exec_row.get("reason_code")),
                "obligation_id": obligation_id,
                "experiment_id": experiment_id,
                "campaign_id": campaign_id,
            },
        })
    return {
        "selected_count": len(selected_rows),
        "scheduled_count": len(selected_rows),
        "executed_count": executed_count,
        "blocked_count": blocked_count,
        "harness_failure_count": harness_failure_count,
        "cleanup_failures": 0,
        "every_experiment_has_receipt": bool(selected_rows),
        "results": results,
    }


def _selected_rows(plan: DiscoveryPlanningBundle) -> list[dict[str, Any]]:
    experiments = _dict(plan.experiments.get("by_obligation"))
    intents = {
        _text(_dict(row).get("obligation_id")): dict(row)
        for row in _list(
            _dict(plan.experiments.get("agent_intent_plan")).get("intents")
        )
        if isinstance(row, dict) and _text(row.get("obligation_id"))
    }
    rows: list[dict[str, Any]] = []
    for obligation in _list(plan.obligations.get("obligations")):
        if not isinstance(obligation, dict):
            continue
        obligation_id = _text(obligation.get("obligation_id"))
        experiment = _dict(experiments.get(obligation_id))
        intent = _dict(intents.get(obligation_id))
        adapters = [
            _text(value)
            for value in _list(intent.get("execution_adapters"))
            if _text(value)
        ]
        rows.append({
            **obligation,
            "candidate_id": _text(obligation.get("candidate_id")) or obligation_id,
            "experiment_id": _text(experiment.get("experiment_id")),
            "adapter": (
                adapters[0]
                if len(adapters) == 1
                else "multi_surface"
                if adapters
                else "unavailable"
            ),
            "execution_adapters": adapters,
            "agent_intent_id": _text(intent.get("intent_id")),
            "planning_round": 1,
            "operation_refs": list(obligation.get("required_operations") or []),
            "actor_refs": list(obligation.get("required_actors") or []),
            "behavior_ir_refs": list(obligation.get("relation_refs") or []),
        })
    return rows


def _manual_terminal_receipts(
    *,
    selected_rows: list[dict[str, Any]],
    experiments_by_obligation: dict[str, dict[str, Any]],
    obligation_plan: dict[str, Any],
    runtime_contract: dict[str, Any],
    compile_results: dict[str, dict[str, Any]],
    execution_results: dict[str, dict[str, Any]],
) -> None:
    experiments = _dict(experiments_by_obligation)
    obligation_plan = _dict(obligation_plan)
    scheduled_ids = {
        _text(row.get("obligation_id"))
        for row in _list(obligation_plan.get("selected"))
        if isinstance(row, dict) and _text(row.get("obligation_id"))
    }
    pending_ids = {
        _text(row.get("obligation_id"))
        for row in _list(obligation_plan.get("pending_next_round"))
        if isinstance(row, dict) and _text(row.get("obligation_id"))
    }
    runtime_approved = (
        _text(runtime_contract.get("status")) == "approved"
        and bool(_text(runtime_contract.get("approved_base_url")))
    )
    for row in selected_rows:
        obligation_id = _text(row.get("obligation_id"))
        if obligation_id in compile_results:
            continue
        # Check variant obligation_ids and map them to the original
        _variant_result = None
        for _vid, _vresult in compile_results.items():
            if _vid.startswith(obligation_id + "__v_"):
                _variant_result = _vresult
                break
        if _variant_result is not None:
            compile_results[obligation_id] = dict(_variant_result)
            continue
        experiment = _dict(experiments.get(obligation_id))
        compile_receipt = _dict(experiment.get("compile_receipt"))
        compile_status = _text(compile_receipt.get("status")).upper()
        if compile_status == "BLOCKED":
            compile_results[obligation_id] = {
                "status": "BLOCKED",
                "reason_code": _text(compile_receipt.get("reason_code"))
                or "BLOCKED_COMPILE",
                "detail": _text(
                    compile_receipt.get("detail")
                    or compile_receipt.get("reason_detail")
                ),
                "experiment_id": _text(experiment.get("experiment_id")),
                "cost_coverage_status": "UNKNOWN",
            }
        elif obligation_id in pending_ids:
            compile_results[obligation_id] = {
                "status": "DEFERRED",
                "reason_code": "OBLIGATION_BUDGET_REACHED",
                "experiment_id": _text(experiment.get("experiment_id")),
                "cost_coverage_status": "UNKNOWN",
            }
        elif obligation_id in scheduled_ids and not runtime_approved:
            compile_results[obligation_id] = {
                "status": "COMPILED",
                "experiment_id": _text(experiment.get("experiment_id")),
                "cost_coverage_status": "UNKNOWN",
            }
            execution_results[obligation_id] = {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_RUNTIME_TARGET",
                "experiment_id": _text(experiment.get("experiment_id")),
                "cost_coverage_status": "UNKNOWN",
            }
        else:
            # Fallback: obligation compiled but not selected/blocked/deferred.
            # Treat as DEFERRED rather than failing the entire run.
            compile_results[obligation_id] = {
                "status": "DEFERRED",
                "reason_code": "OBLIGATION_NOT_IN_PLAN",
                "detail": _text(compile_receipt.get("detail") or ""),
                "experiment_id": _text(experiment.get("experiment_id")),
                "cost_coverage_status": "UNKNOWN",
            }


def _authority_findings(
    *,
    raw_findings: list[dict[str, Any]],
    gate_results: dict[str, dict[str, Any]],
    contract: MainlineRunContract,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    deliverable: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    shadow: list[dict[str, Any]] = []
    findings_by_id: dict[str, dict[str, Any]] = {}
    for item in raw_findings:
        finding_id = _text(item.get("finding_id") or item.get("id"))
        if not finding_id:
            raise MainlineContractError("experiment_finding_id_missing")
        row = {
            **item,
            "id": finding_id,
            "finding_id": finding_id,
            "mainline_run": {
                "contract_fingerprint": contract["contract_fingerprint"],
            },
        }
        findings_by_id[finding_id] = row
        selected_obligation_id = _text(
            row.get("selected_obligation_id") or row.get("obligation_id")
        )
        gate = _dict(gate_results.get(selected_obligation_id))
        if not gate:
            raise MainlineContractError(
                f"finding_gate_receipt_missing:{finding_id}"
            )
        gate_obligation_id = _text(_dict(gate.get("identity")).get("obligation_id"))
        if gate_obligation_id and gate_obligation_id != _text(row.get("obligation_id")):
            raise MainlineContractError(
                f"finding_gate_obligation_mismatch:{finding_id}"
            )
        if not contract["customer_outputs_published"]:
            shadow.append({
                **row,
                "finding_class": "shadow",
                "shadow_origin": "delivery_gate",
                "semantic_delivery_gate_status": _text(
                    gate.get("semantic_status") or gate.get("status")
                ).upper(),
                "delivery_gate_receipt_id": _text(
                    gate.get("gate_receipt_id") or gate.get("receipt_id")
                ),
            })
        elif _text(gate.get("status")).upper() == "DELIVERABLE":
            deliverable.append(row)
        else:
            candidates.append({
                **row,
                "gate_passed": False,
                "customer_delivery_status": "candidate",
                "customer_delivery_gate_reasons": list(
                    gate.get("reason_codes") or [_text(gate.get("reason_code"))]
                ),
            })
    for gate in gate_results.values():
        if _text(gate.get("status")).upper() != "DELIVERABLE":
            continue
        finding_id = _text(
            _dict(gate.get("identity")).get("finding_id")
            or gate.get("finding_id")
        )
        if finding_id not in findings_by_id:
            raise MainlineContractError(
                f"deliverable_gate_finding_missing:{finding_id or 'MISSING'}"
            )
    return deliverable, candidates, shadow


def _project_gate_results_for_authority(
    *,
    gate_results: dict[str, dict[str, Any]],
    contract: MainlineRunContract,
) -> dict[str, dict[str, Any]]:
    """Project semantic gates into the selected authority's terminal scope."""
    projected = {
        _text(obligation_id): dict(receipt)
        for obligation_id, receipt in gate_results.items()
        if _text(obligation_id) and isinstance(receipt, dict)
    }
    # Semantic Gate receipts are immutable. Shadow publication is projected by
    # `_authority_findings`; it must never rewrite a Gate status or fingerprint.
    return projected


def _finalize_campaign(handle: Any, ledger: dict[str, Any]) -> dict[str, Any]:
    campaign = _campaign_object(handle)
    campaign.record_obligation_attempt_ledger(ledger)
    _campaign_store(handle).save(campaign)
    return {
        **campaign.public_contract(),
        "campaign_mode": _text(_dict(handle).get("mode")),
    }


def _empty_execution_batch() -> dict[str, Any]:
    return {
        "selected_count": 0,
        "executed_count": 0,
        "blocked_count": 0,
        "harness_failure_count": 0,
        "cleanup_failures": 0,
        "findings": [],
        "results": [],
        "compile_results": {},
        "execution_results": {},
        "gate_results": {},
        "every_experiment_has_receipt": True,
    }


def _consume_pending_obligation_rounds(
    *,
    obligation_plan: dict[str, Any],
    obligations: list[dict[str, Any]],
    experiments_by_obligation: dict[str, dict[str, Any]],
    behavior_ir: dict[str, Any],
    root: Any,
    project: str,
    base_url: str,
    runtime_contract: dict[str, Any],
    mainline_run: dict[str, Any],
    campaign_id: str,
    automatic_round_limit: int,
    execute_batch,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Consume ``pending_next_round`` with the same per-round budget.

    Does not raise ``configured_budget``. Each additional campaign round re-runs
    ``plan_obligation_round`` on still-pending COMPILED obligations until the
    pending queue is empty or ``automatic_round_limit`` is reached. Remaining
    pending stay visible as ``OBLIGATION_BUDGET_REACHED`` via manual terminals.
    """

    from .adaptive_discovery_planner import (
        build_agent_intent_plan,
        plan_obligation_round,
    )

    plan_row = dict(_dict(obligation_plan))
    budget = int(plan_row.get("budget") or 0)
    if budget <= 0:
        return [], plan_row
    round_limit = max(1, int(automatic_round_limit or 1))
    pending_rows = [
        dict(row)
        for row in _list(plan_row.get("pending_next_round"))
        if isinstance(row, dict) and _text(row.get("obligation_id"))
    ]
    if not pending_rows or round_limit <= 1:
        return [], plan_row

    obligation_by_id = {
        _text(row.get("obligation_id")): dict(row)
        for row in obligations
        if isinstance(row, dict) and _text(row.get("obligation_id"))
    }
    experiments = {
        _text(key): dict(value)
        for key, value in _dict(experiments_by_obligation).items()
        if _text(key) and isinstance(value, dict)
    }
    follow_on_batches: list[dict[str, Any]] = []
    follow_on_receipts: list[dict[str, Any]] = []
    # ── Enhanced: track resolved bindings across rounds ──
    accumulated_bindings: dict[str, str] = {}
    # ── Enhanced: collect BLOCKED obligations eligible for retry ──
    retry_eligible_reasons = {
        "BLOCKED_MISSING_BINDING",
        "HARNESS_FAILED",
        "BLOCKED_MISSING_OBSERVER",
    }
    # Round 1 already ran; additional rounds are 2..round_limit inclusive.
    for planning_round in range(2, round_limit + 1):
        pending_ids = [
            _text(row.get("obligation_id"))
            for row in pending_rows
            if _text(row.get("obligation_id"))
        ]
        # ── Enhanced: include retry-eligible BLOCKED obligations ──
        blocked_retry_ids = [
            _text(row.get("obligation_id"))
            for row in _list(plan_row.get("blocked_retry_pool"))
            if isinstance(row, dict)
            and _text(row.get("block_reason")) in retry_eligible_reasons
            and _text(row.get("obligation_id"))
        ]
        all_round_ids = list(dict.fromkeys(pending_ids + blocked_retry_ids))
        remaining_obligations = [
            obligation_by_id[oid]
            for oid in all_round_ids
            if oid in obligation_by_id
        ]
        remaining_experiments = {
            oid: experiments[oid]
            for oid in all_round_ids
            if oid in experiments
            and (
                _text(
                    _dict(_dict(experiments[oid]).get("compile_receipt")).get("status")
                ).upper()
                == "COMPILED"
                # Allow retry of blocked experiments that may now succeed
                or _text(
                    _dict(_dict(experiments[oid]).get("compile_receipt")).get("status")
                ).upper()
                in {"BLOCKED", "BLOCKED_MISSING_BINDING", "HARNESS_FAILED"}
            )
        }
        if not remaining_experiments:
            break
        next_plan = plan_obligation_round(
            remaining_obligations,
            experiments_by_obligation=remaining_experiments,
            behavior_ir=behavior_ir,
            budget=budget,
            cold_start_reason="PENDING_NEXT_ROUND_CONTINUATION",
        )
        next_intents = build_agent_intent_plan(
            next_plan,
            obligations=remaining_obligations,
            experiments_by_obligation=remaining_experiments,
            behavior_ir=behavior_ir,
        )
        next_scheduled = [
            dict(row)
            for row in _list(next_intents.get("intents"))
            if isinstance(row, dict)
        ]
        if not next_scheduled:
            pending_rows = [
                dict(row)
                for row in _list(next_plan.get("pending_next_round"))
                if isinstance(row, dict)
            ]
            plan_row = {
                **plan_row,
                "pending_next_round": pending_rows[:600],
                "pending_count": len(pending_rows),
                "stop_condition": _text(next_plan.get("stop_condition"))
                or plan_row.get("stop_condition"),
            }
            break
        next_batch = execute_batch(
            next_scheduled,
            experiments_by_obligation=remaining_experiments,
            behavior_ir=behavior_ir,
            root=root,
            project=project,
            base_url=base_url,
            runtime_contract=runtime_contract,
            mainline_run=mainline_run,
            campaign_id=campaign_id,
        )
        # ── Enhanced: collect runtime bindings from this round ──
        batch_bindings = _dict(_dict(next_batch).get("runtime_bindings"))
        if batch_bindings:
            accumulated_bindings.update(batch_bindings)
        # ── Enhanced: collect BLOCKED experiments for retry pool ──
        blocked_retry_pool: list[dict[str, Any]] = []
        for result_row in _list(_dict(next_batch).get("results")):
            if not isinstance(result_row, dict):
                continue
            status = _text(result_row.get("status") or result_row.get("execution_status")).upper()
            reason = _text(result_row.get("block_reason") or result_row.get("failure_reason"))
            if status in {"BLOCKED", "HARNESS_FAILED"} and reason in retry_eligible_reasons:
                blocked_retry_pool.append({
                    "obligation_id": _text(result_row.get("obligation_id")),
                    "block_reason": reason,
                    "planning_round": planning_round,
                })
        follow_on_batches.append(dict(_dict(next_batch)))
        follow_on_receipts.append({
            "planning_round": planning_round,
            "selected_count": int(next_plan.get("selected_count") or 0),
            "pending_count": int(next_plan.get("pending_count") or 0),
            "executed_count": int(_dict(next_batch).get("executed_count") or 0),
            "budget": budget,
            "accumulated_bindings_count": len(accumulated_bindings),
            "blocked_retry_count": len(blocked_retry_pool),
        })
        pending_rows = [
            dict(row)
            for row in _list(next_plan.get("pending_next_round"))
            if isinstance(row, dict)
        ]
        plan_row = {
            **plan_row,
            "pending_next_round": pending_rows[:600],
            "pending_count": len(pending_rows),
            "stop_condition": _text(next_plan.get("stop_condition"))
            or plan_row.get("stop_condition"),
            "follow_on_round_receipts": follow_on_receipts,
            "blocked_retry_pool": blocked_retry_pool[:100],
            "accumulated_bindings": accumulated_bindings,
        }
        if not pending_rows:
            break
    return follow_on_batches, plan_row

