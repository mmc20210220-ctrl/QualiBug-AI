"""Discovery experiment-candidate execution and receipt finalization.

Extracted from ``discovery_runtime``. ``run_experiment_candidate`` remains the
execution authority for the experiment-candidate mainline. Helpers live in
``discovery_runtime_execution_support`` and are re-exported here for
compatibility.
"""
from __future__ import annotations

import time
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
from .discovery_funnel import (
    _accounting_rows_for_execution,
    _ensure_accounting_terminal_receipts,
    _build_knowledge_source_flow_receipt,
    _compiled_round0_obligation_ids,
    _execution_ir_with_discovered_operations,
    _formal_obligation_rows_and_identity_receipt,
    _is_discovery_task,
    _runtime_recompile_round0_obligation_ids,
    _selected_rows,
    build_business_discovery_separation,
    build_funnel,
)
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
    _execution_status_and_count,
    _prepare_execution_ir,
    _finalize_campaign,
    _legacy_execution_terminal,
    _legacy_experiment_execution_batch,
    _list,
    _manual_terminal_receipts,
    _merge_experiment_execution_results,
    _operational_summary_from_attempt_ledger,
    _project_gate_results_for_authority,
    _sum_batch_int,
    _text,
)



from .experiment_executor import execute_selected_experiments
from .formal_delivery_authority import build_formal_delivery_authority_receipt
from .formal_delivery_scope import formal_customer_deliverable_findings
from .obligation_attempt_ledger import (
    bind_stage_receipt_identity,
    build_obligation_attempt_ledger,
)
from .operational_receipts import aggregate_execution_operational_receipts
from .runtime_fact_candidate import (
    build_runtime_feedback_receipt,
    project_runtime_fact_candidates,
    related_blocked_obligation_ids,
    reproject_experimentability_with_candidates,
)
from .runtime_interface_discovery import (
    execute_runtime_interface_discovery,
    load_runtime_interface_confirmation_tokens,
)


