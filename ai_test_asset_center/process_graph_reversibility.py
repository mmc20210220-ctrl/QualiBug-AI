"""Public per-source-step reversibility authority for governed graphs.

The existing node-proof implementation remains unchanged in
``process_graph_reversibility_core``. This facade binds the independently frozen
rollback dependency contract into the aggregate graph proof fingerprint and
revalidates both authorities before transport.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

from . import process_graph_reversibility_core as _core
from .process_graph_rollback_contract import (
    ROLLBACK_CONTRACT_DRIFT,
    validate_process_graph_rollback_contract,
)


for _name in dir(_core):
    if not _name.startswith("__") and not _name.startswith("_original_"):
        globals()[_name] = getattr(_core, _name)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _stable_hash(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _rollback_contract(experiment: dict[str, Any]) -> dict[str, Any]:
    exp = _dict(experiment)
    write_contract = _dict(exp.get("process_graph_write_contract"))
    graph = _dict(exp.get("execution_graph"))
    return _dict(
        exp.get("process_graph_rollback_contract")
        or write_contract.get("rollback_contract")
        or graph.get("rollback_contract")
    )


def build_process_graph_proof_fingerprint(proof: dict[str, Any]) -> str:
    """Compute the rollback-bound graph proof fingerprint."""
    row = _dict(proof)
    base_fingerprint = _text(row.get("base_reversibility_fingerprint"))
    rollback_id = _text(row.get("process_graph_rollback_contract_id"))
    if not base_fingerprint and not rollback_id:
        return _core.build_process_graph_proof_fingerprint(row)
    return _stable_hash(
        {
            "schema_version": _text(row.get("schema_version")),
            "proof_id": _text(row.get("proof_id")),
            "proof_status": _text(row.get("proof_status")),
            "proof_kind": _text(row.get("proof_kind")),
            "execution_graph_id": _text(row.get("execution_graph_id")),
            "process_graph_write_contract_id": _text(
                row.get("process_graph_write_contract_id")
            ),
            "process_graph_rollback_contract_id": rollback_id,
            "base_reversibility_fingerprint": base_fingerprint,
        }
    )[:32]


def _blocked_result(
    experiment: dict[str, Any],
    *,
    detail: str,
) -> dict[str, Any]:
    result = deepcopy(experiment)
    result["control_plan"] = []
    result["treatment_plan"] = []
    result["cleanup_plan"] = []
    result["compile_receipt"] = {
        "status": "BLOCKED",
        "reason_code": ROLLBACK_CONTRACT_DRIFT,
        "detail": detail,
    }
    return result


def finalize_process_graph_reversibility(
    experiment: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    """Freeze per-node proofs and bind the frozen rollback contract identity."""
    result = _core.finalize_process_graph_reversibility(
        experiment,
        behavior_ir,
    )
    if _text(_dict(result.get("compile_receipt")).get("status")) != "COMPILED":
        return result

    proof = deepcopy(_dict(result.get("write_reversibility_proof")))
    if not _core.is_process_graph_reversibility_proof(proof):
        return result
    graph = _dict(result.get("execution_graph"))
    write_contract = _dict(result.get("process_graph_write_contract"))
    rollback = _rollback_contract(result)
    valid, detail = validate_process_graph_rollback_contract(
        graph,
        write_contract,
        rollback,
    )
    if not valid:
        return _blocked_result(
            result,
            detail=detail or "rollback_contract_validation_failed",
        )
    rollback_id = _text(rollback.get("contract_fingerprint"))
    base_fingerprint = _text(proof.get("fingerprint"))
    if not rollback_id or not base_fingerprint:
        return _blocked_result(
            result,
            detail="rollback_or_reversibility_fingerprint_missing",
        )

    proof["base_reversibility_fingerprint"] = base_fingerprint
    proof["process_graph_rollback_contract_id"] = rollback_id
    proof["fingerprint"] = build_process_graph_proof_fingerprint(proof)
    result["write_reversibility_proof"] = proof

    contract_copy = deepcopy(write_contract)
    contract_copy["reversibility_fingerprint"] = proof["fingerprint"]
    contract_copy["base_reversibility_fingerprint"] = base_fingerprint
    contract_copy["process_graph_rollback_contract_id"] = rollback_id
    result["process_graph_write_contract"] = contract_copy
    receipt = dict(_dict(result.get("compile_receipt")))
    receipt.update(
        {
            "write_reversibility_fingerprint": proof["fingerprint"],
            "base_reversibility_fingerprint": base_fingerprint,
            "process_graph_rollback_contract_id": rollback_id,
            "rollback_bound_reversibility": True,
        }
    )
    result["compile_receipt"] = receipt
    return result


def validate_process_graph_reversibility_runtime(
    experiment: dict[str, Any],
    behavior_ir: dict[str, Any],
    *,
    compile_proof_fingerprint: str,
    runtime_bindings: dict[str, Any] | None = None,
    binding_receipts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Revalidate rollback identity and every per-node ordinary proof."""
    exp = _dict(experiment)
    proof = _dict(exp.get("write_reversibility_proof"))
    if not _core.is_process_graph_reversibility_proof(proof):
        return {
            "valid": False,
            "reason_code": _core.GRAPH_REVERSIBILITY_INVALID,
            "detail": "graph_reversibility_proof_schema_invalid",
            "proof": {},
            "coverage": {},
            "phase": "runtime",
        }
    attached = _text(proof.get("fingerprint"))
    computed = build_process_graph_proof_fingerprint(proof)
    if (
        not attached
        or attached != computed
        or (
            compile_proof_fingerprint
            and compile_proof_fingerprint != attached
        )
    ):
        return {
            "valid": False,
            "reason_code": _core.GRAPH_REVERSIBILITY_INVALID,
            "detail": (
                "rollback_bound_graph_proof_fingerprint_mismatch:"
                f"compile={compile_proof_fingerprint}:"
                f"attached={attached}:computed={computed}"
            ),
            "proof": proof,
            "coverage": {},
            "phase": "runtime",
        }

    graph = _dict(exp.get("execution_graph"))
    write_contract = _dict(exp.get("process_graph_write_contract"))
    rollback = _rollback_contract(exp)
    valid, detail = validate_process_graph_rollback_contract(
        graph,
        write_contract,
        rollback,
    )
    rollback_id = _text(rollback.get("contract_fingerprint"))
    if (
        not valid
        or not rollback_id
        or rollback_id
        != _text(proof.get("process_graph_rollback_contract_id"))
    ):
        return {
            "valid": False,
            "reason_code": ROLLBACK_CONTRACT_DRIFT,
            "detail": detail or "rollback_contract_identity_mismatch",
            "proof": proof,
            "coverage": {},
            "phase": "runtime",
        }

    base_fingerprint = _text(proof.get("base_reversibility_fingerprint"))
    if not base_fingerprint:
        return {
            "valid": False,
            "reason_code": _core.GRAPH_REVERSIBILITY_INVALID,
            "detail": "base_reversibility_fingerprint_missing",
            "proof": proof,
            "coverage": {},
            "phase": "runtime",
        }
    core_exp = deepcopy(exp)
    core_proof = deepcopy(proof)
    core_proof["fingerprint"] = base_fingerprint
    core_exp["write_reversibility_proof"] = core_proof
    validation = _core.validate_process_graph_reversibility_runtime(
        core_exp,
        behavior_ir,
        compile_proof_fingerprint=base_fingerprint,
        runtime_bindings=runtime_bindings or {},
        binding_receipts=binding_receipts or [],
    )
    if validation.get("valid") is not True:
        return validation
    validation["proof"] = proof
    validation["rollback_contract_validation"] = {
        "valid": True,
        "contract_fingerprint": rollback_id,
    }
    validation["runtime_fingerprint"] = attached
    return validation


is_process_graph_reversibility_proof = (
    _core.is_process_graph_reversibility_proof
)


__all__ = sorted(
    {
        *[
            name
            for name in dir(_core)
            if not name.startswith("__")
        ],
        "build_process_graph_proof_fingerprint",
        "finalize_process_graph_reversibility",
        "validate_process_graph_reversibility_runtime",
        "is_process_graph_reversibility_proof",
    }
)
