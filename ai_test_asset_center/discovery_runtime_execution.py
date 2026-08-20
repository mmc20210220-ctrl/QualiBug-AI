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
from .discovery_stage_result_merge import merge_discovery_stage_results
from .discovery_runtime_execution_result import (  # noqa: F401
    RUNTIME_SCHEMA, _assemble_experiment_candidate_result)  # noqa: E501
from .discovery_runtime_execution_support import (  # noqa: F401
    _authority_findings,
    _campaign_automatic_round_limit,
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
            automatic_round_limit=_campaign_automatic_round_limit(
                campaign_handle
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
                automatic_round_limit=_campaign_automatic_round_limit(
                    campaign_handle
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

    compile_results, execution_results, gate_results = merge_discovery_stage_results(
        main_initial_batch=batch,
        expansion_initial_batch=round_two_batch,
        feedback_initial_batch=feedback_batch,
        main_follow_on_batches=follow_on_batches,
        expansion_follow_on_batches=expansion_follow_on_batches,
    )
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
    # ── Source contract statements for delivered findings ──
    # Resolve each executed finding's own source_refs against the knowledge
    # asset's rule/permission statement texts and append a 源契约 paragraph
    # when the obligation is rule-bound (permission-matrix rows, rule library,
    # interface contracts).  Obligations without a bound statement contribute
    # nothing.  The finalizer has already appended assertion-derived 源契约 and
    # the always-present 运行时证据 paragraph.
    from .finding_source_contract import (
        attach_evidence_paragraphs,
        build_rule_statement_index,
        resolve_source_ref_statements,
    )

    _knowledge_asset = _dict(plan.experiments.get("_knowledge_asset"))
    if _knowledge_asset:
        _statement_index = build_rule_statement_index(_knowledge_asset)
        for _batch in (
            batch,
            round_two_batch,
            *list(business_follow_on_batches or []),
        ):
            _batch["findings"] = [
                attach_evidence_paragraphs(
                    dict(row),
                    statements=resolve_source_ref_statements(
                        _list(row.get("source_refs")), _statement_index
                    ),
                    with_runtime_evidence=False,
                )
                for row in _list(_batch.get("findings"))
                if isinstance(row, dict)
            ]
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
    return _assemble_experiment_candidate_result(
        plan=plan,
        campaign_handle=campaign_handle,
        runtime_contract=runtime_contract,
        execution_behavior_ir=execution_behavior_ir,
        formal_obligation_rows=formal_obligation_rows,
        obligation_identity_receipt=obligation_identity_receipt,
        obligation_plan=obligation_plan,
        planning_history_receipt=planning_history_receipt,
        agent_intent_plan=agent_intent_plan,
        knowledge_source_flow_receipt=knowledge_source_flow_receipt,
        surface_plan=surface_plan,
        surface_execution=surface_execution,
        expansion=expansion,
        expansion_follow_on_batches=expansion_follow_on_batches,
        runtime_feedback=runtime_feedback,
        selected_rows=selected_rows,
        scheduled=scheduled,
        round_two_scheduled=round_two_scheduled,
        business_follow_on_batches=business_follow_on_batches,
        batch=batch,
        round_two_batch=round_two_batch,
        ledger=ledger,
        operational_summary=operational_summary,
        canonical_registry=canonical_registry,
        formal_delivery_authority=formal_delivery_authority,
        formal=formal,
        defect_identity_consistency=defect_identity_consistency,
        authority_occurrences=authority_occurrences,
        canonical_findings=canonical_findings,
        candidates=candidates,
        shadow=shadow,
        deliverable=deliverable,
        gate_results=gate_results,
        execution_status_value=execution_status_value,
        executed_count=executed_count,
        started=started,
    )
