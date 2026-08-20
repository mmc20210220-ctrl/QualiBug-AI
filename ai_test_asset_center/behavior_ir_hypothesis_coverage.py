"""Compatibility facade adding exact Recall coverage authority.

The current mainline implementation is preserved byte-for-byte in
``behavior_ir_hypothesis_coverage_base``. Historical helpers remain available;
only the planning-authority functions that decide whether a Behavior-IR surface
is already covered are hardened here.
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


def build_source_backed_coverage_obligations(
    behavior_ir: dict[str, Any],
    gaps: dict[str, Any],
    *,
    max_obligations: int = MAX_COVERAGE_HYPOTHESES,
) -> list[dict[str, Any]]:
    """Generate legacy-compatible gap-fill obligations with exact origin."""
    generated = _ORIGINAL_BUILD_COVERAGE_OBLIGATIONS(
        behavior_ir, gaps, max_obligations=max_obligations
    )
    return attach_coverage_origin(generated, gaps)


# Helpers defined in the base module resolve globals in that defining module at
# call time. Redirect only planning-authority names so existing enrich helpers
# also observe the hardened map without copying or rewriting legacy mechanics.
_base.build_behavior_ir_coverage_map = build_behavior_ir_coverage_map
_base._obligation_covers_node = _obligation_covers_node
_base.compute_obligation_coverage_gaps = compute_obligation_coverage_gaps
_base.build_source_backed_coverage_obligations = build_source_backed_coverage_obligations
