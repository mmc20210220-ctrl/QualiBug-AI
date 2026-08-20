"""Compatibility facade for discovery runtime execution support.

Historical helpers remain re-exported from
``discovery_runtime_execution_support_base``. Multi-round continuation lives in
``discovery_continuation_authority``; this facade seals the first exact fresh
identity set before execution and projects the final bounded preview only from
exact resume authorities.
"""
from __future__ import annotations

from typing import Any

from . import discovery_runtime_execution_support_base as _base

# Preserve the complete historical module surface, including private helpers
# imported by discovery_runtime_execution and compatibility tests.
for _name in dir(_base):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_base, _name))

from .continuation_preview_authority import (  # noqa: E402
    synchronize_continuation_preview,
)
from .discovery_continuation_authority import (  # noqa: E402,F401
    _consume_pending_obligation_rounds as _consume_exact_pending_obligation_rounds,
    _continuation_obligation_universe,
)
from .initial_fresh_pending_authority import (  # noqa: E402
    seed_initial_fresh_pending_authority,
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _prepare_continuation_attempt(
    obligation_plan: dict[str, Any],
) -> dict[str, Any]:
    """Clear prior-attempt stop receipts without touching resume authority."""
    plan = dict(obligation_plan) if isinstance(obligation_plan, dict) else {}
    previous_stop = _text(plan.get("stop_condition"))
    continuation_stop = (
        previous_stop == "round_limit_reached"
        or previous_stop == "PENDING_QUEUE_EMPTY"
        or previous_stop.startswith("PENDING_NEXT_ROUND_SKIPPED_")
        or previous_stop.startswith("NO_PROGRESS_")
        or previous_stop.startswith("SAME_PLAN_")
        or previous_stop.startswith("SAME_ERROR_")
        or previous_stop in {
            "NO_CONTINUATION_EXPERIMENTS",
            "NO_SCHEDULED_EXPERIMENTS",
        }
    )
    for key in (
        "early_stop_reason",
        "round_limit_reached",
        "follow_on_round_limit",
    ):
        plan.pop(key, None)
    if continuation_stop:
        plan.pop("stop_condition", None)
    # Receipts describe this continuation attempt, not the lifetime history.
    # Exact pools remain the cross-attempt state authority.
    plan["follow_on_round_receipts"] = []
    return plan


def _experiment_compile_status(experiment: Any) -> str:
    if not isinstance(experiment, dict):
        return ""
    receipt = experiment.get("compile_receipt")
    receipt = receipt if isinstance(receipt, dict) else {}
    return _text(receipt.get("status") or experiment.get("compile_status")).upper()


def _planner_safe_continuation_obligations(
    *,
    obligation_plan: dict[str, Any],
    obligations: list[dict[str, Any]],
    experiments_by_obligation: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Prevent stale obligation compile markers from selecting missing deps.

    ``plan_obligation_round`` intentionally supports an obligation-level
    ``compile_status`` fallback for compatibility when no experiment map is
    supplied. During exact continuation we *do* have a current experiment map;
    if an exact outstanding identity has no currently usable experiment, that
    fallback would let it consume a planner slot with an empty experiment_id and
    then fail the intent gate. Keep the identity in its exact pool, but suppress
    only that stale fallback in the ephemeral planner view so other fresh work
    can proceed. Persisted/source obligations are never mutated.
    """
    if "fresh_pending_pool" not in obligation_plan:
        return [dict(row) for row in obligations if isinstance(row, dict)]

    exact_ids: set[str] = set()
    for key in (
        "fresh_pending_pool",
        "budget_deferred_pool",
        "blocked_retry_pool",
    ):
        for raw in obligation_plan.get(key) or []:
            if not isinstance(raw, dict):
                continue
            oid = _text(raw.get("obligation_id"))
            if oid:
                exact_ids.add(oid)

    usable_statuses = {
        "COMPILED",
        "BLOCKED",
        "BLOCKED_MISSING_BINDING",
        "HARNESS_FAILED",
    }
    result: list[dict[str, Any]] = []
    for raw in obligations:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        oid = _text(row.get("obligation_id"))
        if oid in exact_ids:
            status = _experiment_compile_status(
                experiments_by_obligation.get(oid)
            )
            if status not in usable_statuses:
                row.pop("compile_status", None)
        result.append(row)
    return result


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
    """Reset attempt-local stop state, seal exact fresh membership, and run."""
    prepared_plan = _prepare_continuation_attempt(obligation_plan)
    seeded_plan = seed_initial_fresh_pending_authority(
        obligation_plan=prepared_plan,
        obligations=obligations,
        experiments_by_obligation=experiments_by_obligation,
        behavior_ir=behavior_ir,
    )
    engine_obligations = _planner_safe_continuation_obligations(
        obligation_plan=seeded_plan,
        obligations=obligations,
        experiments_by_obligation=experiments_by_obligation,
    )
    batches, final_plan = _consume_exact_pending_obligation_rounds(
        obligation_plan=seeded_plan,
        obligations=engine_obligations,
        experiments_by_obligation=experiments_by_obligation,
        behavior_ir=behavior_ir,
        root=root,
        project=project,
        base_url=base_url,
        runtime_contract=runtime_contract,
        mainline_run=mainline_run,
        campaign_id=campaign_id,
        automatic_round_limit=automatic_round_limit,
        execute_batch=execute_batch,
        exclude_obligation_ids=exclude_obligation_ids,
    )
    return batches, synchronize_continuation_preview(final_plan)


def _finalize_campaign(handle: Any, ledger: dict[str, Any]) -> dict[str, Any]:
    """Finalize through historical authority and release continuation capture."""
    campaign_id = ""
    try:
        campaign_id = _text(
            getattr(_base._campaign_object(handle), "campaign_id", "")
        )
    except Exception:
        campaign_id = ""
    try:
        return _base._finalize_campaign(handle, ledger)
    finally:
        if campaign_id:
            from .experiment_executor import clear_continuation_retry_receipts

            clear_continuation_retry_receipts(campaign_id)
