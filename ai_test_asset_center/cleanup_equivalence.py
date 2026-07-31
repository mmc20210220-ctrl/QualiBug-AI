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
            return _graph_core.evaluate_process_graph_cleanup_equivalence(
                proof=proof,
                cleanup_execution_receipt=cleanup_execution_receipt,
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
