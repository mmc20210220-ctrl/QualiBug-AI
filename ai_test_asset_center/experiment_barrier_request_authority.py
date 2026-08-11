"""Barrier-path request first-loss authority.

Sequential execution seals explicit zero-transport governance results into
``pre_transport_block_reasons``. Barrier participants historically bypassed
that adapter: a governed write could return ``write_request_attempt_count=0``
with a precise request/governance reason, its contract receipt would be BLOCKED,
but the barrier result carried no ``pre_transport_reason``. When the block
happened before a before-GET, the Finalizer fell back to
``HARNESS_REQUEST_BUILD_FAILED``.

Barrier synchronization failures are different: a broken release/wait is a real
harness runtime failure. This authority therefore consumes ONLY an explicit
zero-write ``governance_receipt``; it never promotes generic barrier
``blocked_write`` rows into request-build blockers.
"""
from __future__ import annotations

import sys
from typing import Any


_REQUEST_FIRST_LOSS_SCHEMA = "qualibug.request-build-first-loss.v1"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def seal_barrier_request_first_loss(
    result: dict[str, Any],
    *,
    observations: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Seal only explicit barrier governance refusals that never sent the write."""

    from .experiment_plan_lifecycle_adapter import _request_first_loss_category

    governed = dict(_dict(result))
    reasons = [
        _text(value)
        for value in list(governed.get("pre_transport_block_reasons") or [])
        if _text(value)
    ]
    rows: list[dict[str, Any]] = []

    for raw in list(governed.get("steps") or []):
        if not isinstance(raw, dict):
            continue
        step = raw
        governance = _dict(step.get("governance_receipt"))
        if not governance:
            # BARRIER_RELEASE_FAILED/BARRIER_WRITE_REQUIRED and other barrier
            # runtime/plan rows do not pass through the request first-loss gate.
            continue
        write = _dict(governance.get("write"))
        attempts = _int(governance.get("write_request_attempt_count"))
        write_status = _int(write.get("status"))
        reason = _text(
            governance.get("reason")
            or write.get("error")
            or step.get("reason")
            or step.get("detail")
        )
        if attempts != 0 or write_status != 0 or not reason:
            continue
        lower = reason.lower()
        if any(
            marker in lower
            for marker in ("connection", "timeout", "network", "transport_error")
        ):
            # A transport attempt whose response was lost remains harness-scoped.
            continue
        if reason not in reasons:
            reasons.append(reason)
        rows.append(
            {
                "step_id": _text(step.get("step_id") or step.get("subject_id")),
                "phase": _text(step.get("phase")),
                "operation_ref": _text(step.get("operation_ref")),
                "actor_ref": _text(step.get("actor_ref")),
                "method": _text(step.get("method")).upper(),
                "path": _text(step.get("path")),
                "reason_code": reason,
                "category": _request_first_loss_category(reason),
                "write_request_attempt_count": attempts,
                "request_reached_transport": False,
                "barrier_scope": True,
            }
        )

    governed["pre_transport_block_reasons"] = list(dict.fromkeys(reasons))
    if not rows:
        return governed

    category_counts: dict[str, int] = {}
    for row in rows:
        category = _text(row.get("category")) or "PRE_TRANSPORT_REQUEST_BUILD"
        category_counts[category] = category_counts.get(category, 0) + 1
    receipt = {
        "schema_version": _REQUEST_FIRST_LOSS_SCHEMA,
        "status": "BLOCKED",
        "row_count": len(rows),
        "rows": rows,
        "by_category": category_counts,
        "transport_attempted": False,
        "harness_failure_claimed": False,
        "barrier_scope": True,
    }
    governed["barrier_request_build_first_loss_receipt"] = receipt
    if isinstance(observations, dict):
        observations["barrier_request_build_first_loss_receipt"] = dict(receipt)
    return governed


def execute_barrier_plans(**kwargs: Any) -> dict[str, Any]:
    """Delegate unchanged concurrency, then seal zero-write governance blocks."""

    from .experiment_barrier_executor import (
        execute_barrier_plans as _execute_barrier_plans,
    )

    result = _execute_barrier_plans(**kwargs)
    observations = kwargs.get("observations")
    return seal_barrier_request_first_loss(
        _dict(result),
        observations=observations if isinstance(observations, dict) else None,
    )


def install_barrier_request_first_loss_authority() -> None:
    """Install on the formal executor hook surfaces, preserving one delegate."""

    package = __package__ or "ai_test_asset_center"
    for module_name in (
        f"{package}.experiment_executor_core",
        f"{package}._experiment_executor_governance_authority_mechanics",
        f"{package}.experiment_executor_governance",
    ):
        module = sys.modules.get(module_name)
        if module is not None:
            setattr(module, "execute_barrier_plans", execute_barrier_plans)


__all__ = [
    "execute_barrier_plans",
    "install_barrier_request_first_loss_authority",
    "seal_barrier_request_first_loss",
]
