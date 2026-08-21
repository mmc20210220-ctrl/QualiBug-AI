"""Compatibility facade adding exact Recall coverage authority.

The current mainline implementation is preserved in
``behavior_ir_hypothesis_coverage_base``. This facade owns planning authority,
including the rule that source-backed coverage generation is not silently
truncated by a structural hypothesis budget.
"""
from __future__ import annotations

from typing import Any

from . import behavior_ir_hypothesis_coverage_base as _base
from .recall_coverage_authority import (
    attach_coverage_origin,
    compute_exact_obligation_coverage_gaps,
    harden_behavior_ir_coverage_map,
    obligation_match_dimensions,
)

# Preserve the historical module surface, including private helpers used by
# planning code. A normal ``import *`` would omit those names.
for _name in dir(_base):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_base, _name))


_ORIGINAL_BUILD_COVERAGE_MAP = _base.build_behavior_ir_coverage_map
_ORIGINAL_BUILD_COVERAGE_OBLIGATIONS = _base.build_source_backed_coverage_obligations
_ORIGINAL_BUILD_COVERAGE_HYPOTHESES = _base.build_source_backed_coverage_hypotheses


def build_behavior_ir_coverage_map(behavior_ir: dict[str, Any]) -> dict[str, Any]:
    """Return coverage whose authorization surfaces have source authority."""
    return harden_behavior_ir_coverage_map(
        _ORIGINAL_BUILD_COVERAGE_MAP(behavior_ir), behavior_ir
    )


def _obligation_covers_node(
    obligation: dict[str, Any], node: dict[str, Any]
) -> bool:
    """Family equality alone is never sufficient coverage authority."""
    return bool(obligation_match_dimensions(obligation, node))


def compute_obligation_coverage_gaps(
    behavior_ir: dict[str, Any],
    obligations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute exact structural coverage and retain per-node lineage."""
    return compute_exact_obligation_coverage_gaps(
        behavior_ir,
        obligations,
        build_coverage_map=build_behavior_ir_coverage_map,
        schema_version=COVERAGE_SCHEMA,
    )


def _coverage_budget_receipt(
    gaps: dict[str, Any],
    requested_limit: int | None,
) -> dict[str, Any]:
    uncovered_count = len(
        [row for row in gaps.get("uncovered_nodes", []) if isinstance(row, dict)]
    )
    if requested_limit is None:
        return {
            "mode": "UNBOUNDED_SOURCE_COVERAGE",
            "source_uncovered_count": uncovered_count,
            "effective_limit": uncovered_count,
            "budget_skipped_count": 0,
            "truncated": False,
            "reason_code": "NO_STRUCTURAL_COVERAGE_HYPOTHESIS_CAP",
        }

    effective_limit = max(1, int(requested_limit))
    return {
        "mode": "EXPLICIT_OPERATOR_COVERAGE_BUDGET",
        "source_uncovered_count": uncovered_count,
        "requested_limit": int(requested_limit),
        "effective_limit": effective_limit,
        "budget_skipped_count": max(0, uncovered_count - effective_limit),
        "truncated": uncovered_count > effective_limit,
        "reason_code": (
            "EXPLICIT_OPERATOR_COVERAGE_BUDGET_APPLIED"
            if uncovered_count > effective_limit
            else "EXPLICIT_OPERATOR_COVERAGE_BUDGET_NOT_REACHED"
        ),
    }


def _annotate_coverage_budget(
    generated: list[dict[str, Any]],
    gaps: dict[str, Any],
    receipt: dict[str, Any],
) -> list[dict[str, Any]]:
    # The gap report is the stable operator-visible carrier, including when the
    # generator emits zero rows. Generated rows also carry the same receipt so
    # downstream ledgers do not lose the budget decision during projection.
    if isinstance(gaps, dict):
        gaps["coverage_budget_receipt"] = dict(receipt)
    for row in generated:
        if isinstance(row, dict):
            row["_coverage_budget_receipt"] = dict(receipt)
    return generated


def build_source_backed_coverage_hypotheses(
    behavior_ir: dict[str, Any],
    gaps: dict[str, Any],
    *,
    max_hypotheses: int | None = None,
) -> list[dict[str, Any]]:
    """Generate source-backed hypotheses without a silent structural cap.

    ``max_hypotheses=None`` means every uncovered source node is eligible. A
    caller may still provide an explicit operator budget; when that budget
    truncates the source pool, the decision is written into the gap report and
    every emitted hypothesis.
    """
    receipt = _coverage_budget_receipt(gaps, max_hypotheses)
    generated = _ORIGINAL_BUILD_COVERAGE_HYPOTHESES(
        behavior_ir,
        gaps,
        max_hypotheses=receipt["effective_limit"],
    )
    return _annotate_coverage_budget(generated, gaps, receipt)


def build_source_backed_coverage_obligations(
    behavior_ir: dict[str, Any],
    gaps: dict[str, Any],
    *,
    max_obligations: int | None = None,
) -> list[dict[str, Any]]:
    """Generate source-backed gap-fill obligations without silent truncation."""
    receipt = _coverage_budget_receipt(gaps, max_obligations)
    generated = _ORIGINAL_BUILD_COVERAGE_OBLIGATIONS(
        behavior_ir,
        gaps,
        max_obligations=receipt["effective_limit"],
    )
    generated = attach_coverage_origin(generated, gaps)
    return _annotate_coverage_budget(generated, gaps, receipt)


# Helpers defined in the base module resolve globals in that defining module at
# call time. Redirect planning-authority names so all existing enrich helpers
# observe the exact coverage map and lossless default budget.
_base.build_behavior_ir_coverage_map = build_behavior_ir_coverage_map
_base._obligation_covers_node = _obligation_covers_node
_base.compute_obligation_coverage_gaps = compute_obligation_coverage_gaps
_base.build_source_backed_coverage_hypotheses = build_source_backed_coverage_hypotheses
_base.build_source_backed_coverage_obligations = build_source_backed_coverage_obligations
