"""Public execution wrapper for formal evidence and loss-funnel projection."""
from __future__ import annotations

from typing import Any

from .discovery_loss_funnel import build_discovery_loss_funnel
from .formal_evidence_projection import (
    run_experiment_candidate as _run_with_formal_evidence,
)


def project_discovery_quality(result: dict[str, Any]) -> dict[str, Any]:
    """Attach receipt-backed conversion measurement to one discovery result."""

    if not isinstance(result, dict):
        raise TypeError("discovery_result_not_object")
    projected = dict(result)
    projected["discovery_loss_funnel"] = build_discovery_loss_funnel(projected)
    return projected


def run_experiment_candidate(inputs: Any, campaign_handle: Any, plan: Any) -> dict[str, Any]:
    """Execute, project formal evidence, then build the honest loss funnel."""

    result = _run_with_formal_evidence(inputs, campaign_handle, plan)
    return project_discovery_quality(result)


__all__ = [
    "project_discovery_quality",
    "run_experiment_candidate",
]
