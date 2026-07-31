"""Per-source-step reversibility authority for governed process graphs.

The existing cleanup-plan validator and WriteReversibilityProof remain the only
semantic proof engines.  This module scopes one ordinary proof to every formal
write node, freezes their fingerprints into one graph proof set, and replays the
same validators at runtime before any graph transport.

No graph write is represented by another node's compensator, proof or binding.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

from .cleanup_plan_validator import validate_cleanup_plan


GRAPH_REVERSIBILITY_SCHEMA = "qualibug.process-graph-reversibility-proof.v1"
GRAPH_REVERSIBILITY_INVALID = "BLOCKED_CLEANUP_CONTRACT_DRIFT"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


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


def is_process_graph_reversibility_proof(proof: dict[str, Any]) -> bool:
    return _text(_dict(proof).get("schema_version")) == GRAPH_REVERSIBILITY_SCHEMA


def build_process_graph_proof_fingerprint(proof: dict[str, Any]) -> str:
    row = _dict(proof)
    content = {
        "schema_version": _text(row.get("schema_version")),
        "proof_id": _text(row.get("proof_id")),
        "proof_status": _text(row.get("proof_status")),
        "proof_kind": _text(row.get("proof_kind")),
        "execution_graph_id": _text(row.get("execution_graph_id")),
        "process_graph_write_contract_id": _text(
            row.get("process_graph_write_contract_id")
        ),
        "write_step_ids": [_text(value) for value in _list(row.get("write_step_ids"))],
        "cleanup_order": [_text(value) for value in _list(row.get("cleanup_order"))],
        "step_proof_fingerprints_by_id": {
            _text(key): _text(value)
            for key, value in sorted(
                _dict(row.get("step_proof_fingerprints_by_id")).items()
            )
        },
        "step_proofs_by_id": {
            _text(key): _dict(value)
            for key, value in sorted(_dict(row.get("step_proofs_by_id")).items())
        },
    }
    return _stable_hash(content)[:32]


def _blocked(
    experiment: dict[str, Any],
    *,
    reason_code: str,
    detail: str,
) -> dict[str, Any]:
    result = deepcopy(experiment)
    result["control_plan"] = []
    result["treatment_plan"] = []
    result["cleanup_plan"] = []
    result["compile_receipt"] = {
        "status": "BLOCKED",
        "reason_code": reason_code,
        "detail": detail,
    }
    return result


def _treatment_by_step(experiment: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        _text(row.get("step_id") or row.get("id")): row
        for row in _list(experiment.get("treatment_plan"))
        if isinstance(row, dict) and _text(row.get("step_id") or row.get("id"))
    }


def _cleanup_by_source(experiment: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], str]:
    result: dict[str, dict[str, Any]] = {}
    for raw in _list(experiment.get("cleanup_plan")):
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        source_step_id = _text(
            row.get("source_step_id")
            or row.get("compensates_step_id")
            or row.get("write_step_id")
        )
        if not source_step_id:
            return {}, "cleanup_source_step_identity_missing"
        if source_step_id in result:
            return {}, f"duplicate_cleanup_source_step:{source_step_id}"
        result[source_step_id] = row
    return result, ""


def _node_experiment(
    experiment: dict[str, Any],
    *,
    source_step_id: str,
    treatment_step: dict[str, Any],
    cleanup_step: dict[str, Any],
) -> dict[str, Any]:
    step = deepcopy(treatment_step)
    step.pop("_execution_graph", None)
    step.pop("_graph_write_contract_id", None)
    cleanup = deepcopy(cleanup_step)
    return {
        "experiment_id": (
            f"{_text(experiment.get('experiment_id'))}::{source_step_id}"
        ),
        "obligation_id": _text(experiment.get("obligation_id")),
        "compile_receipt": {"status": "COMPILED"},
        "control_plan": [],
        "treatment_plan": [step],
        "cleanup_plan": [cleanup],
        "safety_contract": {
            "governed_write": True,
            "cleanup_not_required": False,
        },
        "source_refs": deepcopy(_list(experiment.get("source_refs"))),
        "cleanup_requirement": deepcopy(
            _dict(experiment.get("cleanup_requirement"))
        ),
    }


def _contract_scope(
    experiment: dict[str, Any],
) -> tuple[
    dict[str, Any],
    list[str],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    str,
]:
    contract = _dict(experiment.get("process_graph_write_contract"))
    if _text(contract.get("status")) != "RESOLVED":
        return {}, [], {}, {}, "graph_write_contract_not_resolved"
    write_step_ids = [
        _text(value)
        for value in _list(contract.get("write_step_ids"))
        if _text(value)
    ]
    if not write_step_ids or len(write_step_ids) != len(set(write_step_ids)):
        return contract, write_step_ids, {}, {}, "graph_write_step_identity_invalid"
    cleanup_order = [
        _text(value)
        for value in _list(contract.get("cleanup_order"))
        if _text(value)
    ]
    if cleanup_order != list(reversed(write_step_ids)):
        return (
            contract,
            write_step_ids,
            {},
            {},
            "graph_cleanup_order_not_reverse_write_order:"
            f"expected={','.join(reversed(write_step_ids))}:"
            f"actual={','.join(cleanup_order)}",
        )
    treatment = _treatment_by_step(experiment)
    cleanup, detail = _cleanup_by_source(experiment)
    if detail:
        return contract, write_step_ids, treatment, {}, detail
    expected = set(write_step_ids)
    if set(cleanup) != expected:
        return (
            contract,
            write_step_ids,
            treatment,
            cleanup,
            "graph_cleanup_scope_mismatch:"
            f"missing={','.join(sorted(expected - set(cleanup)))}:"
            f"unknown={','.join(sorted(set(cleanup) - expected))}",
        )
    if any(step_id not in treatment for step_id in write_step_ids):
        missing = [step_id for step_id in write_step_ids if step_id not in treatment]
        return (
            contract,
            write_step_ids,
            treatment,
            cleanup,
            "graph_treatment_step_missing:" + ",".join(missing),
        )
    return contract, write_step_ids, treatment, cleanup, ""


def finalize_process_graph_reversibility(
    experiment: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    """Freeze one ordinary WRP per graph write and one aggregate proof set."""
    exp = deepcopy(experiment)
    contract, write_ids, treatment, cleanup, detail = _contract_scope(exp)
    if detail:
        return _blocked(
            exp,
            reason_code=GRAPH_REVERSIBILITY_INVALID,
            detail=detail,
        )

    step_proofs: dict[str, dict[str, Any]] = {}
    step_coverage: dict[str, dict[str, Any]] = {}
    for source_step_id in write_ids:
        node_exp = _node_experiment(
            exp,
            source_step_id=source_step_id,
            treatment_step=treatment[source_step_id],
            cleanup_step=cleanup[source_step_id],
        )
        validation = validate_cleanup_plan(
            node_exp,
            behavior_ir,
            phase="compile",
        )
        if validation.get("valid") is not True:
            return _blocked(
                exp,
                reason_code=(
                    _text(validation.get("reason_code"))
                    or GRAPH_REVERSIBILITY_INVALID
                ),
                detail=(
                    f"{source_step_id}:"
                    + (
                        _text(validation.get("detail"))
                        or "node_cleanup_validation_failed"
                    )
                ),
            )
        proof = _dict(validation.get("proof"))
        if (
            _text(proof.get("proof_status")) != "PROVEN"
            or not _text(proof.get("proof_id"))
            or not _text(proof.get("fingerprint"))
        ):
            return _blocked(
                exp,
                reason_code=GRAPH_REVERSIBILITY_INVALID,
                detail=f"{source_step_id}:node_reversibility_proof_invalid",
            )
        step_proofs[source_step_id] = deepcopy(proof)
        step_coverage[source_step_id] = deepcopy(
            _dict(validation.get("coverage"))
        )

    step_fingerprints = {
        step_id: _text(proof.get("fingerprint"))
        for step_id, proof in step_proofs.items()
    }
    graph_proof = {
        "schema_version": GRAPH_REVERSIBILITY_SCHEMA,
        "proof_id": "pgrp_"
        + _stable_hash(
            {
                "execution_graph_id": _text(contract.get("execution_graph_id")),
                "contract_id": _text(contract.get("contract_id")),
                "write_step_ids": write_ids,
                "cleanup_order": _list(contract.get("cleanup_order")),
                "step_fingerprints": step_fingerprints,
            }
        )[:24],
        "proof_status": "PROVEN",
        "proof_kind": "process_graph_per_source_step",
        "execution_graph_id": _text(contract.get("execution_graph_id")),
        "process_graph_write_contract_id": _text(contract.get("contract_id")),
        "write_step_ids": list(write_ids),
        "cleanup_order": list(_list(contract.get("cleanup_order"))),
        "step_proofs_by_id": step_proofs,
        "step_proof_fingerprints_by_id": step_fingerprints,
        "fingerprint": "",
    }
    graph_proof["fingerprint"] = build_process_graph_proof_fingerprint(
        graph_proof
    )

    contract_copy = deepcopy(contract)
    contract_copy["reversibility_proof_schema"] = GRAPH_REVERSIBILITY_SCHEMA
    contract_copy["reversibility_proof_id"] = graph_proof["proof_id"]
    contract_copy["reversibility_fingerprint"] = graph_proof["fingerprint"]
    contract_copy["step_reversibility_proofs_by_id"] = deepcopy(step_proofs)
    contract_copy["step_reversibility_fingerprints_by_id"] = dict(
        step_fingerprints
    )
    write_contracts = deepcopy(
        _dict(contract_copy.get("write_contracts_by_node"))
    )
    for step_id, proof in step_proofs.items():
        row = dict(_dict(write_contracts.get(step_id)))
        row["write_reversibility_proof_id"] = _text(proof.get("proof_id"))
        row["write_reversibility_fingerprint"] = _text(
            proof.get("fingerprint")
        )
        write_contracts[step_id] = row
    contract_copy["write_contracts_by_node"] = write_contracts

    exp["process_graph_write_contract"] = contract_copy
    exp["write_reversibility_proof"] = graph_proof
    exp["cleanup_coverage_contract"] = {
        "schema_version": "qualibug.process-graph-cleanup-coverage.v1",
        "valid": True,
        "write_step_ids": list(write_ids),
        "cleanup_source_step_ids": list(_list(contract.get("cleanup_order"))),
        "step_coverage_by_id": step_coverage,
    }
    receipt = dict(_dict(exp.get("compile_receipt")))
    receipt.update(
        {
            "write_reversibility_proof_id": graph_proof["proof_id"],
            "write_reversibility_fingerprint": graph_proof["fingerprint"],
            "graph_step_reversibility_proof_count": len(step_proofs),
            "cleanup_semantic_validated": True,
        }
    )
    exp["compile_receipt"] = receipt
    return exp


def validate_process_graph_reversibility_runtime(
    experiment: dict[str, Any],
    behavior_ir: dict[str, Any],
    *,
    compile_proof_fingerprint: str,
    runtime_bindings: dict[str, Any] | None = None,
    binding_receipts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Revalidate every frozen node proof and aggregate fingerprint."""
    exp = _dict(experiment)
    proof = _dict(exp.get("write_reversibility_proof"))
    if not is_process_graph_reversibility_proof(proof):
        return {
            "valid": False,
            "reason_code": GRAPH_REVERSIBILITY_INVALID,
            "detail": "graph_reversibility_proof_schema_invalid",
            "proof": {},
            "coverage": {},
            "phase": "runtime",
        }
    attached = _text(proof.get("fingerprint"))
    computed = build_process_graph_proof_fingerprint(proof)
    if (
        not attached
        or computed != attached
        or (
            compile_proof_fingerprint
            and compile_proof_fingerprint != attached
        )
    ):
        return {
            "valid": False,
            "reason_code": GRAPH_REVERSIBILITY_INVALID,
            "detail": (
                "graph_proof_fingerprint_mismatch:"
                f"compile={compile_proof_fingerprint}:"
                f"attached={attached}:computed={computed}"
            ),
            "proof": proof,
            "coverage": {},
            "phase": "runtime",
        }

    contract, write_ids, treatment, cleanup, detail = _contract_scope(exp)
    if detail:
        return {
            "valid": False,
            "reason_code": GRAPH_REVERSIBILITY_INVALID,
            "detail": detail,
            "proof": proof,
            "coverage": {},
            "phase": "runtime",
        }
    stored_proofs = _dict(proof.get("step_proofs_by_id"))
    stored_fingerprints = _dict(
        proof.get("step_proof_fingerprints_by_id")
    )
    if set(stored_proofs) != set(write_ids):
        return {
            "valid": False,
            "reason_code": GRAPH_REVERSIBILITY_INVALID,
            "detail": "graph_step_proof_scope_mismatch",
            "proof": proof,
            "coverage": {},
            "phase": "runtime",
        }

    runtime_step_proofs: dict[str, dict[str, Any]] = {}
    runtime_coverage: dict[str, dict[str, Any]] = {}
    for source_step_id in write_ids:
        node_exp = _node_experiment(
            exp,
            source_step_id=source_step_id,
            treatment_step=treatment[source_step_id],
            cleanup_step=cleanup[source_step_id],
        )
        expected_fp = _text(stored_fingerprints.get(source_step_id))
        validation = validate_cleanup_plan(
            node_exp,
            behavior_ir,
            phase="runtime",
            compile_proof_fingerprint=expected_fp,
            runtime_bindings=runtime_bindings or {},
            binding_receipts=binding_receipts or [],
        )
        if validation.get("valid") is not True:
            return {
                "valid": False,
                "reason_code": (
                    _text(validation.get("reason_code"))
                    or GRAPH_REVERSIBILITY_INVALID
                ),
                "detail": (
                    f"{source_step_id}:"
                    + (
                        _text(validation.get("detail"))
                        or "runtime_node_cleanup_validation_failed"
                    )
                ),
                "proof": proof,
                "coverage": {},
                "phase": "runtime",
            }
        runtime_proof = _dict(validation.get("proof"))
        if _text(runtime_proof.get("fingerprint")) != expected_fp:
            return {
                "valid": False,
                "reason_code": GRAPH_REVERSIBILITY_INVALID,
                "detail": f"{source_step_id}:runtime_node_proof_drift",
                "proof": proof,
                "coverage": {},
                "phase": "runtime",
            }
        runtime_step_proofs[source_step_id] = runtime_proof
        runtime_coverage[source_step_id] = _dict(
            validation.get("coverage")
        )

    return {
        "valid": True,
        "reason_code": "",
        "detail": "",
        "proof": proof,
        "coverage": {
            "schema_version": "qualibug.process-graph-cleanup-coverage.v1",
            "valid": True,
            "write_step_ids": write_ids,
            "cleanup_source_step_ids": list(
                _list(contract.get("cleanup_order"))
            ),
            "step_coverage_by_id": runtime_coverage,
        },
        "runtime_step_proofs_by_id": runtime_step_proofs,
        "phase": "runtime",
    }


__all__ = [
    "GRAPH_REVERSIBILITY_SCHEMA",
    "GRAPH_REVERSIBILITY_INVALID",
    "is_process_graph_reversibility_proof",
    "build_process_graph_proof_fingerprint",
    "finalize_process_graph_reversibility",
    "validate_process_graph_reversibility_runtime",
]
