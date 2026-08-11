"""Strict public authority for process-graph cleanup equivalence.

The existing per-step input builder and single-write comparison remain unchanged
in ``process_graph_cleanup_equivalence_core``. This facade seals their runtime
scope, handles formally proven zero-transport writes, and prevents unmeasured
residual counts from being represented as observed facts.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from . import process_graph_cleanup_equivalence_core as _core

for _name in dir(_core):
    if not _name.startswith("__") and not _name.startswith("_original_"):
        globals()[_name] = getattr(_core, _name)

GRAPH_EQUIVALENCE_SCOPE_INVALID = "PROCESS_GRAPH_EQUIVALENCE_SCOPE_INVALID"
GRAPH_SOURCE_WRITE_NOT_REACHED = (
    "PROCESS_GRAPH_SOURCE_WRITE_NOT_REACHED_TRANSPORT"
)


def _receipt_step_id(receipt: dict[str, Any]) -> str:
    return _core._text(
        receipt.get("source_step_id")
        or _core._dict(receipt.get("evidence")).get("source_step_id")
    )


def _seal_environment_truth(
    execution_set: dict[str, Any],
    *,
    equivalence_status: str = "",
) -> None:
    """Mark graph residue counts as unmeasured instead of inventing cardinality."""
    environment = _core._dict(
        _core._dict(execution_set).get("environment_restoration_receipt")
    )
    if not environment:
        return
    environment.update(
        {
            "created_rows_remaining": 0,
            "modified_rows_not_restored": 0,
            "deleted_rows_not_restored": 0,
            "residual_counts_measured": False,
            "restoration_basis": (
                "per_source_step_cleanup_equivalence"
                if equivalence_status
                else "pending_per_source_step_equivalence"
            ),
        }
    )
    for failure in _core._list(environment.get("cleanup_failures")):
        if isinstance(failure, dict):
            failure["residual_counts_measured"] = False


def _scope_payload(execution_set: dict[str, Any]) -> dict[str, Any]:
    row = _core._dict(execution_set)
    inputs = _core._dict(row.get("step_inputs_by_id"))
    node_receipts = _core._dict(
        row.get("step_cleanup_execution_receipts_by_id")
    )
    return {
        "schema_version": _core._text(row.get("schema_version")),
        "receipt_id": _core._text(row.get("receipt_id")),
        "proof_id": _core._text(row.get("proof_id")),
        "process_graph_write_contract_id": _core._text(
            row.get("process_graph_write_contract_id")
        ),
        "write_step_ids": list(_core._list(row.get("write_step_ids"))),
        "cleanup_order": list(_core._list(row.get("cleanup_order"))),
        "source_receipt_ids": list(
            _core._list(row.get("source_receipt_ids"))
        ),
        "status": _core._text(row.get("status")).upper(),
        "step_input_fingerprints_by_id": {
            key: _core._stable_hash(_core._dict(value))[:32]
            for key, value in sorted(inputs.items())
        },
        "step_cleanup_receipt_fingerprints_by_id": {
            key: _core._stable_hash(_core._dict(value))[:32]
            for key, value in sorted(node_receipts.items())
        },
    }


def build_process_graph_cleanup_scope_fingerprint(
    execution_set: dict[str, Any],
) -> str:
    return _core._stable_hash(_scope_payload(execution_set))[:32]


def finalize_process_graph_cleanup_equivalence_inputs(
    *,
    exp: dict[str, Any],
    result: dict[str, Any],
    resolved_campaign_id: str,
    runtime_bindings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    output = _core.finalize_process_graph_cleanup_equivalence_inputs(
        exp=exp,
        result=result,
        resolved_campaign_id=resolved_campaign_id,
        runtime_bindings=runtime_bindings,
    )
    observations = _core._dict(output.get("observations"))
    execution_set = _core._dict(
        observations.get("cleanup_execution_receipt")
    )
    if (
        _core._text(execution_set.get("schema_version"))
        != _core.GRAPH_CLEANUP_EXECUTION_SET_SCHEMA
    ):
        return output

    write_ids = [
        _core._text(value)
        for value in _core._list(execution_set.get("write_step_ids"))
        if _core._text(value)
    ]
    inputs = deepcopy(_core._dict(execution_set.get("step_inputs_by_id")))
    graph_receipts = _core._graph_cleanup_receipts(observations)
    rollback_outcomes = _core._dict(
        observations.get("process_graph_rollback_outcomes")
    )
    node_receipts = _core._dict(
        execution_set.get("step_cleanup_execution_receipts_by_id")
    )

    for step_id in write_ids:
        row = deepcopy(_core._dict(inputs.get(step_id)))
        scoped = [
            deepcopy(value)
            for value in graph_receipts.get(step_id, [])
            if isinstance(value, dict)
        ]
        graph_receipt = scoped[0] if len(scoped) == 1 else {}
        node_receipt = _core._dict(node_receipts.get(step_id))
        row.update(
            {
                "graph_cleanup_receipt": graph_receipt,
                "graph_cleanup_receipt_identity_valid": bool(
                    len(scoped) == 1
                    and _receipt_step_id(graph_receipt) == step_id
                ),
                "rollback_outcome": _core._text(
                    rollback_outcomes.get(step_id)
                ).upper(),
                "cleanup_execution_receipt_identity_valid": bool(
                    _core._text(node_receipt.get("source_step_id"))
                    == step_id
                    and _core._text(node_receipt.get("receipt_id"))
                    == _core._text(
                        _core._dict(
                            row.get("cleanup_execution_receipt")
                        ).get("receipt_id")
                    )
                ),
            }
        )
        inputs[step_id] = row

    execution_set["step_inputs_by_id"] = inputs
    execution_set["scope_fingerprint"] = (
        build_process_graph_cleanup_scope_fingerprint(execution_set)
    )
    environment = _core._dict(
        execution_set.get("environment_restoration_receipt")
    )
    if environment:
        environment["cleanup_execution_scope_fingerprint"] = (
            execution_set["scope_fingerprint"]
        )
    _seal_environment_truth(execution_set)
    observations["cleanup_execution_receipt"] = execution_set
    observations["cleanup_execution_receipts"] = [execution_set]
    observations[
        "process_graph_cleanup_equivalence_inputs_by_step"
    ] = inputs
    output["observations"] = observations
    return output


def _scope_error(
    proof: dict[str, Any], execution_set: dict[str, Any]
) -> str:
    graph_proof = _core._dict(proof)
    execution = _core._dict(execution_set)
    write_ids = [
        _core._text(value)
        for value in _core._list(graph_proof.get("write_step_ids"))
        if _core._text(value)
    ]
    if not _core.is_process_graph_reversibility_proof(graph_proof):
        return "graph_proof_schema_invalid"
    if (
        _core._text(execution.get("schema_version"))
        != _core.GRAPH_CLEANUP_EXECUTION_SET_SCHEMA
    ):
        return "cleanup_execution_set_schema_invalid"
    if not write_ids or len(write_ids) != len(set(write_ids)):
        return "write_step_identity_invalid"
    if list(_core._list(execution.get("write_step_ids"))) != write_ids:
        return "write_step_scope_mismatch"
    if _core._text(execution.get("proof_id")) != _core._text(
        graph_proof.get("proof_id")
    ):
        return "proof_identity_mismatch"
    if _core._text(
        execution.get("process_graph_write_contract_id")
    ) != _core._text(
        graph_proof.get("process_graph_write_contract_id")
    ):
        return "write_contract_identity_mismatch"
    if list(_core._list(execution.get("cleanup_order"))) != list(
        _core._list(graph_proof.get("cleanup_order"))
    ):
        return "cleanup_order_mismatch"

    expected = set(write_ids)
    inputs = _core._dict(execution.get("step_inputs_by_id"))
    proofs = _core._dict(graph_proof.get("step_proofs_by_id"))
    node_receipts = _core._dict(
        execution.get("step_cleanup_execution_receipts_by_id")
    )
    if set(inputs) != expected or set(proofs) != expected:
        return "step_input_or_proof_scope_mismatch"
    if set(node_receipts) != expected:
        return "cleanup_receipt_scope_mismatch"

    source_receipt_ids: list[str] = []
    for step_id in write_ids:
        row = _core._dict(inputs.get(step_id))
        node_proof = _core._dict(proofs.get(step_id))
        row_proof = _core._dict(row.get("proof"))
        node_receipt = _core._dict(node_receipts.get(step_id))
        row_receipt = _core._dict(
            row.get("cleanup_execution_receipt")
        )
        graph_receipt = _core._dict(row.get("graph_cleanup_receipt"))
        if _core._text(row.get("source_step_id")) != step_id:
            return f"{step_id}:step_input_identity_mismatch"
        if (
            _core._text(row_proof.get("proof_id"))
            != _core._text(node_proof.get("proof_id"))
            or _core._text(row_proof.get("fingerprint"))
            != _core._text(node_proof.get("fingerprint"))
        ):
            return f"{step_id}:step_proof_identity_mismatch"
        if (
            _core._text(node_receipt.get("source_step_id")) != step_id
            or not _core._text(node_receipt.get("receipt_id"))
            or _core._stable_hash(node_receipt)
            != _core._stable_hash(row_receipt)
            or row.get("cleanup_execution_receipt_identity_valid")
            is not True
        ):
            return f"{step_id}:cleanup_receipt_identity_mismatch"
        if (
            row.get("graph_cleanup_receipt_identity_valid") is not True
            or _receipt_step_id(graph_receipt) != step_id
            or not _core._text(graph_receipt.get("receipt_id"))
        ):
            return f"{step_id}:graph_cleanup_receipt_identity_mismatch"
        graph_status = _core._text(graph_receipt.get("status")).upper()
        if graph_status != _core._text(
            row.get("rollback_outcome")
        ).upper():
            return f"{step_id}:rollback_outcome_mismatch"
        node_status = _core._text(node_receipt.get("status")).upper()
        if graph_status == "COMPLETED" and node_status != "ACCEPTED":
            return f"{step_id}:completed_receipt_not_accepted"
        if graph_status == "NOT_REQUIRED" and node_status != "NOT_REQUIRED":
            return f"{step_id}:not_required_receipt_mismatch"
        if graph_status in {"FAILED", "BLOCKED"} and node_status in {
            "ACCEPTED",
            "NOT_REQUIRED",
        }:
            return f"{step_id}:failed_receipt_contradiction"
        source_receipt_ids.append(_core._text(node_receipt.get("receipt_id")))

    if list(_core._list(execution.get("source_receipt_ids"))) != source_receipt_ids:
        return "source_receipt_order_mismatch"
    attached = _core._text(execution.get("scope_fingerprint"))
    computed = build_process_graph_cleanup_scope_fingerprint(execution)
    if not attached or attached != computed:
        return f"scope_fingerprint_mismatch:{attached}:{computed}"
    return ""


def _zero_transport_receipt(
    step_id: str,
    node_proof: dict[str, Any],
    row: dict[str, Any],
) -> dict[str, Any]:
    graph_receipt = _core._dict(row.get("graph_cleanup_receipt"))
    evidence = _core._dict(graph_receipt.get("evidence"))
    cleanup_receipt = _core._dict(
        row.get("cleanup_execution_receipt")
    )
    valid = bool(
        row.get("source_step_identity_valid") is not True
        and row.get("cleanup_step_identity_valid") is True
        and row.get("graph_cleanup_receipt_identity_valid") is True
        and row.get("cleanup_execution_receipt_identity_valid") is True
        and _core._text(row.get("rollback_outcome")).upper()
        == "NOT_REQUIRED"
        and _core._text(graph_receipt.get("status")).upper()
        == "NOT_REQUIRED"
        and _receipt_step_id(graph_receipt) == step_id
        and _core._text(evidence.get("reason_code"))
        == GRAPH_SOURCE_WRITE_NOT_REACHED
        and evidence.get("request_reached_transport") is False
        and int(evidence.get("effectful_write_count") or 0) == 0
        and int(evidence.get("cleanup_write_count") or 0) == 0
        and _core._text(cleanup_receipt.get("status")).upper()
        == "NOT_REQUIRED"
        and cleanup_receipt.get("attempted") is False
        and cleanup_receipt.get("transport_reached") is False
        and not _core._dict(row.get("before_observation"))
        and not _core._dict(row.get("after_write_observation"))
        and not _core._dict(row.get("after_cleanup_observation"))
    )
    if not valid:
        return {}
    proof_id = _core._text(node_proof.get("proof_id"))
    return {
        "schema_version": "qualibug.cleanup-equivalence-receipt.v1",
        "receipt_id": "ceq_"
        + _core._stable_hash(
            {
                "proof_id": proof_id,
                "step_id": step_id,
                "graph_receipt_id": _core._text(
                    graph_receipt.get("receipt_id")
                ),
                "cleanup_receipt_id": _core._text(
                    cleanup_receipt.get("receipt_id")
                ),
            }
        )[:32],
        "proof_id": proof_id,
        "source_step_id": step_id,
        "system_ref": _core._text(row.get("system_ref")),
        "equivalence_status": "NOT_APPLICABLE",
        "reason_code": GRAPH_SOURCE_WRITE_NOT_REACHED,
        "detail": "source_write_formally_proven_not_to_reach_transport",
        "transport_proven_absent": True,
        "graph_cleanup_receipt_id": _core._text(
            graph_receipt.get("receipt_id")
        ),
        "cleanup_execution_receipt_id": _core._text(
            cleanup_receipt.get("receipt_id")
        ),
    }


def _indeterminate_receipt(
    proof: dict[str, Any], execution_set: dict[str, Any], detail: str
) -> dict[str, Any]:
    proof_id = _core._text(proof.get("proof_id"))
    execution_id = _core._text(execution_set.get("receipt_id"))
    receipt = {
        "schema_version": _core.GRAPH_CLEANUP_EQUIVALENCE_SCHEMA,
        "receipt_id": "pgceq_"
        + _core._stable_hash(
            {"proof_id": proof_id, "execution_id": execution_id, "detail": detail}
        )[:32],
        "proof_id": proof_id,
        "process_graph_write_contract_id": _core._text(
            proof.get("process_graph_write_contract_id")
        ),
        "cleanup_execution_receipt_id": execution_id,
        "write_step_ids": list(_core._list(proof.get("write_step_ids"))),
        "cleanup_order": list(_core._list(proof.get("cleanup_order"))),
        "equivalence_status": "INDETERMINATE",
        "reason_code": "PROCESS_GRAPH_CLEANUP_EQUIVALENCE_INDETERMINATE",
        "detail": f"{GRAPH_EQUIVALENCE_SCOPE_INVALID}:{detail}",
        "step_equivalence_receipts_by_id": {},
        "step_equivalence_receipt_ids": [],
        "equivalent_step_count": 0,
        "not_equivalent_step_count": 0,
        "indeterminate_step_count": len(
            _core._list(proof.get("write_step_ids"))
        ),
        "fingerprint": "",
    }
    receipt["fingerprint"] = _core._stable_hash(receipt)[:32]
    _core._update_environment_receipt(
        execution_set,
        equivalence_status="INDETERMINATE",
        reason_codes=[receipt["detail"]],
        step_receipt_ids=[],
    )
    _seal_environment_truth(
        execution_set,
        equivalence_status="INDETERMINATE",
    )
    return receipt


def evaluate_process_graph_cleanup_equivalence(
    *,
    proof: dict[str, Any],
    cleanup_execution_receipt: dict[str, Any],
) -> dict[str, Any]:
    graph_proof = _core._dict(proof)
    execution_set = _core._dict(cleanup_execution_receipt)
    error = _scope_error(graph_proof, execution_set)
    if error:
        return _indeterminate_receipt(graph_proof, execution_set, error)

    aggregate = _core.evaluate_process_graph_cleanup_equivalence(
        proof=graph_proof,
        cleanup_execution_receipt=execution_set,
    )
    write_ids = [
        _core._text(value)
        for value in _core._list(graph_proof.get("write_step_ids"))
        if _core._text(value)
    ]
    inputs = _core._dict(execution_set.get("step_inputs_by_id"))
    proofs = _core._dict(graph_proof.get("step_proofs_by_id"))
    node_receipts = deepcopy(
        _core._dict(aggregate.get("step_equivalence_receipts_by_id"))
    )
    changed = False
    for step_id in write_ids:
        replacement = _zero_transport_receipt(
            step_id,
            _core._dict(proofs.get(step_id)),
            _core._dict(inputs.get(step_id)),
        )
        if replacement:
            node_receipts[step_id] = replacement
            changed = True
    if not changed:
        _seal_environment_truth(
            execution_set,
            equivalence_status=_core._text(
                aggregate.get("equivalence_status")
            ).upper(),
        )
        return aggregate

    statuses = {
        step_id: _core._text(
            _core._dict(node_receipts.get(step_id)).get("equivalence_status")
        ).upper()
        for step_id in write_ids
    }
    values = [statuses.get(step_id, "") for step_id in write_ids]
    if any(value == "NOT_EQUIVALENT" for value in values):
        final_status = "NOT_EQUIVALENT"
    elif any(
        value not in {"EQUIVALENT", "NOT_APPLICABLE"} for value in values
    ):
        final_status = "INDETERMINATE"
    else:
        final_status = "EQUIVALENT"
    receipt_ids = [
        _core._text(_core._dict(node_receipts.get(step_id)).get("receipt_id"))
        for step_id in write_ids
        if _core._text(
            _core._dict(node_receipts.get(step_id)).get("receipt_id")
        )
    ]
    reasons = [
        f"{step_id}:{_core._text(_core._dict(node_receipts.get(step_id)).get('reason_code'))}"
        for step_id in write_ids
        if _core._text(
            _core._dict(node_receipts.get(step_id)).get("reason_code")
        )
    ]
    aggregate.update(
        {
            "receipt_id": "pgceq_"
            + _core._stable_hash(
                {
                    "proof_id": _core._text(graph_proof.get("proof_id")),
                    "execution_id": _core._text(execution_set.get("receipt_id")),
                    "scope_fingerprint": _core._text(
                        execution_set.get("scope_fingerprint")
                    ),
                    "receipt_ids": receipt_ids,
                    "status": final_status,
                }
            )[:32],
            "equivalence_status": final_status,
            "reason_code": (
                ""
                if final_status == "EQUIVALENT"
                else (
                    "PROCESS_GRAPH_CLEANUP_NOT_EQUIVALENT"
                    if final_status == "NOT_EQUIVALENT"
                    else "PROCESS_GRAPH_CLEANUP_EQUIVALENCE_INDETERMINATE"
                )
            ),
            "detail": ";".join(reasons),
            "step_equivalence_receipts_by_id": node_receipts,
            "step_equivalence_receipt_ids": receipt_ids,
            "step_equivalence_status_by_id": statuses,
            "equivalent_step_count": sum(
                value in {"EQUIVALENT", "NOT_APPLICABLE"} for value in values
            ),
            "not_equivalent_step_count": sum(
                value == "NOT_EQUIVALENT" for value in values
            ),
            "indeterminate_step_count": sum(
                value
                not in {"EQUIVALENT", "NOT_APPLICABLE", "NOT_EQUIVALENT"}
                for value in values
            ),
            "fingerprint": "",
        }
    )
    aggregate["fingerprint"] = _core._stable_hash(
        {
            "receipt_id": aggregate["receipt_id"],
            "proof_id": aggregate["proof_id"],
            "equivalence_status": final_status,
            "receipt_ids": receipt_ids,
            "statuses": statuses,
        }
    )[:32]
    _core._update_environment_receipt(
        execution_set,
        equivalence_status=final_status,
        reason_codes=reasons,
        step_receipt_ids=receipt_ids,
    )
    _seal_environment_truth(
        execution_set,
        equivalence_status=final_status,
    )
    return aggregate


__all__ = sorted(
    {
        *[name for name in dir(_core) if not name.startswith("__")],
        "GRAPH_EQUIVALENCE_SCOPE_INVALID",
        "GRAPH_SOURCE_WRITE_NOT_REACHED",
        "build_process_graph_cleanup_scope_fingerprint",
        "finalize_process_graph_cleanup_equivalence_inputs",
        "evaluate_process_graph_cleanup_equivalence",
    }
)
