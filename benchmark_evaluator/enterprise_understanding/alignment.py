"""Stable evaluator alignment facade with governed operation-identity projection."""
from __future__ import annotations

from typing import Any

from .alignment_core import (
    ALIGNMENT_SCHEMA,
    COVERED_STATUSES,
    EXACT_STATUSES,
    align_enterprise_understanding as _align_enterprise_understanding,
)
from .behavior_operation_identity_projection import (
    project_governed_behavior_operation_identity,
)


def align_enterprise_understanding(
    ground_truth: dict[str, Any], asset: dict[str, Any]
) -> dict[str, Any]:
    """Align against an immutable evaluator-local projection of product authorities."""
    return _align_enterprise_understanding(
        ground_truth,
        project_governed_behavior_operation_identity(asset),
    )


__all__ = [
    "ALIGNMENT_SCHEMA",
    "EXACT_STATUSES",
    "COVERED_STATUSES",
    "align_enterprise_understanding",
]
