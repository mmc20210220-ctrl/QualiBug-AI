"""Discovery execution helpers: terminals, authority findings, campaign finalize.

Extracted from ``discovery_runtime_execution``. Symbols are re-exported from
``discovery_runtime_execution`` and ``discovery_runtime`` for compatibility.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .discovery_mainline import DiscoveryPlanningBundle
from .discovery_funnel import (
    _build_knowledge_source_flow_receipt,
    _execution_ir_with_discovered_operations,
    _formal_obligation_rows_and_identity_receipt,
)
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


def _execution_status_and_count(
    *,
    runtime_contract: dict[str, Any],
    ledger: dict[str, Any],
    selected_count: int,
    batch: dict[str, Any],
    business_follow_on_batches: list[dict[str, Any]],
    round_two_batch: dict[str, Any],
) -> tuple[str, int]:
    """Return honest execution status and transport count from terminal receipts."""
    blocked_obligations = sum(
        1
        for row in _list(ledger.get("attempts"))
        if _text(_dict(row).get("selection_status")).upper()
        in {"", "SELECTED"}
        if _text(_dict(row).get("terminal_status")).upper()
        in {"BLOCKED", "DEFERRED"}
    )
    executed_count = (
        int(batch.get("executed_count") or 0)
        + sum(
            int(follow_on.get("executed_count") or 0)
            for follow_on in business_follow_on_batches
        )
        + int(round_two_batch.get("executed_count") or 0)
    )
    if _text(runtime_contract.get("status")) == "plan_only":
        execution_status_value = "plan_only"
    elif blocked_obligations > 0 or selected_count == 0:
        execution_status_value = "blocked"
    elif executed_count >= selected_count and bool(ledger.get("complete")):
        execution_status_value = "completed"
    elif executed_count > 0:
        execution_status_value = "partial"
    else:
        execution_status_value = "blocked"
    return execution_status_value, executed_count


def _prepare_execution_ir(
    *,
    plan: DiscoveryPlanningBundle,
    expansion: dict[str, Any],
) -> tuple[
    list[dict[str, Any]], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any],
]:
    """Prepare the formal identity and execution-time Behavior IR view."""
    formal_rows, identity_receipt = (
        _formal_obligation_rows_and_identity_receipt(plan, expansion)
    )
    execution_behavior_ir = _dict(expansion.get("behavior_ir"))
    source_flow_receipt = _build_knowledge_source_flow_receipt(
        plan=plan,
        behavior_ir=execution_behavior_ir,
        formal_obligation_rows=formal_rows,
    )
    initial_keys = {
        (
            _text(row.get("method")).upper(),
            _text(row.get("path") or row.get("raw_path")),
        )
        for row in _list(plan.behavior_ir.get("operations"))
        if isinstance(row, dict)
    }
    expanded_operations = [
        dict(row)
        for row in _list(_dict(expansion.get("behavior_ir")).get("operations"))
        if isinstance(row, dict)
        and (
            _text(row.get("method")).upper(),
            _text(row.get("path") or row.get("raw_path")),
        ) not in initial_keys
    ]
    execution_ir = _execution_ir_with_discovered_operations(
        plan.behavior_ir,
        expanded_operations,
    )
    return (
        formal_rows,
        identity_receipt,
        execution_behavior_ir,
        source_flow_receipt,
        execution_ir,
    )


def _pending_with_budget_deferred(
    plan: dict[str, Any],
    batch: dict[str, Any],
) -> list[dict[str, Any]]:
    """Merge planner pending with rows this batch could not reach under budget."""
    pending_rows = [
        dict(row)
        for row in _list(_dict(plan).get("pending_next_round"))
        if isinstance(row, dict)
    ]
    carried = {
        _text(row.get("obligation_id"))
        for row in pending_rows
        if _text(row.get("obligation_id"))
    }
    for row in _list(_dict(batch).get("budget_deferred")):
        if not isinstance(row, dict):
            continue
        deferred_id = _text(row.get("obligation_id"))
        if deferred_id and deferred_id not in carried:
            pending_rows.append(dict(row))
            carried.add(deferred_id)
    return pending_rows


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


def _merge_experiment_execution_results(
    *batches: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Retain Finalizer outcomes from every execution batch.

    Primary, follow-on, round-two, and surface batches all run
    ``finalize_experiment_execution``. Counts merge every batch, but the
    persisted ``experiment_execution.results`` projection historically omitted
    follow-on (and surface) rows — dropping TRUE_COMPLETED / EQUIVALENT
    receipts while the obligation ledger still showed cleanup COMPLETED.
    """

    merged: list[dict[str, Any]] = []
    seen_execution_ids: set[str] = set()
    seen_obligation_ids: set[str] = set()
    for batch in batches:
        if not isinstance(batch, dict):
            continue
        for row in _list(batch.get("results")):
            if not isinstance(row, dict):
                continue
            execution_id = _text(row.get("execution_id"))
            obligation_id = _text(row.get("obligation_id"))
            if execution_id and execution_id in seen_execution_ids:
                continue
            if not execution_id and obligation_id and obligation_id in seen_obligation_ids:
                continue
            if execution_id:
                seen_execution_ids.add(execution_id)
            if obligation_id:
                seen_obligation_ids.add(obligation_id)
            merged.append(dict(row))
    return merged


