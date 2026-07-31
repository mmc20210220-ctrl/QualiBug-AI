"""Pure Probe admission policy for the enterprise-understanding pipeline."""
from __future__ import annotations

from typing import Any, Callable

from .probe_binding_lineage import attach_runtime_observer_lineage
from .schema import as_dict


def probe_generation_block_reason(asset: dict[str, Any]) -> str:
    """Return the first closed formal gate, or empty text when admission passes."""
    # These receipts are mandatory in the formal composition root. Compatibility
    # unit assets and explicitly migrated legacy assets may predate them; when a
    # receipt is present its failure is authoritative and can never be bypassed.
    lexicon = as_dict(asset.get("semantic_lexicon_contract"))
    if lexicon and not bool(lexicon.get("entry_allowed")):
        return "SEMANTIC_LEXICON_CONTRACT_CLOSED"

    facts = as_dict(asset.get("structure_first_business_fact_compilation_receipt"))
    if facts and str(facts.get("status") or "").upper() != "PASS":
        return "STRUCTURE_FIRST_BUSINESS_FACT_COMPILATION_CLOSED"

    rules = as_dict(asset.get("implicit_rule_projection_gate"))
    if rules and not bool(rules.get("entry_allowed")):
        return "IMPLICIT_RULE_PROJECTION_GATE_CLOSED"

    comprehension = as_dict(asset.get("enterprise_comprehension_gate"))
    if comprehension and not bool(comprehension.get("entry_allowed")):
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
    """Compile Probes only after gates and observer identities close."""
    limit = max(0, int(max_count))
    if limit == 0 or not probe_generation_allowed(asset):
        return []
    if compiler is None:
        from .. import _linking

        compiler = _linking._probes_from_asset
    compiled = [
        dict(row)
        for row in compiler(asset, limit)
        if isinstance(row, dict)
    ]
    return attach_runtime_observer_lineage(asset, compiled)


__all__ = [
    "probe_generation_block_reason",
    "probe_generation_allowed",
    "build_gated_probes",
]
