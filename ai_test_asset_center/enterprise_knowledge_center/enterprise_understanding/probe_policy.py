"""Pure Probe admission policy for the enterprise-understanding pipeline."""
from __future__ import annotations

from typing import Any, Callable

from .schema import as_dict


def probe_generation_block_reason(asset: dict[str, Any]) -> str:
    """Return the first closed formal gate, or empty text when admission passes."""
    lexicon = as_dict(asset.get("semantic_lexicon_contract"))
    if not lexicon:
        return "SEMANTIC_LEXICON_CONTRACT_NOT_BUILT"
    if not bool(lexicon.get("entry_allowed")):
        return "SEMANTIC_LEXICON_CONTRACT_CLOSED"

    facts = as_dict(asset.get("structure_first_business_fact_compilation_receipt"))
    if not facts:
        return "STRUCTURE_FIRST_BUSINESS_FACT_COMPILATION_NOT_BUILT"
    if str(facts.get("status") or "").upper() != "PASS":
        return "STRUCTURE_FIRST_BUSINESS_FACT_COMPILATION_CLOSED"

    comprehension = as_dict(asset.get("enterprise_comprehension_gate"))
    if not comprehension:
        return "ENTERPRISE_COMPREHENSION_GATE_NOT_BUILT"
    if not bool(comprehension.get("entry_allowed")):
        return "ENTERPRISE_COMPREHENSION_GATE_CLOSED"

    planning = as_dict(asset.get("scenario_planning_gate"))
    if not planning:
        return "SCENARIO_PLANNING_GATE_NOT_BUILT"
    if not bool(planning.get("scenario_planning_allowed")):
        return "SCENARIO_PLANNING_GATE_CLOSED"

    required = (
        ("scenario_ir_gate", "SCENARIO_IR_GATE"),
        ("binding_identity_gate", "BINDING_IDENTITY_GATE"),
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
