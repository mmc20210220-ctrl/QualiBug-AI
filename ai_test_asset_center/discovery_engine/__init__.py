"""discovery_engine package - backward-compatible facade.

All public and private symbols are re-exported so that existing
``from .discovery_engine import X`` continues to work.
"""
from ._common import *  # noqa: F401,F403
from ._budget import *  # noqa: F401,F403
from ._engine import *  # noqa: F401,F403
from ._entry import *  # noqa: F401,F403

# Explicit re-exports for underscore-prefixed symbols
from ._budget import _safe_int, _safe_float, _read_budget_setting, _verification_step_count, _hypothesis_source_count, _is_write_hypothesis, _classify_budget_tier, _get_execution_budget_settings, _apply_drift_guardrails, _summarize_execution_feedback, _derive_execution_budget_targets, _plan_execution_budget, _apply_execution_budget_profile  # noqa: F401
from ._entry import _build_basic_probes  # noqa: F401

__all__ = [
    "logger",
    "DiscoveryFinding",
    "_safe_int",
    "_safe_float",
    "_read_budget_setting",
    "_verification_step_count",
    "_hypothesis_source_count",
    "_is_write_hypothesis",
    "_classify_budget_tier",
    "_get_execution_budget_settings",
    "_apply_drift_guardrails",
    "_summarize_execution_feedback",
    "_derive_execution_budget_targets",
    "_plan_execution_budget",
    "_apply_execution_budget_profile",
    "AutonomousDiscoveryEngine",
    "run_discovery",
    "run_generic_probes",
    "_build_basic_probes",
]
