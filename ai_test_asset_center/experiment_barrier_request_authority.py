"""Barrier-path request first-loss authority.

Sequential execution already seals an explicit zero-transport governance result
into ``pre_transport_block_reasons`` through
``experiment_plan_lifecycle_adapter._seal_pre_transport_request_blocks``.
Barrier participants historically bypassed that adapter: a governed write could
return ``write_request_attempt_count=0`` with a precise reason, its contract
receipt would be BLOCKED, but the barrier result carried no
``pre_transport_reason``. When the block happened before a before-GET, the
Finalizer had no other evidence and fell back to ``HARNESS_REQUEST_BUILD_FAILED``.

This module reuses the exact sequential first-loss authority on the barrier
result. It adds no concurrency, transport, classification, or business logic.
"""
from __future__ import annotations

import sys
from typing import Any


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def seal_barrier_request_first_loss(
    result: dict[str, Any],
    *,
    observations: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply the canonical sequential zero-transport seal to a barrier result."""

    # Lazy import avoids introducing an import cycle between the barrier module
    # and the graph/sequential lifecycle adapter during package initialization.
    from .experiment_plan_lifecycle_adapter import (
        _seal_pre_transport_request_blocks,
    )

    sealed, receipt = _seal_pre_transport_request_blocks(_dict(result))
    if int(_dict(receipt).get("row_count") or 0) > 0 and isinstance(observations, dict):
        observations["barrier_request_build_first_loss_receipt"] = dict(receipt)
    if int(_dict(receipt).get("row_count") or 0) > 0:
        sealed["barrier_request_build_first_loss_receipt"] = dict(receipt)
    return sealed


def execute_barrier_plans(**kwargs: Any) -> dict[str, Any]:
    """Delegate unchanged concurrency, then seal explicit zero-transport blocks."""

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
