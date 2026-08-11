"""Public cleanup equivalence authority.

Ordinary experiments use the single ``cleanup_equivalence_core`` evaluator. Before a
created-entity absence check, this facade narrows common collection envelopes to the exact runtime
identity only when the declared identity is fully bound and every identity field is present with a
comparable value type on a candidate row. The core still owns every equivalence verdict and keeps
its three-phase, fail-closed evidence requirements.

A process-graph proof set is evaluated by applying that same core engine to each source step and
aggregating only the resulting formal receipts.

The strict graph authority requires graph cleanup receipts for every non-transport conclusion. A
narrow compatibility path remains for completed cleanup rows that already carry one exact governed
transport/readback receipt and one matching ACCEPTED Cleanup Execution Receipt per source step.
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


def __getattr__(name: str) -> Any:
    # Lazy delegation to cleanup_equivalence_core. The former wholesale
    # ``dir(_core)`` copy re-entered the finalizer import cycle during
    # partial initialization (AttributeError on half-built modules) —
    # delegation resolves any name only when the module graph is complete.
    if not name.startswith("__"):
        return getattr(_core, name)
    raise AttributeError(name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_core)))


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _legacy_graph_scope_identity_valid(
    execution_set: dict[str, Any],
) -> bool:
    """Validate the governed identity envelope of the old graph API shape.

    Observation completeness is intentionally not part of this gate. Once every
    source/cleanup identity and ACCEPTED transport receipt is proven, the existing
    per-step equivalence engine owns whether each step is EQUIVALENT or
    INDETERMINATE. This prevents one missing observation in one system from
    erasing measured cleanup truth for every other system in the graph.
    """
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


_COLLECTION_ENVELOPE_KEYS = (
    "items",
    "rows",
    "results",
    "records",
    "data",
)


def _runtime_identity(
    proof: dict[str, Any],
    runtime_bindings: dict[str, Any],
) -> dict[str, Any]:
    identity_contract = _dict(proof.get("identity_contract"))
    fields = [
        _text(field)
        for field in _list(identity_contract.get("identity_fields"))
        if _text(field)
    ]
    if not fields or any(
        field not in runtime_bindings
        or runtime_bindings[field] in (None, "")
        for field in fields
    ):
        return {}
    return {field: runtime_bindings[field] for field in fields}


def _row_matches_identity(
    row: dict[str, Any],
    identity: dict[str, Any],
) -> bool | None:
    if not identity or any(field not in row for field in identity):
        return None
    if any(type(row[field]) is not type(expected) for field, expected in identity.items()):
        return None
    return all(row.get(field) == expected for field, expected in identity.items())


def _identity_scoped_collection_observation(
    observation: dict[str, Any],
    identity: dict[str, Any],
) -> dict[str, Any]:
    """Narrow one collection envelope to one complete runtime identity.

    An empty collection proves global absence. A non-empty collection is narrowed only when at
    least one row exposes every bound identity field with the same JSON value types; otherwise the
    original observation is preserved so the core remains fail-closed rather than guessing from
    partial or type-coerced identity.
    """
    original = _dict(observation)
    body = original.get("body")
    if not isinstance(body, dict):
        return original

    collection_key = next(
        (
            key
            for key in _COLLECTION_ENVELOPE_KEYS
            if isinstance(body.get(key), list)
        ),
        "",
    )
    if not collection_key:
        return original
    rows = list(body.get(collection_key) or [])
    if not rows:
        projected = deepcopy(original)
        projected["body"] = []
        projected["identity_scope_projection"] = {
            "status": "EMPTY_COLLECTION",
            "collection_key": collection_key,
            "raw_rows_returned": False,
        }
        return projected
    if not identity:
        return original

    matched: list[dict[str, Any]] = []
    comparable_row_count = 0
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        verdict = _row_matches_identity(raw, identity)
        if verdict is None:
            continue
        comparable_row_count += 1
        if verdict:
            matched.append(raw)
    if not comparable_row_count:
        return original

    projected = deepcopy(original)
    projected["body"] = matched
    projected["identity_scope_projection"] = {
        "status": "PROJECTED",
        "collection_key": collection_key,
        "source_row_count": len(rows),
        "comparable_row_count": comparable_row_count,
        "matched_row_count": len(matched),
        "identity_fields": sorted(identity),
        "raw_rows_returned": False,
    }
    return projected


def _ordinary_observations(
    *,
    proof: dict[str, Any],
    before_observation: dict[str, Any],
    after_write_observation: dict[str, Any],
    after_cleanup_observation: dict[str, Any],
    runtime_bindings: dict[str, Any],
    cleanup_execution_receipt: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    equivalence_contract = _dict(proof.get("equivalence_contract"))
    mode = _core._effective_equivalence_mode(
        _text(equivalence_contract.get("mode")),
        cleanup_execution_receipt,
    )
    if mode != "created_entity_absent":
        return (
            before_observation,
            after_write_observation,
            after_cleanup_observation,
        )
    identity = _runtime_identity(proof, runtime_bindings)
    before = _identity_scoped_collection_observation(
        before_observation,
        identity,
    )
    after_write = _identity_scoped_collection_observation(
        after_write_observation,
        identity,
    )
    after_cleanup = _identity_scoped_collection_observation(
        after_cleanup_observation,
        identity,
    )
    return before, after_write, after_cleanup


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
            and _legacy_graph_scope_identity_valid(
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

    before, after_write, after_cleanup = _ordinary_observations(
        proof=proof,
        before_observation=before_observation,
        after_write_observation=after_write_observation,
        after_cleanup_observation=after_cleanup_observation,
        runtime_bindings=runtime_bindings,
        cleanup_execution_receipt=cleanup_execution_receipt,
    )
    return _core.evaluate_cleanup_equivalence(
        proof=proof,
        before_observation=before,
        after_write_observation=after_write,
        after_cleanup_observation=after_cleanup,
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
