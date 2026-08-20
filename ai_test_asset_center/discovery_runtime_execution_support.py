"""Compatibility facade with lossless pending-round continuation authority.

The current execution-support implementation is preserved byte-for-byte in
``discovery_runtime_execution_support_base``. Only pending continuation is
overridden: the public pending preview may remain bounded while in-process
scheduling retains every eligible, not-yet-processed identity.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from . import discovery_runtime_execution_support_base as _base
from .adaptive_discovery_planner import _obligation_view_from_compiled_experiment
from .recall_pending_continuation_authority import (
    _compile_status,
    _rows_for_ids,
    complete_pending_continuation_rows,
)

_LOGGER = logging.getLogger(__name__)

# Preserve the complete historical module surface, including private helpers
# imported by discovery_runtime_execution and compatibility tests.
for _name in dir(_base):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_base, _name))


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _continuation_obligation_universe(
    *,
    obligations: list[dict[str, Any]],
    experiments_by_obligation: dict[str, dict[str, Any]],
    obligation_plan: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return the planner-visible obligation universe for continuation rounds.

    Round-one intent binding already accepts compiler-expanded obligation ids
    that exist only in ``experiments_by_obligation``. A deferred compiled-only
    id must therefore remain planner-visible in round 2+ as well; otherwise it
    is present in the pending queue but disappears while ``remaining_obligations``
    is built. Reconstruct the exact same source-backed obligation view used by
    ``build_agent_intent_plan`` and carry coverage-unit metadata from the plan
    or compiled experiment. No new semantic fields are invented.
    """
    merged: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for raw in obligations:
        if not isinstance(raw, dict):
            continue
        oid = _text(raw.get("obligation_id"))
        if not oid or oid in by_id:
            continue
        row = dict(raw)
        by_id[oid] = row
        merged.append(row)

    plan_metadata: dict[str, dict[str, Any]] = {}
    unit_by_obligation_id: dict[str, str] = {}
    for key in ("selected", "pending_next_round", "selected_units"):
        for raw in _list(_dict(obligation_plan).get(key)):
            if not isinstance(raw, dict):
                continue
            oid = _text(raw.get("obligation_id"))
            if oid:
                plan_metadata[oid] = dict(raw)
            unit_id = _text(raw.get("coverage_unit_id"))
            if not unit_id:
                continue
            if oid:
                unit_by_obligation_id[oid] = unit_id
            for member in _list(raw.get("obligation_ids")):
                member_id = _text(member)
                if member_id:
                    unit_by_obligation_id[member_id] = unit_id

    for raw_key, raw_experiment in _dict(experiments_by_obligation).items():
        oid = _text(raw_key)
        experiment = _dict(raw_experiment)
        if not oid or oid in by_id or not experiment:
            continue
        view = _obligation_view_from_compiled_experiment(experiment, oid)
        if view is None:
            continue

        parent_id = _text(
            experiment.get("expanded_from_obligation_id")
            or experiment.get("representative_obligation_id")
        )
        parent = by_id.get(parent_id) or {}
        for field in (
            "confidence",
            "subject_refs",
            "property",
            "pre_transport_executable",
            "canonical_obligation_key",
        ):
            if field in parent and field not in view:
                view[field] = parent[field]

        metadata = plan_metadata.get(oid) or {}
        unit_id = _text(
            metadata.get("coverage_unit_id")
            or experiment.get("coverage_unit_id")
            or unit_by_obligation_id.get(oid)
            or parent.get("coverage_unit_id")
        )
        if unit_id:
            view["coverage_unit_id"] = unit_id
        canonical_key = _text(
            metadata.get("canonical_obligation_key")
            or experiment.get("canonical_obligation_key")
            or parent.get("canonical_obligation_key")
        )
        if canonical_key:
            view["canonical_obligation_key"] = canonical_key

        by_id[oid] = view
        merged.append(view)

    return merged


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
    """Drain fresh continuation work before retry-only stop gates may fire.

    An identity leaves the queue only after the executor returns a terminal
    result for it. Budget-deferred and unreceipted selected identities remain
    pending. Retry-eligible failures are kept in a separate backlog and cannot
    monopolize planning while never-attempted candidates still exist. Initial
    batch outcomes come from the executor receipt ledger, so caller exclusion
    hints are honored only for identities that actually produced a non-retry
    terminal receipt.
    """
    from .adaptive_discovery_planner import build_agent_intent_plan, plan_obligation_round
    from .experiment_executor import consume_continuation_execution_receipts
    from .pipeline_slices import _ABS_MAX_SLICE_BUDGET

    continuation_obligations = _continuation_obligation_universe(
        obligations=[dict(row) for row in _list(obligations) if isinstance(row, dict)],
        experiments_by_obligation=dict(_dict(experiments_by_obligation)),
        obligation_plan=dict(_dict(obligation_plan)),
    )
    plan_row = dict(_dict(obligation_plan))
    budget = int(plan_row.get("budget") or 0)
    round_limit = max(1, int(automatic_round_limit or 1))
    requested_excluded = set(exclude_obligation_ids or set())
    experiments = {
        _text(key): dict(value)
        for key, value in _dict(experiments_by_obligation).items()
        if _text(key) and isinstance(value, dict)
    }
    retry_eligible_reasons = {
        "BLOCKED_MISSING_BINDING",
        "HARNESS_FAILED",
        "BLOCKED_MISSING_OBSERVER",
        "BLOCKED_CONTROL_ARM_NOT_PROVEN",
        "BLOCKED_OBSERVER_RECEIPT_INDETERMINATE",
        "UNRECEIPTED_SELECTED",
    }

    # Exact experiment ids keep primary/expansion domains separate even when a
    # recompile reused the same obligation id. The public executor records every
    # initially selected identity as TERMINAL_RESULT, BUDGET_DEFERRED, or
    # UNRECEIPTED_SELECTED before continuation capture closes.
    allowed_experiment_ids = {
        oid: _text(exp.get("experiment_id"))
        for oid, exp in experiments.items()
    }
    captured_initial_rows = consume_continuation_execution_receipts(
        campaign_id,
        allowed_experiment_ids_by_obligation=allowed_experiment_ids,
        close_capture=True,
    )
    captured_terminal_done_ids: set[str] = set()
    initial_requeue_ids: list[str] = []
    captured_retry_rows: list[dict[str, Any]] = []
    for raw in captured_initial_rows:
        oid = _text(raw.get("obligation_id"))
        status = _text(raw.get("status")).upper()
        reason = _text(raw.get("reason_code"))
        receipt_kind = _text(raw.get("receipt_kind")).upper()
        if not oid:
            continue
        if (
            status in {"BLOCKED", "HARNESS_FAILED"}
            and reason in retry_eligible_reasons
        ):
            captured_retry_rows.append(dict(raw))
        elif receipt_kind == "UNRECEIPTED_SELECTED" or status == "UNRECEIPTED":
            initial_requeue_ids.append(oid)
            captured_retry_rows.append({
                **dict(raw),
                "obligation_id": oid,
                "reason_code": "UNRECEIPTED_SELECTED",
            })
        elif receipt_kind == "BUDGET_DEFERRED" or status == "DEFERRED":
            initial_requeue_ids.append(oid)
        elif receipt_kind == "TERMINAL_RESULT":
            captured_terminal_done_ids.add(oid)

    # The caller currently supplies scheduled-minus-budget-deferred ids. Treat
    # that set only as an exclusion request; evidence decides which ids really
    # reached a terminal outcome. Missing receipt is continuation work, not done.
    excluded = {
        oid for oid in requested_excluded if oid in captured_terminal_done_ids
    }

    pending_rows, continuation_receipt = complete_pending_continuation_rows(
        obligation_plan=plan_row,
        obligations=continuation_obligations,
        experiments_by_obligation=experiments,
        exclude_obligation_ids=excluded,
    )
    obligation_by_id = {
        _text(row.get("obligation_id")): dict(row)
        for row in continuation_obligations
        if isinstance(row, dict) and _text(row.get("obligation_id"))
    }
    existing_pending_ids = {
        _text(row.get("obligation_id"))
        for row in pending_rows
        if _text(row.get("obligation_id"))
    }
    requeue_ids = [
        oid
        for oid in dict.fromkeys(initial_requeue_ids)
        if oid in experiments
        and oid not in excluded
        and oid not in existing_pending_ids
    ]
    if requeue_ids:
        pending_rows.extend(
            _rows_for_ids(
                requeue_ids,
                prior_rows=[],
                plan_rows=[
                    dict(row)
                    for row in [
                        *_list(plan_row.get("selected")),
                        *_list(plan_row.get("pending_next_round")),
                    ]
                    if isinstance(row, dict)
                ],
                obligations_by_id=obligation_by_id,
            )
        )
    continuation_receipt.update({
        "captured_initial_outcome_count": len(captured_initial_rows),
        "captured_initial_retry_count": len(captured_retry_rows),
        "initial_unreceipted_or_deferred_requeued_count": len(requeue_ids),
        "requested_excluded_count": len(requested_excluded),
        "terminal_receipt_excluded_count": len(excluded),
        "unproven_exclusion_rejected_count": len(requested_excluded - excluded),
        "continuation_count": len(pending_rows),
    })
    plan_row["pending_continuation_authority_receipt"] = continuation_receipt

    # Seed retry authority from persisted/in-process plan rows plus actual
    # pre-continuation retry receipts. Preserve the concrete reason code across
    # round-limit boundaries so a later consumer can bootstrap it again.
    retry_reason_by_id: dict[str, str] = {}
    retry_backlog_ids: list[str] = []
    for raw in _list(plan_row.get("blocked_retry_pool")):
        if not isinstance(raw, dict):
            continue
        oid = _text(raw.get("obligation_id"))
        reason = _text(raw.get("block_reason") or raw.get("reason_code"))
        if oid and oid not in excluded and reason in retry_eligible_reasons:
            retry_reason_by_id[oid] = reason
            retry_backlog_ids.append(oid)
    for raw in captured_retry_rows:
        oid = _text(raw.get("obligation_id"))
        reason = _text(raw.get("reason_code"))
        if oid and oid not in excluded and reason in retry_eligible_reasons:
            retry_reason_by_id[oid] = reason
            retry_backlog_ids.append(oid)
    retry_backlog_ids = list(dict.fromkeys(retry_backlog_ids))
    plan_row["blocked_retry_pool"] = [
        {"obligation_id": oid, "block_reason": retry_reason_by_id[oid]}
        for oid in retry_backlog_ids[:100]
        if oid in retry_reason_by_id
    ]

    if budget <= 0:
        if pending_rows or retry_backlog_ids:
            plan_row["early_stop_reason"] = "PENDING_NEXT_ROUND_SKIPPED_PLAN_BUDGET_ZERO"
            plan_row["pending_count"] = len(pending_rows)
        return [], plan_row
    if not pending_rows and not retry_backlog_ids:
        return [], plan_row
    if round_limit <= 1:
        plan_row["early_stop_reason"] = "PENDING_NEXT_ROUND_SKIPPED_ROUND_LIMIT_ONE"
        plan_row["follow_on_round_limit"] = round_limit
        plan_row["pending_count"] = len(pending_rows)
        plan_row["pending_next_round"] = pending_rows[:_ABS_MAX_SLICE_BUDGET]
        return [], plan_row

    follow_on_batches: list[dict[str, Any]] = []
    follow_on_receipts: list[dict[str, Any]] = []
    accumulated_bindings: dict[str, str] = {}
    no_progress_limit = 3
    same_plan_limit = 2
    same_error_limit = 3
    no_progress_streak = 0
    previous_plan_fingerprint = ""
    same_plan_streak = 0
    previous_dominant_error = ""
    same_error_streak = 0
    early_stop_reason = ""
    loop_exhausted_with_pending = False

    for planning_round in range(2, round_limit + 1):
        pending_ids = [
            _text(row.get("obligation_id"))
            for row in pending_rows
            if _text(row.get("obligation_id")) and _text(row.get("obligation_id")) not in excluded
        ]
        queue_ids = list(dict.fromkeys([
            *pending_ids,
            *[
                oid for oid in retry_backlog_ids
                if oid and oid not in excluded
            ],
        ]))
        retry_set = set(retry_backlog_ids)
        fresh_ids = [oid for oid in queue_ids if oid not in retry_set]
        round_ids = fresh_ids if fresh_ids else [oid for oid in queue_ids if oid in retry_set]
        round_mode = "fresh" if fresh_ids else "retry"
        if not round_ids:
            pending_rows = []
            break

        remaining_obligations = [
            obligation_by_id[oid] for oid in round_ids if oid in obligation_by_id
        ]
        remaining_experiments = {
            oid: experiments[oid]
            for oid in round_ids
            if oid in experiments
            and _compile_status(experiments[oid]) in {
                "COMPILED", "BLOCKED", "BLOCKED_MISSING_BINDING", "HARNESS_FAILED"
            }
        }
        if not remaining_experiments:
            pending_rows = _rows_for_ids(
                queue_ids,
                prior_rows=pending_rows,
                plan_rows=[],
                obligations_by_id=obligation_by_id,
            )
            early_stop_reason = "NO_CONTINUATION_EXPERIMENTS"
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
            dict(row) for row in _list(next_intents.get("intents")) if isinstance(row, dict)
        ]
        scheduled_ids = [
            _text(row.get("obligation_id"))
            for row in next_scheduled
            if _text(row.get("obligation_id"))
        ]
        if not next_scheduled:
            pending_rows = _rows_for_ids(
                queue_ids,
                prior_rows=pending_rows,
                plan_rows=[
                    dict(row)
                    for row in _list(next_plan.get("pending_next_round"))
                    if isinstance(row, dict)
                ],
                obligations_by_id=obligation_by_id,
            )
            early_stop_reason = "NO_SCHEDULED_EXPERIMENTS"
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
        follow_on_batches.append(dict(_dict(next_batch)))
        batch_bindings = _dict(_dict(next_batch).get("runtime_bindings"))
        if batch_bindings:
            accumulated_bindings.update(batch_bindings)

        deferred_ids = {
            _text(row.get("obligation_id"))
            for row in _list(_dict(next_batch).get("budget_deferred"))
            if isinstance(row, dict) and _text(row.get("obligation_id"))
        }
        result_ids: set[str] = set()
        next_retry_ids: list[str] = []
        terminal_done_ids: set[str] = set()
        error_counts: dict[str, int] = {}
        for raw in _list(_dict(next_batch).get("results")):
            if not isinstance(raw, dict):
                continue
            oid = _text(raw.get("obligation_id"))
            if oid:
                result_ids.add(oid)
            status = _text(raw.get("status") or raw.get("execution_status")).upper()
            reason = _text(
                raw.get("reason_code") or raw.get("block_reason") or raw.get("failure_reason")
            )
            retry_eligible = (
                oid
                and status in {"BLOCKED", "HARNESS_FAILED"}
                and reason in retry_eligible_reasons
            )
            if retry_eligible:
                retry_reason_by_id[oid] = reason
                next_retry_ids.append(oid)
            elif oid and oid not in deferred_ids:
                terminal_done_ids.add(oid)
                retry_reason_by_id.pop(oid, None)
            if status in {"BLOCKED", "HARNESS_FAILED", "FAILED"} and reason:
                error_counts[reason] = error_counts.get(reason, 0) + 1

        # No receipt is not completion, but it is an attempted scheduling slot.
        # Demote it behind never-attempted work so one broken executor identity
        # cannot repeatedly monopolize the fresh queue and starve the tail.
        unreceipted_ids = {
            oid for oid in scheduled_ids
            if oid and oid not in result_ids and oid not in deferred_ids
        }
        for oid in unreceipted_ids:
            retry_reason_by_id[oid] = "UNRECEIPTED_SELECTED"
            next_retry_ids.append(oid)

        # A selected identity is not complete merely because it was scheduled.
        # Only a returned terminal result may remove it. Missing receipts and
        # executor budget deferrals stay in the queue, while retry-eligible
        # failures move to a retry backlog that cannot outrank fresh work.
        retry_backlog_set = set(retry_backlog_ids)
        retry_backlog_set.difference_update(terminal_done_ids)
        retry_backlog_set.update(next_retry_ids)
        retry_backlog_ids = [
            oid for oid in queue_ids if oid in retry_backlog_set
        ] + [
            oid for oid in next_retry_ids if oid not in queue_ids
        ]
        retry_backlog_ids = list(dict.fromkeys(retry_backlog_ids))

        next_queue_ids = [
            oid for oid in queue_ids if oid not in terminal_done_ids
        ]
        for oid in [*scheduled_ids, *deferred_ids, *retry_backlog_ids]:
            if oid and oid not in terminal_done_ids and oid not in next_queue_ids:
                next_queue_ids.append(oid)
        pending_rows = _rows_for_ids(
            next_queue_ids,
            prior_rows=pending_rows,
            plan_rows=[
                *[
                    dict(row)
                    for row in _list(next_plan.get("pending_next_round"))
                    if isinstance(row, dict)
                ],
                *[
                    dict(row)
                    for row in _list(_dict(next_batch).get("budget_deferred"))
                    if isinstance(row, dict)
                ],
            ],
            obligations_by_id=obligation_by_id,
        )

        round_executed = int(_dict(next_batch).get("executed_count") or 0)
        retry_set_after = set(retry_backlog_ids)
        fresh_remaining = [oid for oid in next_queue_ids if oid not in retry_set_after]
        follow_on_receipts.append({
            "planning_round": planning_round,
            "selected_count": int(next_plan.get("selected_count") or 0),
            "pending_count": len(pending_rows),
            "fresh_pending_count": len(fresh_remaining),
            "retry_pending_count": len(retry_backlog_ids),
            "unreceipted_selected_count": len(unreceipted_ids),
            "executed_count": round_executed,
            "budget": budget,
            "round_mode": round_mode,
            "accumulated_bindings_count": len(accumulated_bindings),
            "continuation_authority": "fresh_before_retry_terminal_receipt_required",
        })
        _LOGGER.info(
            "follow-on round %d: mode=%s selected=%s fresh_pending=%s retry_pending=%s executed=%s budget=%s",
            planning_round,
            round_mode,
            next_plan.get("selected_count"),
            len(fresh_remaining),
            len(retry_backlog_ids),
            round_executed,
            budget,
        )

        # No-progress is a retry-loop guard, not a Recall gate. A fresh round
        # earns unseen identities a scheduling opportunity even if the executor
        # returns no terminal receipt; those identities are demoted to retry so
        # the next fresh tail can advance.
        round_processed = len(result_ids)
        if round_mode == "fresh" or fresh_remaining:
            no_progress_streak = 0
        else:
            no_progress_streak = no_progress_streak + 1 if round_processed == 0 else 0
            if no_progress_streak >= no_progress_limit:
                early_stop_reason = f"NO_PROGRESS_{no_progress_limit}_CONSECUTIVE_ROUNDS"
                break

        # Repeat-plan/error guards are retry-loop guards, not Recall gates.
        # Different fresh identities may legitimately fail with the same reason;
        # stopping on that pattern would permanently starve unseen candidates.
        if fresh_remaining or round_mode == "fresh":
            previous_plan_fingerprint = ""
            same_plan_streak = 0
            previous_dominant_error = ""
            same_error_streak = 0
        else:
            plan_fingerprint = hashlib.sha256(
                json.dumps(scheduled_ids, sort_keys=True, default=str).encode()
            ).hexdigest()[:16]
            same_plan_streak = (
                same_plan_streak + 1
                if plan_fingerprint == previous_plan_fingerprint
                else 0
            )
            previous_plan_fingerprint = plan_fingerprint
            if same_plan_streak >= same_plan_limit:
                early_stop_reason = f"SAME_PLAN_{same_plan_limit}_CONSECUTIVE_ROUNDS"
                break

            dominant_error = max(error_counts, key=error_counts.get) if error_counts else ""
            still_draining_budget = bool(deferred_ids)
            same_error_streak = (
                same_error_streak + 1
                if not still_draining_budget
                and dominant_error
                and dominant_error == previous_dominant_error
                else 0
            )
            previous_dominant_error = dominant_error
            if same_error_streak >= same_error_limit:
                early_stop_reason = (
                    f"SAME_ERROR_{same_error_limit}_CONSECUTIVE:{dominant_error}"
                )
                break

        if not pending_rows:
            early_stop_reason = "PENDING_QUEUE_EMPTY"
            break
    else:
        loop_exhausted_with_pending = bool(pending_rows)

    plan_row.update({
        "pending_next_round": pending_rows[:_ABS_MAX_SLICE_BUDGET],
        "pending_count": len(pending_rows),
        "follow_on_round_receipts": follow_on_receipts,
        "blocked_retry_pool": [
            {"obligation_id": oid, "block_reason": retry_reason_by_id[oid]}
            for oid in retry_backlog_ids[:100]
            if oid in retry_reason_by_id
        ],
        "accumulated_bindings": accumulated_bindings,
    })
    if early_stop_reason:
        plan_row["early_stop_reason"] = early_stop_reason
        plan_row["stop_condition"] = early_stop_reason
    elif loop_exhausted_with_pending:
        plan_row["stop_condition"] = "round_limit_reached"
        plan_row["round_limit_reached"] = True
        plan_row["follow_on_round_limit"] = round_limit
    elif pending_rows:
        plan_row["stop_condition"] = "round_limit_reached"
        plan_row["round_limit_reached"] = True
        plan_row["follow_on_round_limit"] = round_limit
    return follow_on_batches, plan_row


def _finalize_campaign(handle: Any, ledger: dict[str, Any]) -> dict[str, Any]:
    """Finalize through the historical authority and release retry capture."""
    campaign_id = ""
    try:
        campaign_id = _text(getattr(_base._campaign_object(handle), "campaign_id", ""))
    except Exception:
        campaign_id = ""
    try:
        return _base._finalize_campaign(handle, ledger)
    finally:
        if campaign_id:
            from .experiment_executor import clear_continuation_retry_receipts

            clear_continuation_retry_receipts(campaign_id)
