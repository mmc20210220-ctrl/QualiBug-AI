"""Lossless in-memory continuation authority for discovery planning rounds.

The public ``pending_next_round`` list is intentionally bounded for product
artifacts.  It must not, however, be the scheduling authority: clipping that
list used to delete compiled, not-yet-processed obligations from all later
rounds.  This module keeps the per-round safety budget unchanged while
reconstructing and carrying the complete in-memory continuation identity set.

Generic only: no benchmark ids, customer names, paths, or ground truth.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _compile_status(experiment: Any) -> str:
    row = _dict(experiment)
    status = _text(_dict(row.get("compile_receipt")).get("status")).upper()
    if not status:
        status = _text(row.get("compile_status")).upper()
    return status


def _continuation_eligible(
    obligation: dict[str, Any], experiment: dict[str, Any]
) -> bool:
    return (
        _compile_status(experiment) == "COMPILED"
        and obligation.get("pre_transport_executable") is not False
    )


def _representative(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    return sorted(
        rows,
        key=lambda row: (
            -float(row.get("confidence") or 0.0),
            _text(row.get("obligation_id")),
        ),
    )[0]


def complete_pending_continuation_rows(
    *,
    obligation_plan: dict[str, Any],
    obligations: list[dict[str, Any]],
    experiments_by_obligation: dict[str, dict[str, Any]],
    exclude_obligation_ids: set[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Restore only the planner-declared portion omitted by the public cap.

    ``pending_count`` is the generic continuation authority's declared size;
    the bounded public preview may omit some of those identities, but generic
    reconstruction must not widen that declaration to every other executable
    compiled candidate. Exact retry/deferred authorities are handled by their
    own pools in the execution-support layer.
    """
    plan = _dict(obligation_plan)
    visible = [
        dict(row)
        for row in _list(plan.get("pending_next_round"))
        if isinstance(row, dict) and _text(row.get("obligation_id"))
    ]
    declared_pending = int(plan.get("pending_count") or len(visible))
    declared_truncated = int(plan.get("pending_truncated") or 0)
    declared_restore_budget = max(0, declared_pending - len(visible))
    needs_rebuild = declared_truncated > 0 or declared_restore_budget > 0
    receipt = {
        "schema_version": "qualibug.pending-continuation-authority.v1",
        "status": "PASS" if not needs_rebuild else "REBUILT",
        "visible_pending_count": len(visible),
        "declared_pending_count": declared_pending,
        "declared_truncated_count": declared_truncated,
        "declared_restore_budget": declared_restore_budget,
        "eligible_omitted_count": 0,
        "restore_overflow_count": 0,
        "restored_count": 0,
        "authority": "compiled_unprocessed_identity",
    }
    if not needs_rebuild or declared_restore_budget <= 0:
        receipt["continuation_count"] = len(visible)
        return visible, receipt

    excluded = set(exclude_obligation_ids or set())
    experiments = {
        _text(key): dict(value)
        for key, value in _dict(experiments_by_obligation).items()
        if _text(key) and isinstance(value, dict)
    }
    obligations_by_id = {
        _text(row.get("obligation_id")): dict(row)
        for row in obligations
        if isinstance(row, dict) and _text(row.get("obligation_id"))
    }
    selected_rows = [
        dict(row)
        for row in _list(plan.get("selected"))
        if isinstance(row, dict)
    ]
    selected_ids = {
        _text(row.get("obligation_id"))
        for row in selected_rows
        if _text(row.get("obligation_id"))
    }
    visible_ids = {
        _text(row.get("obligation_id"))
        for row in visible
        if _text(row.get("obligation_id"))
    }
    eligible_restored: list[dict[str, Any]] = []
    planning_authority = _text(plan.get("plan_authority")).lower()

    if planning_authority == "coverage_unit":
        selected_units = {
            _text(row.get("coverage_unit_id"))
            for row in selected_rows
            if _text(row.get("coverage_unit_id"))
        }
        visible_units = {
            _text(row.get("coverage_unit_id"))
            for row in visible
            if _text(row.get("coverage_unit_id"))
        }
        grouped: dict[str, list[dict[str, Any]]] = {}
        unscoped: list[dict[str, Any]] = []
        for oid, obligation in obligations_by_id.items():
            if oid in excluded or oid in selected_ids or oid in visible_ids:
                continue
            experiment = experiments.get(oid) or {}
            if not _continuation_eligible(obligation, experiment):
                continue
            unit_id = _text(obligation.get("coverage_unit_id"))
            if unit_id:
                if unit_id in selected_units or unit_id in visible_units:
                    continue
                grouped.setdefault(unit_id, []).append(obligation)
            else:
                unscoped.append(obligation)
        for unit_id in sorted(grouped):
            representative = _representative(grouped[unit_id])
            if representative is None:
                continue
            eligible_restored.append({
                "obligation_id": _text(representative.get("obligation_id")),
                "coverage_unit_id": unit_id,
                "risk_family": _text(representative.get("risk_family")),
                "not_in_plan_reason": "CONTINUATION_VIEW_TRUNCATED",
                "continuation_origin": "reconstructed_coverage_unit",
            })
        for obligation in sorted(
            unscoped, key=lambda row: _text(row.get("obligation_id"))
        ):
            eligible_restored.append({
                "obligation_id": _text(obligation.get("obligation_id")),
                "risk_family": _text(obligation.get("risk_family")),
                "not_in_plan_reason": "CONTINUATION_VIEW_TRUNCATED",
                "continuation_origin": "reconstructed_unscoped_obligation",
            })
    else:
        for oid in sorted(obligations_by_id):
            if oid in excluded or oid in selected_ids or oid in visible_ids:
                continue
            obligation = obligations_by_id[oid]
            experiment = experiments.get(oid) or {}
            if not _continuation_eligible(obligation, experiment):
                continue
            eligible_restored.append({
                "obligation_id": oid,
                "risk_family": _text(obligation.get("risk_family")),
                "coverage_unit_id": _text(obligation.get("coverage_unit_id")),
                "not_in_plan_reason": "CONTINUATION_VIEW_TRUNCATED",
                "continuation_origin": "reconstructed_obligation",
            })

    restored = eligible_restored[:declared_restore_budget]
    result = [*visible, *restored]
    receipt.update({
        "eligible_omitted_count": len(eligible_restored),
        "restored_count": len(restored),
        "restore_overflow_count": max(
            0, len(eligible_restored) - len(restored)
        ),
        "continuation_count": len(result),
    })
    return result, receipt


