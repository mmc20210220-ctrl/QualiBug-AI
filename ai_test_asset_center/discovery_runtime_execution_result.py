"""Final result assembly for the experiment-candidate execution authority.

Extracted from ``discovery_runtime_execution`` to restore that module's
architecture budget. ``run_experiment_candidate`` delegates the terminal
result/funnel/separation assembly here; every input is a receipt or value
already produced by the execution stage, so this module adds no new authority.
"""
from __future__ import annotations

import time
from typing import Any

from .discovery_funnel import (
    build_business_discovery_separation,
    build_funnel,
)
from .discovery_runtime_execution_support import (
    _dict,
    _finalize_campaign,
    _list,
    _merge_experiment_execution_results,
    _sum_batch_int,
    _text,
)


RUNTIME_SCHEMA = "qualibug.discovery-runtime.v1"


def _assemble_experiment_candidate_result(
    *,
    plan: Any,
    campaign_handle: Any,
    runtime_contract: Any,
    execution_behavior_ir: Any,
    formal_obligation_rows: Any,
    obligation_identity_receipt: Any,
    obligation_plan: Any,
    planning_history_receipt: Any,
    agent_intent_plan: Any,
    knowledge_source_flow_receipt: Any,
    surface_plan: Any,
    surface_execution: Any,
    expansion: Any,
    expansion_follow_on_batches: Any,
    runtime_feedback: Any,
    selected_rows: Any,
    scheduled: Any,
    round_two_scheduled: Any,
    business_follow_on_batches: Any,
    batch: Any,
    round_two_batch: Any,
    ledger: Any,
    operational_summary: Any,
    canonical_registry: Any,
    formal_delivery_authority: Any,
    formal: Any,
    defect_identity_consistency: Any,
    authority_occurrences: Any,
    canonical_findings: Any,
    candidates: Any,
    shadow: Any,
    deliverable: Any,
    gate_results: Any,
    execution_status_value: Any,
    executed_count: Any,
    started: Any,
) -> dict[str, Any]:
    """Assemble the terminal discovery-runtime result receipt."""
    result: dict[str, Any] = {
        "schema_version": RUNTIME_SCHEMA,
        "v12_version": "3.0-mainline",
        "enabled": True,
        "mainline_run": dict(plan.mainline_run),
        "runtime_contract": runtime_contract,
        "campaign": _finalize_campaign(campaign_handle, ledger),
        "behavior_ir": dict(execution_behavior_ir),
        "test_obligations": {
            **dict(plan.obligations),
            "obligations": formal_obligation_rows,
            "obligation_identity_receipt": obligation_identity_receipt,
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
        "knowledge_source_flow_receipt": knowledge_source_flow_receipt,
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
            "follow_on_batch_count": len(expansion_follow_on_batches),
            "follow_on_round_receipts": list(
                _list(
                    _dict(expansion.get("obligation_plan")).get(
                        "follow_on_round_receipts"
                    )
                )
            ),
            "agent_intent_plan": dict(
                _dict(expansion.get("agent_intent_plan"))
            ),
        },
        "runtime_feedback": {
            "status": _text(runtime_feedback.get("status")),
            "candidate_ledger": dict(
                _dict(runtime_feedback.get("candidate_ledger"))
            ),
            "feedback_receipt": dict(
                _dict(runtime_feedback.get("feedback_receipt"))
            ),
            "expansion": dict(_dict(runtime_feedback.get("expansion"))),
            "high_authority_promotions": 0,
        },
        "experiment_execution": {
            "selected_count": len(selected_rows),
            "scheduled_count": (
                len(scheduled)
                + len(round_two_scheduled)
                + sum(
                    int(item.get("selected_count") or 0)
                    for item in business_follow_on_batches
                )
                + int(surface_execution.get("selected_count") or 0)
            ),
            "business_selected_count": len(selected_rows),
            "surface_discovery_selected_count": int(
                surface_execution.get("selected_count") or 0
            ),
            "surface_discovery_executed_count": int(
                surface_execution.get("executed_count") or 0
            ),
            "surface_discovery_harness_failure_count": int(
                surface_execution.get("harness_failure_count") or 0
            ),
            "executed_count": executed_count,
            "blocked_count": _sum_batch_int(
                [batch, *business_follow_on_batches, round_two_batch],
                "blocked_count",
            ),
            "harness_failure_count": _sum_batch_int(
                [batch, *business_follow_on_batches, round_two_batch],
                "harness_failure_count",
            ),
            "cleanup_failures": _sum_batch_int(
                [batch, *business_follow_on_batches, round_two_batch],
                "cleanup_failures",
            ),
            "every_experiment_has_receipt": bool(ledger.get("complete")),
            "operational_receipt_summary": operational_summary,
            # Finalizer TRUE_COMPLETED / EQUIVALENT live on full outcome rows.
            # Follow-on and surface batches must be retained here — ledger cleanup
            # COMPLETED alone cannot reconstruct Finalizer receipts.
            "results": _merge_experiment_execution_results(
                batch,
                *business_follow_on_batches,
                round_two_batch,
                surface_execution,
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
                    if _text(row.get("selection_status")).upper()
                    in {"", "SELECTED"}
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
    # ── P0-2: Business / Discovery separation ──
    _separation = build_business_discovery_separation(
        ledger,
        canonical_findings if plan.mainline_run["customer_outputs_published"] else [],
    )
    # Discovery observations are intentionally outside the business obligation
    # ledger. Keep their counts and proven operations visible in a separate
    # receipt-backed projection so they cannot be mistaken for business
    # execution or cause assertion/oracle stages to expect business receipts.
    _separation["discovery_task_summary"].update({
        "generated_discovery_tasks": int(surface_execution.get("selected_count") or 0),
        "executed_discovery_tasks": int(surface_execution.get("executed_count") or 0),
        "successful_discovery_tasks": sum(
            1
            for row in _list(surface_execution.get("observation_receipts"))
            if _text(_dict(row).get("status")).upper() == "DISCOVERED"
        ),
        "discovery_harness_failure_count": int(
            surface_execution.get("harness_failure_count") or 0
        ),
    })
    # Fill discovered_operations from surface execution
    _disc_ops = _list(surface_execution.get("discovered_operations"))
    _separation["discovery_task_summary"]["discovered_operations"] = len(_disc_ops)
    result["business_discovery_separation"] = _separation
    return result