def _sum_batch_int(batches: list[dict[str, Any]], key: str) -> int:
    """Sum an integer counter across execution batches."""

    total = 0
    for batch in batches:
        if isinstance(batch, dict):
            total += int(batch.get(key) or 0)
    return total


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
    # P0-5: map pending obligation_id -> specific not-in-plan reason
    pending_reasons: dict[str, str] = {
        _text(row.get("obligation_id")): _text(row.get("not_in_plan_reason")) or "BUDGET_EXHAUSTED"
        for row in _list(obligation_plan.get("pending_next_round"))
        if isinstance(row, dict) and _text(row.get("obligation_id"))
    }
    runtime_approved = (
        _text(runtime_contract.get("status")) == "approved"
        and bool(_text(runtime_contract.get("approved_base_url")))
    )
    for row in selected_rows:
        obligation_id = _text(row.get("obligation_id"))
        if not obligation_id or obligation_id in execution_results:
            continue
        # Check variant obligation_ids and map them to the original
        _variant_result = None
        for _vid, _vresult in compile_results.items():
            if _vid.startswith(obligation_id + "__v_"):
                _variant_result = _vresult
                break
        if _variant_result is not None and obligation_id not in compile_results:
            compile_results[obligation_id] = dict(_variant_result)
            if obligation_id in execution_results:
                continue
        existing_compile = _dict(compile_results.get(obligation_id))
        existing_compile_status = _text(existing_compile.get("status")).upper()
        # COMPILED without an execution receipt still needs a terminal — especially
        # budget-deferred rows that remain in pending_next_round. Non-COMPILED
        # compile terminals already close the attempt.
        if existing_compile and existing_compile_status != "COMPILED":
            continue
        experiment = _dict(experiments.get(obligation_id))
        compile_receipt = _dict(experiment.get("compile_receipt"))
        compile_status = existing_compile_status or _text(
            compile_receipt.get("status")
        ).upper()
        experiment_id = _text(
            existing_compile.get("experiment_id")
            or experiment.get("experiment_id")
            or compile_receipt.get("experiment_id")
        )
        if not existing_compile and compile_status in {"BLOCKED", "HARNESS_FAILED"}:
            compile_results[obligation_id] = {
                "status": compile_status,
                "reason_code": _text(compile_receipt.get("reason_code"))
                or "BLOCKED_COMPILE",
                "detail": _text(
                    compile_receipt.get("detail")
                    or compile_receipt.get("reason_detail")
                ),
                "experiment_id": experiment_id,
                "cost_coverage_status": "UNKNOWN",
            }
        elif (
            not existing_compile
            and compile_status == "DEFERRED"
            and _text(compile_receipt.get("reason_code"))
        ):
            # The compiler already said WHY it deferred -- e.g.
            # MISSING_PRIMARY_OPERATION for an obligation with no operation to
            # call. Falling through to the branches below discarded that reason and
            # relabelled it OBLIGATION_NOT_IN_PLAN / BUDGET_EXHAUSTED, which reads
            # as "we ran out of budget" when the budget was never the constraint.
            # A wrong reason code is worse than a missing one: it sends the next
            # reader looking for capacity they already have.
            compile_results[obligation_id] = {
                "status": "DEFERRED",
                "reason_code": _text(compile_receipt.get("reason_code")),
                "detail": _text(
                    compile_receipt.get("detail")
                    or compile_receipt.get("reason_detail")
                ),
                "experiment_id": experiment_id,
                "cost_coverage_status": "UNKNOWN",
            }
        elif (
            not existing_compile
            and _text(row.get("selection_status")).upper() == "COMPILE_BLOCKED"
        ):
            compile_results[obligation_id] = {
                "status": "HARNESS_FAILED",
                "reason_code": (
                    "COMPILE_RECEIPT_MISSING"
                    if not compile_status
                    else "BLOCKED_COMPILE"
                ),
                "detail": (
                    "compile_receipt_missing"
                    if not compile_status
                    else "compile_deferred_reason_missing"
                ),
                "experiment_id": experiment_id,
                "cost_coverage_status": "UNKNOWN",
            }
        elif obligation_id in pending_ids:
            # Preserve a real COMPILED compile receipt; defer at execution so
            # budget exhaustion is not misread as a compile failure.
            if compile_status == "COMPILED":
                if obligation_id not in compile_results:
                    compile_results[obligation_id] = {
                        "status": "COMPILED",
                        "experiment_id": experiment_id,
                        "cost_coverage_status": "UNKNOWN",
                    }
                execution_results[obligation_id] = {
                    "status": "DEFERRED",
                    "reason_code": "OBLIGATION_BUDGET_REACHED",
                    "not_in_plan_reason": pending_reasons.get(
                        obligation_id, "BUDGET_EXHAUSTED"
                    ),
                    "detail": "compiled_obligation_deferred_by_execution_budget",
                    "experiment_id": experiment_id,
                    "cost_coverage_status": "UNKNOWN",
                }
            else:
                compile_results[obligation_id] = {
                    "status": "DEFERRED",
                    "reason_code": "OBLIGATION_BUDGET_REACHED",
                    "not_in_plan_reason": pending_reasons.get(
                        obligation_id, "BUDGET_EXHAUSTED"
                    ),
                    "experiment_id": experiment_id,
                    "cost_coverage_status": "UNKNOWN",
                }
        elif obligation_id in scheduled_ids and not runtime_approved:
            if obligation_id not in compile_results:
                compile_results[obligation_id] = {
                    "status": "COMPILED" if compile_status == "COMPILED" else compile_status or "COMPILED",
                    "experiment_id": experiment_id,
                    "cost_coverage_status": "UNKNOWN",
                }
            execution_results[obligation_id] = {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_RUNTIME_TARGET",
                "experiment_id": experiment_id,
                "cost_coverage_status": "UNKNOWN",
            }
        elif existing_compile_status == "COMPILED":
            # Already compiled in a batch/filler, but never executed and not
            # pending — leave the execution gap for
            # ``_ensure_accounting_terminal_receipts`` so status/reason stay
            # aligned (BLOCKED / BLOCKED_EXECUTION), not HARNESS_FAILED.
            continue
        else:
            # Fallback: obligation compiled but not selected/blocked/deferred.
            # Treat as DEFERRED rather than failing the entire run. Do NOT default
            # not_in_plan_reason to BUDGET_EXHAUSTED -- this branch is reached
            # precisely when the obligation is NOT in pending_ids, so the budget is
            # the one explanation that cannot apply. Say it is unattributed instead.
            compile_results[obligation_id] = {
                "status": "DEFERRED",
                "reason_code": "OBLIGATION_NOT_IN_PLAN",
                "not_in_plan_reason": pending_reasons.get(
                    obligation_id, "NOT_IN_PLAN_REASON_UNATTRIBUTED"
                ),
                "detail": _text(compile_receipt.get("detail") or ""),
                "experiment_id": experiment_id,
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
    exclude_obligation_ids: set[str] | None = None,
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
    from .pipeline_slices import _ABS_MAX_SLICE_BUDGET

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
        # Split out of BLOCKED_MISSING_OBSERVER.  Kept retry-eligible so the
        # reason-code refinement does not silently strip retry attempts from
        # obligations that previously received them.
        "BLOCKED_CONTROL_ARM_NOT_PROVEN",
        "BLOCKED_OBSERVER_RECEIPT_INDETERMINATE",
    }
    # ── P0-6: Early stop policy tracking ──
    _NO_PROGRESS_LIMIT = 3   # consecutive rounds with zero new executions
    _SAME_PLAN_LIMIT = 2     # consecutive rounds with identical plan fingerprint
    _SAME_ERROR_LIMIT = 3    # consecutive rounds with identical dominant error
    _no_progress_streak = 0
    _prev_plan_fingerprint = ""
    _same_plan_streak = 0
    _prev_dominant_error = ""
    _same_error_streak = 0
    _early_stop_reason = ""
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
        excluded = exclude_obligation_ids or set()
        all_round_ids = [
            obligation_id
            for obligation_id in dict.fromkeys(pending_ids + blocked_retry_ids)
            if obligation_id not in excluded
        ]
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
                "pending_next_round": pending_rows[:_ABS_MAX_SLICE_BUDGET],
                "pending_count": len(pending_rows),
                "stop_condition": _text(next_plan.get("stop_condition"))
                or plan_row.get("stop_condition"),
            }
            _early_stop_reason = "NO_SCHEDULED_EXPERIMENTS"
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
        _round_executed = int(_dict(next_batch).get("executed_count") or 0)
        follow_on_receipts.append({
            "planning_round": planning_round,
            "selected_count": int(next_plan.get("selected_count") or 0),
            "pending_count": int(next_plan.get("pending_count") or 0),
            "executed_count": _round_executed,
            "budget": budget,
            "accumulated_bindings_count": len(accumulated_bindings),
            "blocked_retry_count": len(blocked_retry_pool),
        })
        # ── P0-6: Early stop condition checks ──
        # (a) No progress: the round attempted nothing. Walking a throttled
        # queue where every attempt is BLOCKED still advances the plan, so
        # only a completely empty result set counts as stuck.
        _round_processed = len(_list(_dict(next_batch).get("results")))
        if _round_processed == 0:
            _no_progress_streak += 1
        else:
            _no_progress_streak = 0
        if _no_progress_streak >= _NO_PROGRESS_LIMIT:
            _early_stop_reason = f"NO_PROGRESS_{_NO_PROGRESS_LIMIT}_CONSECUTIVE_ROUNDS"
            pending_rows = _pending_with_budget_deferred(next_plan, next_batch)
            plan_row = {
                **plan_row,
                "pending_next_round": pending_rows[:_ABS_MAX_SLICE_BUDGET],
                "pending_count": len(pending_rows),
                "stop_condition": _early_stop_reason,
                "follow_on_round_receipts": follow_on_receipts,
                "blocked_retry_pool": blocked_retry_pool[:100],
                "accumulated_bindings": accumulated_bindings,
            }
            break
        # (b) Same plan fingerprint repeated
        import hashlib as _hl
        _plan_fp = _hl.sha256(
            json.dumps(
                [r.get("obligation_id") for r in _list(next_plan.get("selected"))],
                sort_keys=True, default=str,
            ).encode()
        ).hexdigest()[:16]
        if _plan_fp == _prev_plan_fingerprint:
            _same_plan_streak += 1
        else:
            _same_plan_streak = 0
        _prev_plan_fingerprint = _plan_fp
        if _same_plan_streak >= _SAME_PLAN_LIMIT:
            _early_stop_reason = f"SAME_PLAN_{_SAME_PLAN_LIMIT}_CONSECUTIVE_ROUNDS"
            pending_rows = _pending_with_budget_deferred(next_plan, next_batch)
            plan_row = {
                **plan_row,
                "pending_next_round": pending_rows[:_ABS_MAX_SLICE_BUDGET],
                "pending_count": len(pending_rows),
                "stop_condition": _early_stop_reason,
                "follow_on_round_receipts": follow_on_receipts,
                "blocked_retry_pool": blocked_retry_pool[:100],
                "accumulated_bindings": accumulated_bindings,
            }
            break
        # (c) Same dominant error repeated — only named failure reasons count.
        # Batch results put the code in reason_code; an empty reason must not be
        # collapsed to "UNKNOWN" or a throttled queue will look stuck and stop.
        _error_counts: dict[str, int] = {}
        for _r in _list(_dict(next_batch).get("results")):
            _row = _dict(_r)
            _st = _text(
                _row.get("status") or _row.get("execution_status")
            ).upper()
            if _st not in {"BLOCKED", "HARNESS_FAILED", "FAILED"}:
                continue
            _rc = _text(
                _row.get("reason_code")
                or _row.get("block_reason")
                or _row.get("failure_reason")
            )
            if not _rc:
                continue
            _error_counts[_rc] = _error_counts.get(_rc, 0) + 1
        _dominant_error = (
            max(_error_counts, key=_error_counts.get) if _error_counts else ""
        )
        # A per-batch budget leaves later rows deferred. Those later rows are new
        # work, not a retry of the same failure, even when they share a reason
        # code (for example many writes missing an observer). Same-error stop
        # only applies once the batch is no longer draining deferred work.
        _still_draining_budget = bool(
            _list(_dict(next_batch).get("budget_deferred"))
        )
        if (
            not _still_draining_budget
            and _dominant_error
            and _dominant_error == _prev_dominant_error
        ):
            _same_error_streak += 1
        else:
            _same_error_streak = 0
        _prev_dominant_error = _dominant_error
        if _same_error_streak >= _SAME_ERROR_LIMIT:
            _early_stop_reason = (
                f"SAME_ERROR_{_SAME_ERROR_LIMIT}_CONSECUTIVE:{_dominant_error}"
            )
            pending_rows = _pending_with_budget_deferred(
                next_plan, next_batch
            )
            plan_row = {
                **plan_row,
                "pending_next_round": pending_rows[:_ABS_MAX_SLICE_BUDGET],
                "pending_count": len(pending_rows),
                "stop_condition": _early_stop_reason,
                "follow_on_round_receipts": follow_on_receipts,
                "blocked_retry_pool": blocked_retry_pool[:100],
                "accumulated_bindings": accumulated_bindings,
            }
            break
        pending_rows = _pending_with_budget_deferred(next_plan, next_batch)
        plan_row = {
            **plan_row,
            "pending_next_round": pending_rows[:_ABS_MAX_SLICE_BUDGET],
            "pending_count": len(pending_rows),
            "stop_condition": _text(next_plan.get("stop_condition"))
            or plan_row.get("stop_condition"),
            "follow_on_round_receipts": follow_on_receipts,
            "blocked_retry_pool": blocked_retry_pool[:100],
            "accumulated_bindings": accumulated_bindings,
        }
        if not pending_rows:
            _early_stop_reason = "PENDING_QUEUE_EMPTY"
            break
    # Attach early stop reason to plan_row for observability.
    if _early_stop_reason:
        plan_row["early_stop_reason"] = _early_stop_reason
    return follow_on_batches, plan_row

