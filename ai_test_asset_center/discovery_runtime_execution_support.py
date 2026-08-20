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
    """Seal exact first fresh membership, run continuation, then bound preview."""
    seeded_plan = seed_initial_fresh_pending_authority(
        obligation_plan=obligation_plan,
        obligations=obligations,
        experiments_by_obligation=experiments_by_obligation,
        behavior_ir=behavior_ir,
    )
    batches, final_plan = _consume_exact_pending_obligation_rounds(
        obligation_plan=seeded_plan,
        obligations=obligations,
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


def _text(value: Any) -> str:
    return str(value or "").strip()


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
