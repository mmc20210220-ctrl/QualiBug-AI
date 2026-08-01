"""Discovery experiment-candidate execution and receipt finalization.

Extracted from ``discovery_runtime``. ``run_experiment_candidate`` remains the
execution authority for the experiment-candidate mainline. Helpers live in
``discovery_runtime_execution_support`` and are re-exported here for
compatibility.
"""
from __future__ import annotations

import time
from collections import Counter
from typing import Any

from .adaptive_behavior_ir_expansion import (
    expand_behavior_ir_from_runtime_observations,
)
from .adaptive_planning_history import build_planning_history_receipt
from .canonical_defect_registry import (
    build_canonical_defect_registry,
    build_defect_identity_consistency,
    canonical_representative_findings,
)
from .discovery_funnel import build_funnel
from .discovery_mainline import (
    DiscoveryMainlineInputs,
    DiscoveryPlanningBundle,
)
from .discovery_quality_projection import (
    build_formal_count_projection,
    validated_delivery_gate_finding_ids,
)
from .discovery_runtime_execution_support import (  # noqa: F401
    _authority_findings,
    _consume_pending_obligation_rounds,
    _dict,
    _empty_execution_batch,
    _finalize_campaign,
    _legacy_execution_terminal,
    _legacy_experiment_execution_batch,
    _list,
    _manual_terminal_receipts,
    _merge_experiment_execution_results,
    _operational_summary_from_attempt_ledger,
    _project_gate_results_for_authority,
    _selected_rows,
    _sum_batch_int,
    _text,
)

import re as _re

_VARIANT_RE = _re.compile(r"^(.+?)__v_[a-f0-9]+$")


def _compiled_round0_obligation_ids(all_experiments: Any) -> set[str]:
    """Compatibility diagnostic for the historical compiled-only projection.

    Variant experiments (``obl_x__v_<digest>``) collapse to their base
    identity. This helper is not used by the product expansion path: round 2
    receives every immutable round-0 obligation identity, including compile-
    blocked rows, so a retry cannot create a duplicate formal obligation.
    """

    compiled: set[str] = set()
    for row in _list(all_experiments):
        if not isinstance(row, dict):
            continue
        if _text(_dict(row.get("compile_receipt")).get("status")).upper() != "COMPILED":
            continue
        obligation_id = _text(row.get("obligation_id"))
        if not obligation_id:
            continue
        match = _VARIANT_RE.match(obligation_id)
        compiled.add(match.group(1) if match else obligation_id)
    return compiled


