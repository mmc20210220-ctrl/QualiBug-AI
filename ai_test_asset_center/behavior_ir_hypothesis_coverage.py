"""Compatibility facade adding exact Recall coverage authority.

The pre-fix implementation is preserved byte-for-byte in
``behavior_ir_hypothesis_coverage_base``.  All historical names remain
available; only coverage-authority functions are hardened here.
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

# Preserve the module's historical public/private surface. Planning code uses
# private helpers such as _stable_id, so a normal ``import *`` is insufficient.
for _name in dir(_base):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_base, _name))

_ORIGINAL_BUILD_COVERAGE_MAP = _base.build_behavior_ir_coverage_map
_ORIGINAL_BUILD_COVERAGE_OBLIGATIONS = _base.build_source_backed_coverage_obligations


def build_behavior_ir_coverage_map(behavior_ir: dict[str, Any]) -> dict[str, Any]:
    return harden_behavior_ir_coverage_map(
        _ORIGINAL_BUILD_COVERAGE_MAP(behavior_ir), behavior_ir
    )


def _obligation_covers_node(
    obligation: dict[str, Any], node: dict[str, Any]
) -> bool:
    return bool(obligation_match_dimensions(obligation, node))


def compute_obligation_coverage_gaps(
    behavior_ir: dict[str, Any],
    obligations: list[dict[str, Any]],
) -> dict[str, Any]:
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
    generated = _ORIGINAL_BUILD_COVERAGE_OBLIGATIONS(
        behavior_ir, gaps, max_obligations=max_obligations
    )
    return attach_coverage_origin(generated, gaps)


# Functions defined in the base module resolve globals from that module at
# call time. Redirect only these authority names so enrich_* helpers also use
# the hardened implementation without rewriting the legacy core.
_base.build_behavior_ir_coverage_map = build_behavior_ir_coverage_map
_base._obligation_covers_node = _obligation_covers_node
_base.compute_obligation_coverage_gaps = compute_obligation_coverage_gaps
_base.build_source_backed_coverage_obligations = build_source_backed_coverage_obligations
