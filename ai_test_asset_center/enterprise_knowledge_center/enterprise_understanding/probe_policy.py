"""Pure Probe admission policy for the enterprise-understanding pipeline.

Probe generation is a downstream compilation step. It must never be enabled by
replacing ``_probes_from_asset`` at import time or by stacking wrappers around a
shared global function. This module evaluates the completed asset explicitly and
then delegates only when every formal gate is open and the budget is positive.
"""
from __future__ import annotations

from typing import Any, Callable

from .schema import as_dict


def probe_generation_block_reason(asset: dict[str, Any]) -> str:
    """Return the first closed formal gate, or an empty string when admission passes."""
    planning = as_dict(asset.get("scenario_planning_gate"))
    if not planning:
        return "SCENARIO_PLANNING_GATE_NOT_BUILT"
    if not bool(planning.get("scenario_planning_allowed")):
        return "SCENARIO_PLANNING_GATE_CLOSED"

    required = (
        ("scenario_ir_gate", "SCENARIO_IR_GATE"),
        ("scenario_execution_contract_gate", "SCENARIO_EXECUTION_CONTRACT_GATE"),
        ("runtime_plan_gate", "RUNTIME_PLAN_GATE"),
        ("runtime_materialization_gate", "RUNTIME_MATERIALIZATION_GATE"),
    )
    for key, label in required:
        gate = as_dict(asset.get(key))
        if not gate:
            return f"{label}_NOT_BUILT"
        if not bool(gate.get("entry_allowed")):
            return f"{label}_CLOSED"
    return ""


def probe_generation_allowed(asset: dict[str, Any]) -> bool:
    return not probe_generation_block_reason(asset)


def build_gated_probes(
    asset: dict[str, Any],
    max_count: int = 140,
    *,
    compiler: Callable[[dict[str, Any], int], list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """Compile Probes after gate closure without mutating module-level authority."""
    limit = max(0, int(max_count))
    if limit == 0 or not probe_generation_allowed(asset):
        return []
    if compiler is None:
        from .. import _linking

        compiler = _linking._probes_from_asset
    return [
        dict(row)
        for row in compiler(asset, limit)
        if isinstance(row, dict)
    ]


__all__ = [
    "probe_generation_block_reason",
    "probe_generation_allowed",
    "build_gated_probes",
]
