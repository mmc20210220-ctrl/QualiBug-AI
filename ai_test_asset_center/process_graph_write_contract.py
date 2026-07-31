"""Public process-graph write-contract authority.

The existing topology, operation, observer and compensation normalization stays
in ``process_graph_write_contract_core``.  This facade changes only the final
proof scope: every graph write receives its own ordinary
WriteReversibilityProof, and those proofs are frozen into one graph proof set.
Read-only graphs and all established exports remain unchanged.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from . import process_graph_write_contract_core as _core
from .process_graph_reversibility import (
    finalize_process_graph_reversibility,
)


for _name in dir(_core):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_core, _name)


def finalize_process_graph_write_contract(
    experiment: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    """Freeze graph write safety and one proof per formal write node."""
    exp = deepcopy(experiment)
    if _core._text(_core._dict(exp.get("compile_receipt")).get("status")) != "COMPILED":
        return exp

    graph, detail = _core._extract_graph(exp)
    if detail:
        return _core._blocked(exp, _core.GRAPH_WRITE_CONTRACT_INVALID, detail)
    if not graph:
        return exp

    canonical_graph, contract, reason, detail = _core._canonicalize_graph(
        graph,
        behavior_ir,
    )
    if reason:
        return _core._blocked(exp, reason, detail)

    exp["execution_graph"] = canonical_graph
    exp["process_graph_write_contract"] = contract
    nodes_by_id = {
        _core._text(row.get("node_id")): row
        for row in _core._list(canonical_graph.get("nodes"))
        if isinstance(row, dict) and _core._text(row.get("node_id"))
    }
    for step in _core._list(exp.get("treatment_plan")):
        if not isinstance(step, dict):
            continue
        node_id = _core._text(step.get("step_id"))
        node = _core._dict(nodes_by_id.get(node_id))
        if not node:
            continue
        step.update(
            {
                "method": _core._text(node.get("method")),
                "path": _core._text(node.get("path")),
                "effect_observer_operations": deepcopy(
                    _core._list(node.get("effect_observer_operations"))
                ),
                "_execution_graph": deepcopy(canonical_graph),
                "_graph_write_contract_id": _core._text(
                    contract.get("contract_id")
                ),
            }
        )

    write_step_ids = _core._list(contract.get("write_step_ids"))
    if not write_step_ids:
        return exp

    exp["cleanup_plan"] = deepcopy(_core._list(contract.get("cleanup_steps")))
    exp["observers"] = _core._merge_graph_observers(exp, contract)
    safety = dict(_core._dict(exp.get("safety_contract")))
    safety.update(
        {
            "governed_write": True,
            "cleanup_not_required": False,
            "cleanup_authority": "process_graph_write_contract",
            "process_graph_write_contract_id": _core._text(
                contract.get("contract_id")
            ),
        }
    )
    exp["safety_contract"] = safety
    receipt = dict(_core._dict(exp.get("compile_receipt")))
    receipt.update(
        {
            "cleanup_present": True,
            "process_graph_write_contract_id": _core._text(
                contract.get("contract_id")
            ),
            "graph_write_step_count": len(write_step_ids),
        }
    )
    exp["compile_receipt"] = receipt
    return finalize_process_graph_reversibility(exp, behavior_ir)


__all__ = sorted(
    {
        *[
            name
            for name in dir(_core)
            if not name.startswith("__")
        ],
        "finalize_process_graph_write_contract",
    }
)
