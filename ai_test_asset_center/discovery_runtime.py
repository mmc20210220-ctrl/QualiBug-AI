"""Single-authority discovery planning and experiment-candidate runtime."""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from .adaptive_planning_history import build_planning_history_receipt
from .adaptive_behavior_ir_expansion import (
    expand_behavior_ir_from_runtime_observations,
)
from .canonical_defect_registry import (
    CanonicalDefectRegistryError,
    build_canonical_defect_registry,
    build_defect_identity_consistency,
    canonical_representative_findings,
)
from .customer_delivery_gate import (
    build_customer_delivery_gate_receipt,
    customer_delivery_rejection_reasons,
)
from .discovery_funnel import build_funnel
from .discovery_mainline import (
    DiscoveryMainlineInputs,
    DiscoveryPlanningBundle,
)
from .discovery_mainline_contract import MainlineContractError, MainlineRunContract
from .discovery_quality_projection import (
    build_formal_count_projection,
    validated_delivery_gate_finding_ids,
)
from .experiment_executor import execute_selected_experiments
from .formal_delivery_scope import formal_customer_deliverable_findings
from .formal_delivery_authority import build_formal_delivery_authority_receipt
from .obligation_attempt_ledger import build_obligation_attempt_ledger
from .operational_receipts import (
    aggregate_execution_operational_receipts,
    build_execution_operational_receipt_from_counts,
)
from .runtime_interface_discovery import (
    execute_runtime_interface_discovery,
    load_runtime_interface_confirmation_tokens,
)


RUNTIME_SCHEMA = "qualibug.discovery-runtime.v1"


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


