"""Public process-graph write-contract authority.

The existing topology, operation, observer and compensation normalization stays
in ``process_graph_write_contract_core``. Single-write graphs preserve the
established ordinary WriteReversibilityProof schema. Multi-write graphs receive
one ordinary proof per write, one rollback-bound aggregate proof set, and one
frozen rollback dependency contract.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from . import process_graph_write_contract_core as _core
from .process_graph_reversibility import (
    finalize_process_graph_reversibility,
)
from .process_graph_rollback_contract import (
    STATUS_FROZEN as ROLLBACK_STATUS_FROZEN,
    freeze_process_graph_rollback_contract,
)


for _name in dir(_core):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_core, _name)


def _finalize_single_write_proof(
    experiment: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    """Keep the third-batch single-write proof and downstream schema stable."""
    exp = deepcopy(experiment)
    validation = _core.validate_cleanup_plan(
        exp,
        behavior_ir,
        phase="compile",
    )
    if not validation.get("valid"):
        return _core._blocked(
            exp,
            _core._text(validation.get("reason_code"))
            or _core.GRAPH_WRITE_CONTRACT_INVALID,
            _core._text(validation.get("detail"))
            or "graph_cleanup_validation_failed",
        )
    proof = _core._dict(validation.get("proof"))
    exp["write_reversibility_proof"] = proof
    exp["cleanup_coverage_contract"] = _core._dict(
        validation.get("coverage")
    )
    exp["compile_receipt"].update(
        {
            "write_reversibility_proof_id": _core._text(
                proof.get("proof_id")
            ),
            "write_reversibility_fingerprint": _core._text(
                proof.get("fingerprint")
            ),
            "cleanup_semantic_validated": True,
            "graph_step_reversibility_proof_count": 1,
        }
    )
    return exp


def finalize_process_graph_write_contract(
    experiment: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    """Freeze graph write safety, rollback dependencies and proof scope."""
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

    rollback_contract = freeze_process_graph_rollback_contract(
        canonical_graph,
        contract,
    )
    if _core._text(rollback_contract.get("status")) != ROLLBACK_STATUS_FROZEN:
        return _core._blocked(
            exp,
            _core.GRAPH_WRITE_CONTRACT_INVALID,
            _core._text(rollback_contract.get("detail"))
            or "process_graph_rollback_contract_not_frozen",
        )
    rollback_fingerprint = _core._text(
        rollback_contract.get("contract_fingerprint")
    )
    contract = {
        **contract,
        "rollback_contract_id": rollback_fingerprint,
        "rollback_contract": deepcopy(rollback_contract),
    }
    canonical_graph = {
        **canonical_graph,
        "rollback_contract_id": rollback_fingerprint,
        "rollback_contract": deepcopy(rollback_contract),
    }

    exp["execution_graph"] = canonical_graph
    exp["process_graph_write_contract"] = contract
    exp["process_graph_rollback_contract"] = deepcopy(rollback_contract)
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
                "_graph_rollback_contract_id": rollback_fingerprint,
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
            "process_graph_rollback_contract_id": rollback_fingerprint,
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
            "process_graph_rollback_contract_id": rollback_fingerprint,
            "graph_write_step_count": len(write_step_ids),
        }
    )
    exp["compile_receipt"] = receipt
    if len(write_step_ids) == 1:
        return _finalize_single_write_proof(exp, behavior_ir)
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
