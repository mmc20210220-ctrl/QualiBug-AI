"""Public cleanup equivalence authority.

Ordinary experiments delegate byte-for-byte to ``cleanup_equivalence_core``.
A process-graph proof set is evaluated by applying that same core engine to each
source step and aggregating only the resulting formal receipts.

The strict graph authority requires graph cleanup receipts for every non-
transport conclusion. A narrow compatibility path remains for completed cleanup
rows that already carry one exact governed transport/readback receipt and one
matching ACCEPTED Cleanup Execution Receipt per source step.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from . import cleanup_equivalence_core as _core
from . import process_graph_cleanup_equivalence_core as _graph_core
from .process_graph_cleanup_equivalence import (
    GRAPH_EQUIVALENCE_SCOPE_INVALID,
    evaluate_process_graph_cleanup_equivalence,
)
from .process_graph_reversibility import (
    is_process_graph_reversibility_proof,
)


for _name in dir(_core):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_core, _name)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _legacy_completed_graph_scope(
    execution_set: dict[str, Any],
) -> bool:
    """Accept only complete, effectful cleanup evidence from the old API shape."""
    row = _dict(execution_set)
    if _text(row.get("schema_version")) != (
        _graph_core.GRAPH_CLEANUP_EXECUTION_SET_SCHEMA
    ):
        return False
    write_ids = [
        _text(value)
        for value in _list(row.get("write_step_ids"))
        if _text(value)
    ]
    inputs = _dict(row.get("step_inputs_by_id"))
    receipts = _dict(
        row.get("step_cleanup_execution_receipts_by_id")
    )
    if (
        not write_ids
        or len(write_ids) != len(set(write_ids))
        or set(inputs) != set(write_ids)
        or set(receipts) != set(write_ids)
    ):
        return False

    expected_receipt_ids: list[str] = []
    for step_id in write_ids:
        step_input = _dict(inputs.get(step_id))
        node_receipt = _dict(receipts.get(step_id))
        input_receipt = _dict(
            step_input.get("cleanup_execution_receipt")
        )
        if (
            step_input.get("source_step_identity_valid") is not True
            or step_input.get("cleanup_step_identity_valid") is not True
            or _dict(step_input.get("graph_cleanup_receipt"))
            or _text(step_input.get("rollback_outcome"))
            or _text(node_receipt.get("source_step_id")) != step_id
            or _text(node_receipt.get("status")).upper() != "ACCEPTED"
            or node_receipt.get("attempted") is not True
            or node_receipt.get("transport_reached") is not True
            or node_receipt.get("succeeded") is not True
            or not 200 <= int(node_receipt.get("status_code") or 0) < 300
            or node_receipt != input_receipt
            or not _dict(step_input.get("before_observation"))
            or not _dict(step_input.get("after_write_observation"))
            or not _dict(step_input.get("after_cleanup_observation"))
        ):
            return False
        receipt_id = _text(node_receipt.get("receipt_id"))
        if not receipt_id:
            return False
        expected_receipt_ids.append(receipt_id)

    return list(_list(row.get("source_receipt_ids"))) == expected_receipt_ids


def _bind_graph_verification_outputs(
    execution_set: dict[str, Any],
    equivalence_receipt: dict[str, Any],
) -> None:
    """Bind per-step verification outputs onto their sealed execution set."""
    execution = _dict(execution_set)
    receipt = _dict(equivalence_receipt)
    if (
        _text(execution.get("schema_version"))
        != _graph_core.GRAPH_CLEANUP_EXECUTION_SET_SCHEMA
        or _text(receipt.get("schema_version"))
        != _graph_core.GRAPH_CLEANUP_EQUIVALENCE_SCHEMA
    ):
        return
    write_ids = [
        _text(value)
        for value in _list(execution.get("write_step_ids"))
        if _text(value)
    ]
    step_receipts = _dict(
        receipt.get("step_equivalence_receipts_by_id")
    )
    if (
        not write_ids
        or set(step_receipts) != set(write_ids)
        or list(_list(receipt.get("write_step_ids"))) != write_ids
        or _text(receipt.get("cleanup_execution_receipt_id"))
        != _text(execution.get("receipt_id"))
        or _text(receipt.get("proof_id"))
        != _text(execution.get("proof_id"))
        or _text(receipt.get("process_graph_write_contract_id"))
        != _text(execution.get("process_graph_write_contract_id"))
    ):
        return

    receipt_ids_by_id: dict[str, str] = {}
    for step_id in write_ids:
        row = _dict(step_receipts.get(step_id))
        receipt_id = _text(row.get("receipt_id"))
        if (
            _text(row.get("source_step_id") or step_id) != step_id
            or not receipt_id
        ):
            return
        receipt_ids_by_id[step_id] = receipt_id

    verification_payload = {
        "cleanup_execution_receipt_id": _text(execution.get("receipt_id")),
        "cleanup_execution_scope_fingerprint": _text(
            execution.get("scope_fingerprint")
        ),
        "cleanup_equivalence_receipt_id": _text(receipt.get("receipt_id")),
        "cleanup_equivalence_fingerprint": _text(receipt.get("fingerprint")),
        "write_step_ids": write_ids,
        "step_cleanup_verification_receipt_ids_by_id": receipt_ids_by_id,
        "step_cleanup_verification_receipts_by_id": {
            step_id: _dict(step_receipts.get(step_id))
            for step_id in write_ids
        },
    }
    verification_fingerprint = _graph_core._stable_hash(
        verification_payload
    )[:32]
    execution.update(
        {
            "cleanup_equivalence_receipt_id": verification_payload[
                "cleanup_equivalence_receipt_id"
            ],
            "cleanup_equivalence_fingerprint": verification_payload[
                "cleanup_equivalence_fingerprint"
            ],
            "step_cleanup_verification_receipt_ids_by_id": dict(
                receipt_ids_by_id
            ),
            "step_cleanup_verification_receipts_by_id": deepcopy(
                verification_payload[
                    "step_cleanup_verification_receipts_by_id"
                ]
            ),
            "cleanup_verification_fingerprint": verification_fingerprint,
        }
    )
    environment = _dict(
        execution.get("environment_restoration_receipt")
    )
    if environment:
        environment.update(
            {
                "aggregate_cleanup_equivalence_receipt_id": _text(
                    receipt.get("receipt_id")
                ),
                "cleanup_verification_fingerprint": (
                    verification_fingerprint
                ),
            }
        )
        execution["environment_restored"] = (
            environment.get("environment_restored") is True
        )


def evaluate_cleanup_equivalence(
    *,
    proof: dict[str, Any],
    before_observation: dict[str, Any],
    after_write_observation: dict[str, Any],
    after_cleanup_observation: dict[str, Any],
    runtime_bindings: dict[str, Any],
    cleanup_execution_receipt: dict[str, Any],
) -> dict[str, Any]:
    """Dispatch ordinary or per-source-step graph equivalence."""
    if is_process_graph_reversibility_proof(proof):
        result = evaluate_process_graph_cleanup_equivalence(
            proof=proof,
            cleanup_execution_receipt=cleanup_execution_receipt,
        )
        detail = _text(result.get("detail"))
        if (
            _text(result.get("equivalence_status")).upper()
            == "INDETERMINATE"
            and GRAPH_EQUIVALENCE_SCOPE_INVALID in detail
            and "graph_cleanup_receipt_identity_mismatch" in detail
            and _legacy_completed_graph_scope(
                cleanup_execution_receipt
            )
        ):
            result = _graph_core.evaluate_process_graph_cleanup_equivalence(
                proof=proof,
                cleanup_execution_receipt=cleanup_execution_receipt,
            )
        _bind_graph_verification_outputs(
            cleanup_execution_receipt,
            result,
        )
        return result
    return _core.evaluate_cleanup_equivalence(
        proof=proof,
        before_observation=before_observation,
        after_write_observation=after_write_observation,
        after_cleanup_observation=after_cleanup_observation,
        runtime_bindings=runtime_bindings,
        cleanup_execution_receipt=cleanup_execution_receipt,
    )


__all__ = sorted(
    {
        *[
            name
            for name in dir(_core)
            if not name.startswith("__")
        ],
        "evaluate_cleanup_equivalence",
    }
)