from .discovery_runtime_planning import (  # noqa: F401
    _api_operations,
    _campaign_object,
    _campaign_store,
    _contract,
    _runtime_actors,
    build_discovery_plan,
)



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
        gate = _dict(gate_results.get(_text(row.get("obligation_id"))))
        if not gate:
            raise MainlineContractError(
                f"finding_gate_receipt_missing:{finding_id}"
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


def run_experiment_candidate(
    inputs: DiscoveryMainlineInputs,
    campaign_handle: Any,
    plan: DiscoveryPlanningBundle,
) -> dict[str, Any]:
    """Execute only the obligation/experiment authority selected by the plan."""

    started = time.time()
    runtime_contract = dict(_dict(plan.experiments.get("runtime_contract")))
    obligation_plan = _dict(plan.experiments.get("obligation_plan"))
    agent_intent_plan = _dict(plan.experiments.get("agent_intent_plan"))
    scheduled = [
        dict(row)
        for row in _list(agent_intent_plan.get("intents"))
        if isinstance(row, dict)
    ]
    runtime_approved = (
        _text(runtime_contract.get("status")) == "approved"
        and bool(_text(runtime_contract.get("approved_base_url")))
    )
    surface_plan = _dict(
        plan.experiments.get("runtime_interface_discovery_plan")
    )
    surface_execution: dict[str, Any] = {
        "selected_count": 0,
        "executed_count": 0,
        "blocked_count": 0,
        "harness_failure_count": 0,
        "cleanup_failures": 0,
        "selected_rows": [],
        "compile_results": {},
        "execution_results": {},
        "gate_results": {},
        "observation_receipts": [],
        "discovered_operations": [],
        "findings": [],
    }
    if runtime_approved and surface_plan:
        actor_tokens = load_runtime_interface_confirmation_tokens(
            inputs.root,
            inputs.project,
        )
        surface_execution = execute_runtime_interface_discovery(
            surface_plan,
            base_url=_text(runtime_contract.get("approved_base_url")),
            mainline_run=plan.mainline_run,
            confirmation_tokens=actor_tokens,
        )
    expansion: dict[str, Any] = {
        "status": "NOT_REQUESTED",
        "behavior_ir": dict(plan.behavior_ir),
        "delta_obligations": [],
        "by_obligation": {},
        "obligation_plan": {},
        "agent_intent_plan": {},
        "selected_rows": [],
        "round_receipt": {},
    }
    if runtime_approved and surface_plan:
        expansion = expand_behavior_ir_from_runtime_observations(
            initial_behavior_ir=plan.behavior_ir,
            existing_obligation_ids={
                _text(row.get("obligation_id"))
                for row in _list(plan.obligations.get("obligations"))
                if isinstance(row, dict) and _text(row.get("obligation_id"))
            },
            knowledge_asset=_dict(plan.experiments.get("_knowledge_asset")),
            documented_operations=[
                dict(row)
                for row in _list(plan.experiments.get("_documented_operations"))
                if isinstance(row, dict)
            ],
            observation_receipts=[
                dict(row)
                for row in _list(surface_execution.get("observation_receipts"))
                if isinstance(row, dict)
            ],
            project_id=inputs.project,
            source_snapshot_hash=_text(
                _dict(inputs.campaign_context.get("source_manifest")).get(
                    "source_hash"
                )
            ),
            runtime_actors=[
                dict(row)
                for row in _list(plan.experiments.get("_runtime_actors"))
                if isinstance(row, dict)
            ],
            environment_type=_text(
                plan.experiments.get("_environment_type")
            ),
            policy_version=_text(inputs.campaign_context.get("policy_version")),
            budget=int(plan.experiments.get("_planning_budget") or 0),
            planning_round=2,
        )

    if runtime_approved and scheduled:
        batch = execute_selected_experiments(
            scheduled,
            experiments_by_obligation=dict(
                _dict(plan.experiments.get("by_obligation"))
            ),
            behavior_ir=plan.behavior_ir,
            root=inputs.root,
            project=inputs.project,
            base_url=_text(runtime_contract.get("approved_base_url")),
            runtime_contract=runtime_contract,
            mainline_run=plan.mainline_run,
            campaign_id=plan.mainline_run["campaign_id"],
        )
    else:
        batch = _empty_execution_batch()

    round_two_scheduled = [
        dict(row)
        for row in _list(
            _dict(expansion.get("agent_intent_plan")).get("intents")
        )
        if isinstance(row, dict)
    ]
    if runtime_approved and round_two_scheduled:
        round_two_batch = execute_selected_experiments(
            round_two_scheduled,
            experiments_by_obligation=dict(
                _dict(expansion.get("by_obligation"))
            ),
            behavior_ir=_dict(expansion.get("behavior_ir")),
            root=inputs.root,
            project=inputs.project,
            base_url=_text(runtime_contract.get("approved_base_url")),
            runtime_contract=runtime_contract,
            mainline_run=plan.mainline_run,
            campaign_id=plan.mainline_run["campaign_id"],
        )
    else:
        round_two_batch = _empty_execution_batch()
    compile_results = {
        _text(key): dict(value)
        for key, value in _dict(batch.get("compile_results")).items()
        if _text(key) and isinstance(value, dict)
    }
    compile_results.update({
        _text(key): dict(value)
        for key, value in _dict(
            round_two_batch.get("compile_results")
        ).items()
        if _text(key) and isinstance(value, dict)
    })
    compile_results.update({
        _text(key): dict(value)
        for key, value in _dict(
            surface_execution.get("compile_results")
        ).items()
        if _text(key) and isinstance(value, dict)
    })
    execution_results = {
        _text(key): dict(value)
        for key, value in _dict(batch.get("execution_results")).items()
        if _text(key) and isinstance(value, dict)
    }
    execution_results.update({
        _text(key): dict(value)
        for key, value in _dict(
            round_two_batch.get("execution_results")
        ).items()
        if _text(key) and isinstance(value, dict)
    })
    execution_results.update({
        _text(key): dict(value)
        for key, value in _dict(
            surface_execution.get("execution_results")
        ).items()
        if _text(key) and isinstance(value, dict)
    })
    gate_results = {
        _text(key): dict(value)
        for key, value in _dict(batch.get("gate_results")).items()
        if _text(key) and isinstance(value, dict)
    }
    gate_results.update({
        _text(key): dict(value)
        for key, value in _dict(round_two_batch.get("gate_results")).items()
        if _text(key) and isinstance(value, dict)
    })
    gate_results.update({
        _text(key): dict(value)
        for key, value in _dict(
            surface_execution.get("gate_results")
        ).items()
        if _text(key) and isinstance(value, dict)
    })
    gate_results = _project_gate_results_for_authority(
        gate_results=gate_results,
        contract=plan.mainline_run,
    )
    initial_selected_rows = _selected_rows(plan)
    expansion_selected_rows = [
        dict(row)
        for row in _list(expansion.get("selected_rows"))
        if isinstance(row, dict)
    ]
    surface_selected_rows = [
        dict(row)
        for row in _list(surface_execution.get("selected_rows"))
        if isinstance(row, dict)
    ]
    selected_rows = (
        initial_selected_rows
        + expansion_selected_rows
        + surface_selected_rows
    )
    _manual_terminal_receipts(
        selected_rows=initial_selected_rows,
        experiments_by_obligation=dict(
            _dict(plan.experiments.get("by_obligation"))
        ),
        obligation_plan=obligation_plan,
        runtime_contract=runtime_contract,
        compile_results=compile_results,
        execution_results=execution_results,
    )
    _manual_terminal_receipts(
        selected_rows=expansion_selected_rows,
        experiments_by_obligation=dict(
            _dict(expansion.get("by_obligation"))
        ),
        obligation_plan=_dict(expansion.get("obligation_plan")),
        runtime_contract=runtime_contract,
        compile_results=compile_results,
        execution_results=execution_results,
    )
    ledger = build_obligation_attempt_ledger(
        mainline_run=plan.mainline_run,
        selected=selected_rows,
        compile_results=compile_results,
        execution_results=execution_results,
        gate_results=gate_results,
    )
    operational_summary = _operational_summary_from_attempt_ledger(ledger)
    planning_history_receipt = build_planning_history_receipt(
        policy_identity=_dict(
            plan.experiments.get("_planning_policy_identity")
        ),
        attempts=[
            dict(row)
            for row in _list(ledger.get("attempts"))
            if isinstance(row, dict)
        ],
    )
    deliverable, candidates, shadow = _authority_findings(
        raw_findings=[
            dict(row)
            for row in (
                _list(batch.get("findings"))
                + _list(round_two_batch.get("findings"))
            )
            if isinstance(row, dict)
        ],
        gate_results=gate_results,
        contract=plan.mainline_run,
    )
    authority_occurrences = (
        deliverable
        if plan.mainline_run["customer_outputs_published"]
        else formal_customer_deliverable_findings(
            shadow,
            obligation_attempt_ledger=ledger,
        )
    )
    canonical_registry = build_canonical_defect_registry(
        mainline_run=plan.mainline_run,
        deliverable_occurrences=authority_occurrences,
        obligation_attempt_ledger=ledger,
    )
    canonical_findings = canonical_representative_findings(
        canonical_registry,
        deliverable_occurrences=authority_occurrences,
    )
    formal_delivery_authority = build_formal_delivery_authority_receipt(
        mainline_run=plan.mainline_run,
        findings=authority_occurrences,
        obligation_attempt_ledger=ledger,
    )
    formal = build_formal_count_projection(
        findings=authority_occurrences,
        candidate_findings=candidates,
        obligation_attempt_ledger=ledger,
        mainline_run=plan.mainline_run,
        canonical_defect_registry=canonical_registry,
    )
    occurrence_ids = list(formal["delivery_occurrence_finding_ids"])
    canonical_ids = list(formal["canonical_defect_ids"])
    representative_canonical_ids = sorted(
        _text(item.get("canonical_defect_id"))
        for item in canonical_findings
        if _text(item.get("canonical_defect_id"))
    )
    defect_identity_consistency = build_defect_identity_consistency(
        occurrence_scopes={
            "delivery_gate_ids": validated_delivery_gate_finding_ids(ledger),
            "formal_authority_occurrence_ids": list(
                formal_delivery_authority["delivery_occurrence_finding_ids"]
            ),
            "registry_occurrence_ids": list(
                canonical_registry["delivery_occurrence_finding_ids"]
            ),
            "formal_projection_occurrence_ids": occurrence_ids,
            "evaluator_submission_occurrence_ids": occurrence_ids,
        },
        canonical_scopes={
            "canonical_registry_ids": list(
                canonical_registry["canonical_defect_ids"]
            ),
            "formal_projection_ids": canonical_ids,
            "product_projection_ids": representative_canonical_ids,
            "evaluator_submission_ids": representative_canonical_ids,
        },
    )
    # Honest execution-phase status: it must reflect what actually happened,
    # not a constant "completed". A blocked/discovery-evolution block, a runtime
    # contract that never reached "approved", or obligations that were selected
    # but never executed must surface as "blocked"; only a genuinely executed
    # (or approved-but-empty) plan earns "completed"/"not_executed".
    blocked_obligations = sum(
        1
        for row in (ledger.get("attempts") or [])
        if _text(_dict(row).get("terminal_status")).upper() in {"BLOCKED", "DEFERRED"}
    )
    executed_count = (
        int(batch.get("executed_count") or 0)
        + int(round_two_batch.get("executed_count") or 0)
        + int(surface_execution.get("executed_count") or 0)
    )
    selected_count = len(selected_rows)
    if _text(runtime_contract.get("status")) == "plan_only":
        # No runtime target was supplied, so execution was never attempted.
        # This is an intentional, clean plan-only state — not a block.
        execution_status_value = "plan_only"
    elif blocked_obligations > 0:
        execution_status_value = "blocked"
    elif selected_count == 0:
        # No obligations were selected for execution: the discovery evolution
        # was blocked (source provenance, runtime-contract, or obligation-plan
        # gate). Execution status is "blocked", never "not_executed" — a scan
        # that cannot select anything to execute has not cleanly completed.
        execution_status_value = "blocked"
    elif executed_count >= selected_count and bool(ledger.get("complete")):
        execution_status_value = "completed"
    elif executed_count > 0:
        execution_status_value = "partial"
    else:
        execution_status_value = "blocked"

    result: dict[str, Any] = {
        "schema_version": RUNTIME_SCHEMA,
        "v12_version": "3.0-mainline",
        "enabled": True,
        "mainline_run": dict(plan.mainline_run),
        "runtime_contract": runtime_contract,
        "campaign": _finalize_campaign(campaign_handle, ledger),
        "behavior_ir": dict(_dict(expansion.get("behavior_ir"))),
        "test_obligations": {
            **dict(plan.obligations),
            "obligations": (
                [
                    dict(row)
                    for row in _list(plan.obligations.get("obligations"))
                    if isinstance(row, dict)
                ]
                + [
                    dict(row)
                    for row in _list(expansion.get("delta_obligations"))
                    if isinstance(row, dict)
                ]
            ),
        },
        "experiment_compile": {
            key: value
            for key, value in plan.experiments.items()
            if key not in {"by_obligation", "runtime_contract"}
            and not str(key).startswith("_")
        },
        "obligation_plan": dict(obligation_plan),
        "planning_budget_receipt": dict(
            _dict(plan.experiments.get("planning_budget_receipt"))
        ),
        "adaptive_planning_history_receipt": planning_history_receipt,
        "agent_intent_plan": dict(agent_intent_plan),
        "agent_semantic_link_receipt": dict(
            _dict(plan.experiments.get("agent_semantic_link_receipt"))
        ),
        "runtime_source_overlay_receipt": dict(
            _dict(plan.experiments.get("runtime_source_overlay_receipt"))
        ),
        "behavior_ir_input_receipt": dict(
            _dict(plan.experiments.get("behavior_ir_input_receipt"))
        ),
        "runtime_interface_discovery": {
            "status": (
                "EXECUTED"
                if int(surface_execution.get("selected_count") or 0) > 0
                else "PLANNED"
                if surface_plan
                else "NOT_REQUESTED"
            ),
            "plan": surface_plan,
            "execution": surface_execution,
        },
        "behavior_ir_expansion": {
            "status": _text(expansion.get("status")),
            "round_receipt": dict(_dict(expansion.get("round_receipt"))),
            "obligation_plan": dict(_dict(expansion.get("obligation_plan"))),
            "agent_intent_plan": dict(
                _dict(expansion.get("agent_intent_plan"))
            ),
        },
        "experiment_execution": {
            "selected_count": len(selected_rows),
            "scheduled_count": (
                len(scheduled)
                + len(round_two_scheduled)
                + int(surface_execution.get("selected_count") or 0)
            ),
            "executed_count": executed_count,
            "blocked_count": (
                int(batch.get("blocked_count") or 0)
                + int(round_two_batch.get("blocked_count") or 0)
                + int(surface_execution.get("blocked_count") or 0)
            ),
            "harness_failure_count": (
                int(batch.get("harness_failure_count") or 0)
                + int(round_two_batch.get("harness_failure_count") or 0)
                + int(surface_execution.get("harness_failure_count") or 0)
            ),
            "cleanup_failures": (
                int(batch.get("cleanup_failures") or 0)
                + int(round_two_batch.get("cleanup_failures") or 0)
                + int(surface_execution.get("cleanup_failures") or 0)
            ),
            "every_experiment_has_receipt": bool(ledger.get("complete")),
            "operational_receipt_summary": operational_summary,
            "results": (
                list(batch.get("results") or [])
                + list(round_two_batch.get("results") or [])
            ),
        },
        "operational_receipt_summary": operational_summary,
        "obligation_attempt_ledger": ledger,
        "canonical_defect_registry": canonical_registry,
        "formal_delivery_authority": formal_delivery_authority,
        "formal_count_projection": formal,
        "defect_identity_consistency": defect_identity_consistency,
        "delivery_occurrences": authority_occurrences,
        "findings": (
            canonical_findings
            if plan.mainline_run["customer_outputs_published"]
            else []
        ),
        "evaluator_canonical_findings": (
            canonical_findings
            if plan.mainline_run["private_evaluator_observation_allowed"]
            else []
        ),
        "candidate_findings": candidates,
        "shadow_findings": shadow,
        "external_findings": [],
        "ui_findings": [],
        "evidence_graphs": [],
        "execution_trace_summaries": [],
        "behavior_slice_ledger": {},
        "phases": {
            "agent_intent": {
                "status": _text(agent_intent_plan.get("status")).lower(),
                "generated": (
                    int(agent_intent_plan.get("intent_count") or 0)
                    + len(round_two_scheduled)
                ),
                "semantic_authority": _text(
                    agent_intent_plan.get("semantic_authority")
                ),
            },
            "agent_semantic_linking": {
                "status": _text(
                    _dict(
                        plan.experiments.get("agent_semantic_link_receipt")
                    ).get("status")
                ).lower(),
                "proposal_count": int(
                    _dict(
                        plan.experiments.get("agent_semantic_link_receipt")
                    ).get("proposal_count")
                    or 0
                ),
                "accepted_relationship_count": int(
                    _dict(
                        plan.experiments.get("agent_semantic_link_receipt")
                    ).get("accepted_relationship_count")
                    or 0
                ),
                "rejected_proposal_count": int(
                    _dict(
                        plan.experiments.get("agent_semantic_link_receipt")
                    ).get("rejected_proposal_count")
                    or 0
                ),
            },
            "behavior_ir": {
                "status": "completed",
                "operation_count": len(
                    _list(_dict(expansion.get("behavior_ir")).get("operations"))
                ),
            },
            "obligation_generation": {
                "status": "completed",
                "selected": len(selected_rows),
            },
            "execution": {
                "status": execution_status_value,
                "executed": executed_count,
                "observed_http_request_count": int(
                    operational_summary.get("observed_http_request_count") or 0
                ),
                "production_http_requests": int(
                    operational_summary.get("production_http_requests") or 0
                ),
                "scenario_attempts": int(
                    operational_summary.get("scenario_attempts") or 0
                ),
                "accepted_write_count": int(
                    operational_summary.get("accepted_write_count") or 0
                ),
                "operational_receipt_complete": bool(
                    operational_summary.get("complete")
                ),
                "blocked": sum(
                    1
                    for row in ledger["attempts"]
                    if _text(row.get("terminal_status")).upper()
                    in {"BLOCKED", "DEFERRED"}
                ),
            },
            "oracle": {
                "status": "completed",
                "total_evaluated": len(gate_results),
                "violations_found": len(deliverable),
            },
        },
        "auto_har": {
            "status": "no_traffic" if execution_status_value == "plan_only" else "receipt_backed",
            "entry_count": int(
                operational_summary.get("observed_http_request_count") or 0
            ),
        },
        "total_duration_ms": int((time.time() - started) * 1000),
    }
    result["adaptive_planning_cost_metrics"] = {
        "schema_version": "qualibug.adaptive-planning-cost.v1",
        "request_count": int(
            operational_summary.get("observed_http_request_count") or 0
        ),
        "request_count_status": (
            "MEASURED"
            if operational_summary.get("complete") is True
            else "NOT_MEASURED"
        ),
        "write_request_count": int(
            operational_summary.get("write_request_attempt_count") or 0
        ),
        "elapsed_ms": result["total_duration_ms"],
        "elapsed_status": "MEASURED",
        "model_token_count": None,
        "model_token_status": "NOT_MEASURED",
        "estimated_cost_usd": None,
        "cost_status": "NOT_MEASURED",
        "unit_formal_deliverable_cost_usd": None,
        "unit_cost_status": "NOT_MEASURED",
        "formal_customer_deliverable_count": int(
            formal.get("formal_customer_deliverable_count") or 0
        ),
    }
    result["discovery_funnel"] = build_funnel(result)
    return result