def _formal_obligation_rows_and_identity_receipt(
    plan: DiscoveryPlanningBundle,
    expansion: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Validate the final formal identity before any round-2 transport."""

    base_rows = [
        dict(row)
        for row in _list(plan.obligations.get("obligations"))
        if isinstance(row, dict)
    ]
    expansion_rows = [
        dict(row)
        for row in _list(expansion.get("delta_obligations"))
        if isinstance(row, dict)
    ]
    formal_rows = [*base_rows, *expansion_rows]
    base_ids = {
        _text(row.get("obligation_id"))
        for row in base_rows
        if _text(row.get("obligation_id"))
    }
    expansion_ids = {
        _text(row.get("obligation_id"))
        for row in expansion_rows
        if _text(row.get("obligation_id"))
    }
    all_id_values = [
        _text(row.get("obligation_id")) for row in formal_rows
    ]
    missing_formal_ids = sum(not value for value in all_id_values)
    duplicate_formal_ids = sorted(
        obligation_id
        for obligation_id, count in Counter(
            value for value in all_id_values if value
        ).items()
        if count > 1
    )
    expansion_overlap_ids = sorted(base_ids & expansion_ids)
    if missing_formal_ids or duplicate_formal_ids or expansion_overlap_ids:
        raise ValueError(
            "formal_obligation_identity_invalid:"
            f"missing={missing_formal_ids};"
            f"duplicates={','.join(duplicate_formal_ids[:20])};"
            f"expansion_overlap={','.join(expansion_overlap_ids[:20])}"
        )
    planning_identity_receipt = _dict(
        plan.obligations.get("obligation_identity_receipt")
    )
    return formal_rows, {
        "schema_version": "qualibug.obligation-identity-receipt.v1",
        "authority": "discovery_runtime_execution.formal_obligation_rows",
        "status": "PASS",
        "input_row_count": len(formal_rows),
        "unique_count": len(formal_rows),
        "duplicate_count": 0,
        "duplicate_ids": [],
        "missing_id_count": 0,
        "expansion_added_count": len(expansion_rows),
        "expansion_overlap_ids": [],
        "planning_receipt": planning_identity_receipt,
    }


def _execution_ir_with_discovered_operations(
    behavior_ir: dict[str, Any],
    discovered_operations: Any,
) -> dict[str, Any]:
    """Append governed runtime-discovered operations to an execution IR view.

    Round-1 experiments were compiled against the immutable documented IR, but
    governed runtime interface discovery may prove additional routes before
    round 1 executes (e.g. GET /api/users/addresses for an order fixture's
    addressId dependency). The discovered operations are appended without
    rebuilding the IR so compiled operation identities stay valid; the same
    observations are covered by the behavior-ir-expansion-round receipt.
    """
    ir = dict(_dict(behavior_ir))
    rows = [
        dict(row)
        for row in _list(discovered_operations)
        if isinstance(row, dict)
    ]
    if not rows:
        return ir
    existing_ops = [
        dict(row)
        for row in _list(ir.get("operations"))
        if isinstance(row, dict)
    ]
    existing_keys = {
        (
            _text(row.get("method")).upper(),
            _text(row.get("path") or row.get("raw_path")),
        )
        for row in existing_ops
    }
    added = 0
    for row in rows:
        normalized = dict(row)
        if not _text(normalized.get("id")):
            # Runtime-discovered operations carry operation_id, while runtime
            # operation indexes are keyed by id. Normalize so the execution IR
            # view exposes the discovered route to resolver lookups.
            normalized["id"] = _text(normalized.get("operation_id"))
        row = normalized
        key = (
            _text(row.get("method")).upper(),
            _text(row.get("path") or row.get("raw_path")),
        )
        if key and key not in existing_keys:
            existing_ops.append(dict(row))
            existing_keys.add(key)
            added += 1
    if added:
        ir = {**ir, "operations": existing_ops}
    return ir
from .experiment_executor import execute_selected_experiments
from .formal_delivery_authority import build_formal_delivery_authority_receipt
from .formal_delivery_scope import formal_customer_deliverable_findings
from .obligation_attempt_ledger import (
    bind_stage_receipt_identity,
    build_obligation_attempt_ledger,
)
from .operational_receipts import aggregate_execution_operational_receipts
from .runtime_interface_discovery import (
    execute_runtime_interface_discovery,
    load_runtime_interface_confirmation_tokens,
)


RUNTIME_SCHEMA = "qualibug.discovery-runtime.v1"

# ── P0-2: Business / Discovery funnel separation ──
_DISCOVERY_REASON_CODES = frozenset({
    "SURFACE_DISCOVERY_OBSERVATION_ONLY",
})
_DISCOVERY_FAMILIES = frozenset({
    "interface_discovery",
})


def _is_discovery_task(attempt: dict[str, Any]) -> bool:
    """Classify an attempt as a discovery task vs business obligation."""
    reason = _text(attempt.get("reason_code")).upper()
    family = _text(attempt.get("risk_family")).lower()
    return reason in _DISCOVERY_REASON_CODES or family in _DISCOVERY_FAMILIES


def build_business_discovery_separation(
    ledger: dict[str, Any],
    findings: list[dict[str, Any]],
) -> dict[str, Any]:
    """Separate ledger attempts into business obligations and discovery tasks.

    Produces per-type funnel statistics for business obligations and a
    separate discovery task summary. Existing metrics remain compatible.
    """
    attempts = [_dict(a) for a in _list(ledger.get("attempts"))]
    business_attempts = [a for a in attempts if not _is_discovery_task(a)]
    discovery_attempts = [a for a in attempts if _is_discovery_task(a)]

    # ── Business obligation per-type funnel ──
    _STAGES = (
        "generated", "eligible", "planned", "prepared",
        "executed", "observed", "oracle_evaluated", "finding", "confirmed_tp",
    )
    per_type: dict[str, dict[str, int]] = {}
    for a in business_attempts:
        family = _text(a.get("risk_family")) or "unknown"
        if family not in per_type:
            per_type[family] = {s: 0 for s in _STAGES}
            per_type[family]["blocked"] = 0
        row = per_type[family]
        row["generated"] += 1
        terminal = _text(a.get("terminal_status")).upper()
        reason = _text(a.get("reason_code")).upper()
        # Eligible: not blocked at compile
        if terminal not in ("DEFERRED",) or reason != "OBLIGATION_NOT_IN_PLAN":
            row["eligible"] += 1
        # Planned: selected for execution
        if reason != "OBLIGATION_NOT_IN_PLAN" and terminal != "DEFERRED":
            row["planned"] += 1
        # Executed: reached transport
        if terminal in ("DELIVERABLE", "REJECTED", "HARNESS_FAILED"):
            row["executed"] += 1
        # Observed: has observation receipts
        if terminal in ("DELIVERABLE", "REJECTED"):
            row["observed"] += 1
        # Oracle evaluated
        if terminal in ("DELIVERABLE", "REJECTED") and reason not in (
            "CONTRACT_ORACLE_BLOCKED", "CONTRACT_ORACLE_HARNESS_FAILED",
        ):
            row["oracle_evaluated"] += 1
        # Finding produced
        if terminal == "DELIVERABLE":
            row["finding"] += 1
        # Blocked
        if terminal in ("BLOCKED", "HARNESS_FAILED") or reason.startswith("BLOCKED"):
            row["blocked"] += 1

    # ── Business funnel totals ──
    biz_total = {s: sum(per_type[t][s] for t in per_type) for s in _STAGES}
    biz_total["blocked"] = sum(per_type[t]["blocked"] for t in per_type)

    # ── Discovery task summary ──
    disc_executed = sum(
        1 for a in discovery_attempts
        if _text(a.get("terminal_status")).upper() in ("REJECTED", "EXECUTED", "DELIVERABLE")
    )
    discovery_summary = {
        "generated_discovery_tasks": len(discovery_attempts),
        "executed_discovery_tasks": disc_executed,
        "successful_discovery_tasks": disc_executed,
        "discovered_operations": 0,  # filled by runtime_interface_discovery
        "discovered_observers": 0,
    }

    return {
        "schema_version": "qualibug.business-discovery-separation.v1",
        "business_obligation_summary": {
            "total": len(business_attempts),
            **biz_total,
        },
        "business_per_type": per_type,
        "discovery_task_summary": discovery_summary,
        "separation_note": (
            f"{len(business_attempts)} business obligations separated from "
            f"{len(discovery_attempts)} discovery tasks. "
            "Discovery tasks do not consume business execution budget."
        ),
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
            # Runtime expansion is planning round 2. Its obligation identity
            # namespace must be disjoint from the immutable round-0 plan,
            # including compile-blocked obligations; retrying one under the
            # same identity would create two formal rows for one obligation.
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

    formal_obligation_rows, obligation_identity_receipt = (
        _formal_obligation_rows_and_identity_receipt(plan, expansion)
    )

    # Round-1 experiments were compiled against the immutable documented IR,
    # but the governed runtime interface discovery already proved additional
    # routes before round 1 executes (e.g. GET /api/users/addresses). Disposable
    # order fixtures need that route to fill the documented addressId
    # dependency. Append the proven discovered operations to the execution-time
    # IR view without rebuilding it, so compiled operation identities stay valid
    # and the round-1 runtime can resolve fixture dependencies on routes that
    # the expansion receipt already records.
    _initial_operation_keys = {
        (
            _text(row.get("method")).upper(),
            _text(row.get("path") or row.get("raw_path")),
        )
        for row in _list(plan.behavior_ir.get("operations"))
        if isinstance(row, dict)
    }
    _expanded_operations = [
        dict(row)
        for row in _list(_dict(expansion.get("behavior_ir")).get("operations"))
        if isinstance(row, dict)
        and (
            _text(row.get("method")).upper(),
            _text(row.get("path") or row.get("raw_path")),
        ) not in _initial_operation_keys
    ]
    _execution_ir = _execution_ir_with_discovered_operations(
        plan.behavior_ir,
        _expanded_operations,
    )

    if runtime_approved and scheduled:
        batch = execute_selected_experiments(
            scheduled,
            experiments_by_obligation=dict(
                _dict(plan.experiments.get("by_obligation"))
            ),
            behavior_ir=_execution_ir,
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
    # ``round_two_scheduled`` is the full intent queue, while the executor can
    # defer its tail behind the per-batch safety budget.  Only obligations that
    # actually reached this batch may be excluded from continuation; excluding
    # the deferred tail drops it from the pending queue and later projects it as
    # OBLIGATION_NOT_IN_PLAN without ever offering it to execution.
    round_two_deferred_ids = {
        _text(row.get("obligation_id"))
        for row in _list(round_two_batch.get("budget_deferred"))
        if isinstance(row, dict) and _text(row.get("obligation_id"))
    }
    round_two_executed_ids = {
        _text(row.get("obligation_id"))
        for row in round_two_scheduled
        if _text(row.get("obligation_id"))
        and _text(row.get("obligation_id")) not in round_two_deferred_ids
    }

    follow_on_batches: list[dict[str, Any]] = []
    expansion_follow_on_batches: list[dict[str, Any]] = []
    if runtime_approved:
        # Round 1 runs at most the per-batch safety budget. Whatever it deferred
        # is still a scheduled obligation, so it joins the pending queue instead
        # of vanishing without a terminal receipt.
        _round_one_deferred = [
            dict(row)
            for row in _list(batch.get("budget_deferred"))
            if isinstance(row, dict) and _text(row.get("obligation_id"))
        ]
        if _round_one_deferred:
            _already_pending = {
                _text(row.get("obligation_id"))
                for row in _list(_dict(obligation_plan).get("pending_next_round"))
                if isinstance(row, dict)
            }
            obligation_plan = {
                **_dict(obligation_plan),
                "pending_next_round": [
                    *_list(_dict(obligation_plan).get("pending_next_round")),
                    *[
                        row
                        for row in _round_one_deferred
                        if _text(row.get("obligation_id")) not in _already_pending
                    ],
                ],
            }
        follow_on_batches, obligation_plan = _consume_pending_obligation_rounds(
            obligation_plan=obligation_plan,
            obligations=[
                dict(row)
                for row in _list(plan.obligations.get("obligations"))
                if isinstance(row, dict)
            ],
            experiments_by_obligation=dict(
                _dict(plan.experiments.get("by_obligation"))
            ),
            behavior_ir=_execution_ir,
            root=inputs.root,
            project=inputs.project,
            base_url=_text(runtime_contract.get("approved_base_url")),
            runtime_contract=runtime_contract,
            mainline_run=plan.mainline_run,
            campaign_id=plan.mainline_run["campaign_id"],
            automatic_round_limit=int(
                getattr(campaign_handle, "automatic_round_limit", 16) or 16
            ),
            execute_batch=execute_selected_experiments,
            exclude_obligation_ids=round_two_executed_ids,
        )
        # Keep the live plan view aligned with drained pending for terminals.
        if isinstance(plan.experiments, dict):
            plan.experiments["obligation_plan"] = obligation_plan

        # Runtime interface expansion owns a separate obligation plan.  Its
        # first batch is intentionally capped by the same planning budget as
        # round 1, so the remainder must enter the existing pending-round
        # continuation authority as well.  Leaving this queue to the manual
        # terminal projector makes every undispatched expansion obligation
        # look like a budget failure even though it was never offered to the
        # executor.
        expansion_obligation_plan = _dict(expansion.get("obligation_plan"))
        _round_two_deferred = [
            dict(row)
            for row in _list(round_two_batch.get("budget_deferred"))
            if isinstance(row, dict) and _text(row.get("obligation_id"))
        ]
        if _round_two_deferred:
            _already_pending = {
                _text(row.get("obligation_id"))
                for row in _list(expansion_obligation_plan.get("pending_next_round"))
                if isinstance(row, dict)
            }
            expansion_obligation_plan = {
                **expansion_obligation_plan,
                "pending_next_round": [
                    *_list(expansion_obligation_plan.get("pending_next_round")),
                    *[
                        row
                        for row in _round_two_deferred
                        if _text(row.get("obligation_id")) not in _already_pending
                    ],
                ],
            }
        expansion_follow_on_batches, expansion_obligation_plan = (
            _consume_pending_obligation_rounds(
                obligation_plan=expansion_obligation_plan,
                obligations=[
                    dict(row)
                    for row in _list(expansion.get("delta_obligations"))
                    if isinstance(row, dict)
                ],
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
                automatic_round_limit=int(
                    getattr(campaign_handle, "automatic_round_limit", 16) or 16
                ),
                execute_batch=execute_selected_experiments,
                exclude_obligation_ids=round_two_executed_ids,
            )
        )
        expansion["obligation_plan"] = expansion_obligation_plan

    business_follow_on_batches = [
        *follow_on_batches,
        *expansion_follow_on_batches,
    ]

    compile_results = {
        _text(key): dict(value)
        for key, value in _dict(batch.get("compile_results")).items()
        if _text(key) and isinstance(value, dict)
    }
    for follow_on in business_follow_on_batches:
        compile_results.update({
            _text(key): dict(value)
            for key, value in _dict(follow_on.get("compile_results")).items()
            if _text(key) and isinstance(value, dict)
        })
    compile_results.update({
        _text(key): dict(value)
        for key, value in _dict(
            round_two_batch.get("compile_results")
        ).items()
        if _text(key) and isinstance(value, dict)
    })
    execution_results = {
        _text(key): dict(value)
        for key, value in _dict(batch.get("execution_results")).items()
        if _text(key) and isinstance(value, dict)
    }
    for follow_on in business_follow_on_batches:
        execution_results.update({
            _text(key): dict(value)
            for key, value in _dict(follow_on.get("execution_results")).items()
            if _text(key) and isinstance(value, dict)
        })
    execution_results.update({
        _text(key): dict(value)
        for key, value in _dict(
            round_two_batch.get("execution_results")
        ).items()
        if _text(key) and isinstance(value, dict)
    })
    gate_results = {
        _text(key): dict(value)
        for key, value in _dict(batch.get("gate_results")).items()
        if _text(key) and isinstance(value, dict)
    }
    for follow_on in business_follow_on_batches:
        gate_results.update({
            _text(key): dict(value)
            for key, value in _dict(follow_on.get("gate_results")).items()
            if _text(key) and isinstance(value, dict)
        })
    gate_results.update({
        _text(key): dict(value)
        for key, value in _dict(round_two_batch.get("gate_results")).items()
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
    expansion_selected_ids = {
        _text(row.get("obligation_id"))
        for row in expansion_selected_rows
        if _text(row.get("obligation_id"))
    }
    selected_rows = (
        [
            row
            for row in initial_selected_rows
            if _text(row.get("obligation_id")) not in expansion_selected_ids
        ]
        + expansion_selected_rows
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
    (
        compile_results,
        execution_results,
        gate_results,
    ) = bind_stage_receipt_identity(
        mainline_run=plan.mainline_run,
        selected=selected_rows,
        compile_results=compile_results,
        execution_results=execution_results,
        gate_results=gate_results,
    )
    ledger = build_obligation_attempt_ledger(
        mainline_run=plan.mainline_run,
        selected=selected_rows,
        compile_results=compile_results,
        execution_results=execution_results,
        gate_results=gate_results,
    )
    business_operational_summary = _operational_summary_from_attempt_ledger(ledger)
    surface_execution_rows = [
        dict(row)
        for row in _dict(surface_execution.get("execution_results")).values()
        if isinstance(row, dict)
    ]
    surface_operational_receipts = [
        dict(row["operational_receipt"])
        for row in surface_execution_rows
        if isinstance(row.get("operational_receipt"), dict)
    ]
    surface_operational_summary = aggregate_execution_operational_receipts(
        surface_operational_receipts
    )
    surface_missing_receipts = [
        _text(row.get("obligation_id") or row.get("candidate_id"))
        for row in surface_execution_rows
        if not isinstance(row.get("operational_receipt"), dict)
    ]
    surface_operational_summary.update({
        "complete": not surface_missing_receipts
        and len(surface_operational_receipts) == len(surface_execution_rows),
        "missing_obligation_ids": surface_missing_receipts,
    })
    all_operational_receipts = [
        dict(row["operational_receipt"])
        for row in _dict(ledger).get("attempts", [])
        if isinstance(row, dict) and isinstance(row.get("operational_receipt"), dict)
    ] + surface_operational_receipts
    operational_summary = aggregate_execution_operational_receipts(
        all_operational_receipts
    )
    operational_summary.update({
        "complete": bool(business_operational_summary.get("complete"))
        and bool(surface_operational_summary.get("complete")),
        "missing_obligation_ids": [
            *_list(business_operational_summary.get("missing_obligation_ids")),
            *_list(surface_operational_summary.get("missing_obligation_ids")),
        ],
        "business": business_operational_summary,
        "surface_discovery": surface_operational_summary,
    })
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
                + [
                    item
                    for follow_on in business_follow_on_batches
                    for item in _list(follow_on.get("findings"))
                ]
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
        + sum(
            int(follow_on.get("executed_count") or 0)
            for follow_on in business_follow_on_batches
        )
        + int(round_two_batch.get("executed_count") or 0)
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