def _rows_for_ids(
    ids: list[str],
    *,
    prior_rows: list[dict[str, Any]],
    plan_rows: list[dict[str, Any]],
    obligations_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    metadata: dict[str, dict[str, Any]] = {}
    for source in (prior_rows, plan_rows):
        for raw in source:
            if not isinstance(raw, dict):
                continue
            oid = _text(raw.get("obligation_id"))
            if oid:
                metadata[oid] = dict(raw)
    rows: list[dict[str, Any]] = []
    for oid in dict.fromkeys(ids):
        if not oid:
            continue
        row = dict(metadata.get(oid) or {})
        obligation = obligations_by_id.get(oid) or {}
        row.setdefault("obligation_id", oid)
        if _text(obligation.get("risk_family")):
            row.setdefault("risk_family", _text(obligation.get("risk_family")))
        if _text(obligation.get("coverage_unit_id")):
            row.setdefault("coverage_unit_id", _text(obligation.get("coverage_unit_id")))
        row.setdefault("not_in_plan_reason", "CONTINUATION_PENDING")
        rows.append(row)
    return rows


def consume_pending_obligation_rounds(
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
    """Consume the complete continuation identity set under the existing budget."""
    from .adaptive_discovery_planner import build_agent_intent_plan, plan_obligation_round
    from .pipeline_slices import _ABS_MAX_SLICE_BUDGET

    plan_row = dict(_dict(obligation_plan))
    budget = int(plan_row.get("budget") or 0)
    round_limit = max(1, int(automatic_round_limit or 1))
    pending_rows, continuation_receipt = complete_pending_continuation_rows(
        obligation_plan=plan_row,
        obligations=obligations,
        experiments_by_obligation=experiments_by_obligation,
        exclude_obligation_ids=exclude_obligation_ids,
    )
    plan_row["pending_continuation_authority_receipt"] = continuation_receipt
    if budget <= 0:
        if pending_rows:
            plan_row["early_stop_reason"] = "PENDING_NEXT_ROUND_SKIPPED_PLAN_BUDGET_ZERO"
            plan_row["pending_count"] = len(pending_rows)
        return [], plan_row
    if not pending_rows:
        return [], plan_row
    if round_limit <= 1:
        plan_row["early_stop_reason"] = "PENDING_NEXT_ROUND_SKIPPED_ROUND_LIMIT_ONE"
        plan_row["follow_on_round_limit"] = round_limit
        plan_row["pending_count"] = len(pending_rows)
        plan_row["pending_next_round"] = pending_rows[:_ABS_MAX_SLICE_BUDGET]
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
    accumulated_bindings: dict[str, str] = {}
    retry_eligible_reasons = {
        "BLOCKED_MISSING_BINDING",
        "HARNESS_FAILED",
        "BLOCKED_MISSING_OBSERVER",
        "BLOCKED_CONTROL_ARM_NOT_PROVEN",
        "BLOCKED_OBSERVER_RECEIPT_INDETERMINATE",
    }
    no_progress_limit = 3
    same_plan_limit = 2
    same_error_limit = 3
    no_progress_streak = 0
    previous_plan_fingerprint = ""
    same_plan_streak = 0
    previous_dominant_error = ""
    same_error_streak = 0
    early_stop_reason = ""
    excluded = set(exclude_obligation_ids or set())
    blocked_retry_ids: list[str] = []

    for planning_round in range(2, round_limit + 1):
        pending_ids = [
            _text(row.get("obligation_id"))
            for row in pending_rows
            if _text(row.get("obligation_id"))
        ]
        all_round_ids = [
            oid for oid in dict.fromkeys([*pending_ids, *blocked_retry_ids])
            if oid and oid not in excluded
        ]
        remaining_obligations = [
            obligation_by_id[oid] for oid in all_round_ids if oid in obligation_by_id
        ]
        remaining_experiments = {
            oid: experiments[oid]
            for oid in all_round_ids
            if oid in experiments
            and _compile_status(experiments[oid]) in {
                "COMPILED", "BLOCKED", "BLOCKED_MISSING_BINDING", "HARNESS_FAILED"
            }
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
            dict(row) for row in _list(next_intents.get("intents")) if isinstance(row, dict)
        ]
        scheduled_ids = [
            _text(row.get("obligation_id")) for row in next_scheduled
            if _text(row.get("obligation_id"))
        ]
        if not next_scheduled:
            pending_rows = _rows_for_ids(
                all_round_ids,
                prior_rows=pending_rows,
                plan_rows=[
                    dict(row) for row in _list(next_plan.get("pending_next_round"))
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
        retry_rows: list[dict[str, Any]] = []
        next_retry_ids: list[str] = []
        for raw in _list(_dict(next_batch).get("results")):
            if not isinstance(raw, dict):
                continue
            oid = _text(raw.get("obligation_id"))
            status = _text(raw.get("status") or raw.get("execution_status")).upper()
            reason = _text(
                raw.get("reason_code") or raw.get("block_reason") or raw.get("failure_reason")
            )
            if oid and status in {"BLOCKED", "HARNESS_FAILED"} and reason in retry_eligible_reasons:
                next_retry_ids.append(oid)
                retry_rows.append({
                    "obligation_id": oid,
                    "block_reason": reason,
                    "planning_round": planning_round,
                })
        blocked_retry_ids = list(dict.fromkeys(next_retry_ids))

        # Full in-memory queue authority: rows not scheduled remain; executor
        # budget-deferred rows remain; retry-eligible processed rows re-enter.
        scheduled_set = set(scheduled_ids)
        next_queue_ids = [oid for oid in all_round_ids if oid not in scheduled_set]
        next_queue_ids.extend(oid for oid in scheduled_ids if oid in deferred_ids)
        next_queue_ids.extend(blocked_retry_ids)
        next_queue_ids = list(dict.fromkeys(oid for oid in next_queue_ids if oid and oid not in excluded))
        pending_rows = _rows_for_ids(
            next_queue_ids,
            prior_rows=pending_rows,
            plan_rows=[
                *[
                    dict(row) for row in _list(next_plan.get("pending_next_round"))
                    if isinstance(row, dict)
                ],
                *[
                    dict(row) for row in _list(_dict(next_batch).get("budget_deferred"))
                    if isinstance(row, dict)
                ],
            ],
            obligations_by_id=obligation_by_id,
        )

        round_executed = int(_dict(next_batch).get("executed_count") or 0)
        follow_on_receipts.append({
            "planning_round": planning_round,
            "selected_count": int(next_plan.get("selected_count") or 0),
            "pending_count": len(pending_rows),
            "executed_count": round_executed,
            "budget": budget,
            "accumulated_bindings_count": len(accumulated_bindings),
            "blocked_retry_count": len(blocked_retry_ids),
            "continuation_authority": "full_in_memory_identity_set",
        })
        _LOGGER.info(
            "follow-on round %d: selected=%s pending=%s executed=%s budget=%s",
            planning_round,
            next_plan.get("selected_count"),
            len(pending_rows),
            round_executed,
            budget,
        )

        round_processed = len(_list(_dict(next_batch).get("results")))
        no_progress_streak = no_progress_streak + 1 if round_processed == 0 else 0
        if no_progress_streak >= no_progress_limit:
            early_stop_reason = f"NO_PROGRESS_{no_progress_limit}_CONSECUTIVE_ROUNDS"
            break

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

        error_counts: dict[str, int] = {}
        for raw in _list(_dict(next_batch).get("results")):
            row = _dict(raw)
            status = _text(row.get("status") or row.get("execution_status")).upper()
            if status not in {"BLOCKED", "HARNESS_FAILED", "FAILED"}:
                continue
            reason = _text(row.get("reason_code") or row.get("block_reason") or row.get("failure_reason"))
            if reason:
                error_counts[reason] = error_counts.get(reason, 0) + 1
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
            early_stop_reason = f"SAME_ERROR_{same_error_limit}_CONSECUTIVE:{dominant_error}"
            break
        if not pending_rows:
            early_stop_reason = "PENDING_QUEUE_EMPTY"
            break

    plan_row.update({
        "pending_next_round": pending_rows[:_ABS_MAX_SLICE_BUDGET],
        "pending_count": len(pending_rows),
        "follow_on_round_receipts": follow_on_receipts,
        "blocked_retry_pool": [
            {"obligation_id": oid, "block_reason": "RETRY_ELIGIBLE"}
            for oid in blocked_retry_ids[:100]
        ],
        "accumulated_bindings": accumulated_bindings,
    })
    if early_stop_reason:
        plan_row["early_stop_reason"] = early_stop_reason
        plan_row["stop_condition"] = early_stop_reason
    elif pending_rows:
        plan_row["stop_condition"] = _text(plan_row.get("stop_condition")) or "round_limit_reached"
    return follow_on_batches, plan_row


__all__ = [
    "complete_pending_continuation_rows",
    "consume_pending_obligation_rounds",
]