RUNTIME_SCHEMA = "qualibug.discovery-runtime.v1"


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
        approved_base_url = _text(runtime_contract.get("approved_base_url"))
        actor_tokens = load_runtime_interface_confirmation_tokens(
            inputs.root,
            inputs.project,
            base_url=approved_base_url,
        )
        surface_execution = execute_runtime_interface_discovery(
            surface_plan,
            base_url=approved_base_url,
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
    runtime_feedback: dict[str, Any] = {
        "status": "NOT_REQUESTED",
        "candidate_ledger": {},
        "feedback_receipt": {},
        "expansion": {},
    }
    expansion_planning_context = {
        "root": inputs.root,
        "project": inputs.project,
        "base_url": _text(runtime_contract.get("approved_base_url")),
        "campaign_id": _text(plan.mainline_run.get("campaign_id")),
        "environment_type": _text(plan.experiments.get("_environment_type")),
        "runtime_contract": runtime_contract,
    }
    if runtime_approved and surface_plan:
        surface_candidate_ledger = project_runtime_fact_candidates(
            observation_receipts=[
                dict(row)
                for row in _list(surface_execution.get("observation_receipts"))
                if isinstance(row, dict)
            ],
            campaign_id=_text(plan.mainline_run.get("campaign_id")),
        )
        knowledge_asset, graded_surface_ledger = (
            reproject_experimentability_with_candidates(
                _dict(plan.experiments.get("_knowledge_asset")),
                surface_candidate_ledger,
            )
        )
        runtime_feedback["candidate_ledger"] = graded_surface_ledger
        expansion = expand_behavior_ir_from_runtime_observations(
            initial_behavior_ir=plan.behavior_ir,
            # Runtime expansion may reopen only source-bound compile blockers
            # that had no target request. The same formal identity is retained
            # for that retry; the expansion receipt is the audit event and the
            # immutable round-0 compiled experiments are never downgraded.
            existing_obligation_ids={
                _text(row.get("obligation_id"))
                for row in _list(plan.obligations.get("obligations"))
                if isinstance(row, dict) and _text(row.get("obligation_id"))
            },
            recompile_obligation_ids=_runtime_recompile_round0_obligation_ids(
                plan.obligations.get("obligations"),
                plan.experiments.get("by_obligation"),
            ),
            knowledge_asset=knowledge_asset,
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
            planning_context=expansion_planning_context,
        )
        runtime_feedback["feedback_receipt"] = build_runtime_feedback_receipt(
            candidate_ledger=graded_surface_ledger,
            recompile_obligation_ids=_list(
                _dict(expansion.get("round_receipt")).get(
                    "recompiled_obligation_ids"
                )
            ),
            expansion_status=_text(expansion.get("status")),
            planning_round=2,
            campaign_id=_text(plan.mainline_run.get("campaign_id")),
        )
        runtime_feedback["status"] = _text(expansion.get("status")) or "APPLIED"

    (
        formal_obligation_rows,
        obligation_identity_receipt,
        execution_behavior_ir,
        knowledge_source_flow_receipt,
        _execution_ir,
    ) = _prepare_execution_ir(plan=plan, expansion=expansion)
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

    # SPEC §7.8 — Runtime Feedback after governed execution: emit low-authority
    # Runtime Fact Candidates, re-project experimentability, and reopen related
    # BLOCKED/ABSTRACT obligations on the existing expansion authority. Never
    # promote candidates to ACCEPTED business facts.
    feedback_batch = _empty_execution_batch()
    if runtime_approved:
        interim_execution_results = {
            **{
                _text(key): dict(value)
                for key, value in _dict(batch.get("execution_results")).items()
                if _text(key) and isinstance(value, dict)
            },
            **{
                _text(key): dict(value)
                for key, value in _dict(
                    round_two_batch.get("execution_results")
                ).items()
                if _text(key) and isinstance(value, dict)
            },
        }
        feedback_ledger = project_runtime_fact_candidates(
            observation_receipts=[
                dict(row)
                for row in _list(surface_execution.get("observation_receipts"))
                if isinstance(row, dict)
            ],
            execution_results=interim_execution_results,
            campaign_id=_text(plan.mainline_run.get("campaign_id")),
        )
        feedback_asset, graded_feedback_ledger = (
            reproject_experimentability_with_candidates(
                _dict(plan.experiments.get("_knowledge_asset")),
                feedback_ledger,
            )
        )
        already_executed_ids = {
            _text(key) for key in interim_execution_results if _text(key)
        }
        feedback_recompile_ids = related_blocked_obligation_ids(
            obligations=plan.obligations.get("obligations"),
            experiments_by_obligation=plan.experiments.get("by_obligation"),
            ledger=graded_feedback_ledger,
        ) - already_executed_ids
        # Prefer still-blocked rows from the prior expansion pack when present.
        feedback_recompile_ids |= related_blocked_obligation_ids(
            obligations=(
                _list(expansion.get("recompile_obligations"))
                or _list(expansion.get("round_obligations"))
                or plan.obligations.get("obligations")
            ),
            experiments_by_obligation={
                **_dict(plan.experiments.get("by_obligation")),
                **_dict(expansion.get("by_obligation")),
            },
            ledger=graded_feedback_ledger,
        ) - already_executed_ids
        runtime_feedback["candidate_ledger"] = graded_feedback_ledger
        if feedback_recompile_ids or _list(graded_feedback_ledger.get("candidates")):
            # Expansion merge accepts only fingerprint-valid
            # qualibug.runtime-interface-observation.v1 DISCOVERED receipts.
            # Runtime Fact Candidates remain a separate low-authority ledger and
            # drive recompile_obligation_ids / experimentability re-projection —
            # they must not be coerced into interface-observation receipts.
            feedback_observations = [
                dict(row)
                for row in _list(surface_execution.get("observation_receipts"))
                if isinstance(row, dict)
            ]
            feedback_expansion = expand_behavior_ir_from_runtime_observations(
                initial_behavior_ir=_dict(expansion.get("behavior_ir"))
                or plan.behavior_ir,
                existing_obligation_ids={
                    _text(row.get("obligation_id"))
                    for row in _list(plan.obligations.get("obligations"))
                    if isinstance(row, dict) and _text(row.get("obligation_id"))
                },
                recompile_obligation_ids=feedback_recompile_ids,
                knowledge_asset=feedback_asset,
                documented_operations=[
                    dict(row)
                    for row in _list(plan.experiments.get("_documented_operations"))
                    if isinstance(row, dict)
                ],
                observation_receipts=feedback_observations,
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
                environment_type=_text(plan.experiments.get("_environment_type")),
                policy_version=_text(inputs.campaign_context.get("policy_version")),
                budget=int(plan.experiments.get("_planning_budget") or 0),
                planning_round=3,
                planning_context=expansion_planning_context,
            )
            runtime_feedback["expansion"] = {
                "status": _text(feedback_expansion.get("status")),
                "round_receipt": dict(
                    _dict(feedback_expansion.get("round_receipt"))
                ),
                "recompile_obligation_ids": sorted(feedback_recompile_ids),
            }
            runtime_feedback["feedback_receipt"] = build_runtime_feedback_receipt(
                candidate_ledger=graded_feedback_ledger,
                recompile_obligation_ids=feedback_recompile_ids,
                expansion_status=_text(feedback_expansion.get("status")),
                planning_round=3,
                campaign_id=_text(plan.mainline_run.get("campaign_id")),
            )
            runtime_feedback["status"] = _text(feedback_expansion.get("status")) or (
                "CANDIDATES_ONLY"
            )
            feedback_intents = [
                dict(row)
                for row in _list(
                    _dict(feedback_expansion.get("agent_intent_plan")).get(
                        "intents"
                    )
                )
                if isinstance(row, dict)
                and _text(row.get("obligation_id")) in feedback_recompile_ids
                and _text(row.get("obligation_id")) not in already_executed_ids
            ]
            # Also admit newly compiled recompile_selected_rows.
            for row in _list(feedback_expansion.get("recompile_selected_rows")):
                oid = _text(_dict(row).get("obligation_id"))
                if (
                    oid
                    and oid in feedback_recompile_ids
                    and oid not in already_executed_ids
                    and not any(
                        _text(item.get("obligation_id")) == oid
                        for item in feedback_intents
                    )
                ):
                    feedback_intents.append(dict(row))
            if feedback_intents:
                feedback_batch = execute_selected_experiments(
                    feedback_intents,
                    experiments_by_obligation=dict(
                        _dict(feedback_expansion.get("by_obligation"))
                    ),
                    behavior_ir=_dict(feedback_expansion.get("behavior_ir")),
                    root=inputs.root,
                    project=inputs.project,
                    base_url=_text(runtime_contract.get("approved_base_url")),
                    runtime_contract=runtime_contract,
                    mainline_run=plan.mainline_run,
                    campaign_id=plan.mainline_run["campaign_id"],
                )
                # Merge feedback compile views into expansion so terminals and
                # accounting see the recompiled experiments.
                merged_by_obligation = {
                    **_dict(expansion.get("by_obligation")),
                    **_dict(feedback_expansion.get("by_obligation")),
                }
                expansion = {
                    **expansion,
                    "by_obligation": merged_by_obligation,
                    "feedback_expansion": feedback_expansion,
                }

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
                    for row in _list(
                        expansion.get("round_obligations")
                        or expansion.get("delta_obligations")
                    )
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
    if _dict(feedback_batch.get("execution_results")) or _dict(
        feedback_batch.get("compile_results")
    ):
        business_follow_on_batches = [*business_follow_on_batches, feedback_batch]

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
    (
        initial_accounting_rows,
        expansion_accounting_rows,
        accounting_rows,
    ) = _accounting_rows_for_execution(plan, expansion)
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
    # Budget-deferred / pending terminals must be sealed before the mechanical
    # accounting filler. Running ``_ensure_accounting_terminal_receipts`` first
    # mis-labelled SELECTED+COMPILED gaps as HARNESS_FAILED+BLOCKED_EXECUTION
    # and then blocked manual DEFERRED sealing because execution_results was set.
    _manual_terminal_receipts(
        selected_rows=initial_accounting_rows,
        experiments_by_obligation=dict(
            _dict(plan.experiments.get("by_obligation"))
        ),
        obligation_plan=obligation_plan,
        runtime_contract=runtime_contract,
        compile_results=compile_results,
        execution_results=execution_results,
    )
    _manual_terminal_receipts(
        selected_rows=expansion_accounting_rows,
        experiments_by_obligation=dict(
            _dict(expansion.get("by_obligation"))
        ),
        obligation_plan=_dict(expansion.get("obligation_plan")),
        runtime_contract=runtime_contract,
        compile_results=compile_results,
        execution_results=execution_results,
    )
    _ensure_accounting_terminal_receipts(
        accounting_rows=accounting_rows,
        compile_results=compile_results,
        execution_results=execution_results,
        runtime_contract=runtime_contract,
    )
    (
        compile_results,
        execution_results,
        gate_results,
    ) = bind_stage_receipt_identity(
        mainline_run=plan.mainline_run,
        selected=accounting_rows,
        compile_results=compile_results,
        execution_results=execution_results,
        gate_results=gate_results,
    )
    ledger = build_obligation_attempt_ledger(
        mainline_run=plan.mainline_run,
        selected=accounting_rows,
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
    execution_status_value, executed_count = _execution_status_and_count(
        runtime_contract=runtime_contract,
        ledger=ledger,
        selected_count=len(selected_rows),
        batch=batch,
        business_follow_on_batches=business_follow_on_batches,
        round_two_batch=round_two_batch,
    )
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


