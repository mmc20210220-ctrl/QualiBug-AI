"""Public exact-step receipt scope over aggregate-aware cleanup evidence.

The original exact-scope binder remains unchanged in
``process_step_receipt_scope_core``. This facade separates graph aggregate
cleanup receipts, which belong to the Receipt Bundle, from exact per-step
cleanup execution and verification receipts, which belong to ProcessStepLedger.
No step identity is inferred from aggregate ``write_step_ids``.
"""
from __future__ import annotations

from typing import Any

from . import process_step_receipt_scope_core as _core


for _name in dir(_core):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_core, _name)


_GRAPH_AGGREGATE_SCHEMAS = frozenset(
    {
        "qualibug.process-graph-cleanup-execution-set.v1",
        "qualibug.process-graph-cleanup-equivalence-receipt.v1",
    }
)


def _receipt_schema(receipt: dict[str, Any]) -> str:
    row = _core._dict(receipt)
    payload = _core._dict(row.get("payload"))
    return _core._text(
        row.get("schema_version") or payload.get("schema_version")
    )


def _is_graph_aggregate(receipt: dict[str, Any]) -> bool:
    return _receipt_schema(receipt) in _GRAPH_AGGREGATE_SCHEMAS


def _nested_receipts(
    aggregates: list[dict[str, Any]],
    *mapping_fields: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for aggregate in aggregates:
        for field in mapping_fields:
            mapping = _core._dict(
                _core._dict(aggregate).get(field)
            )
            rows.extend(
                value
                for _, value in sorted(mapping.items())
                if isinstance(value, dict)
            )
    return _core._deduplicate_receipts(rows)


def _partition_cleanup_receipts(
    source: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    execution_rows = _core._rows(
        source,
        ("cleanup_execution_receipts", "cleanup_execution_receipt"),
    )
    verification_rows = _core._rows(
        source,
        (
            "cleanup_verification_receipts",
            "cleanup_verification",
            "cleanup_equivalence_receipt",
        ),
    )
    execution_aggregates = [
        row for row in execution_rows if _is_graph_aggregate(row)
    ]
    verification_aggregates = [
        row for row in verification_rows if _is_graph_aggregate(row)
    ]
    aggregates = _core._deduplicate_receipts(
        [*execution_aggregates, *verification_aggregates]
    )

    explicit_execution = _core._rows(
        source,
        (
            "process_step_cleanup_execution_receipts",
            "process_graph_step_cleanup_execution_receipts",
            "process_graph_cleanup_receipts",
        ),
    )
    explicit_verification = _core._rows(
        source,
        (
            "process_step_cleanup_verification_receipts",
            "process_graph_step_cleanup_verification_receipts",
        ),
    )
    nested_execution = _nested_receipts(
        aggregates,
        "step_cleanup_execution_receipts_by_id",
    )
    nested_verification = _nested_receipts(
        aggregates,
        "step_cleanup_verification_receipts_by_id",
        "step_equivalence_receipts_by_id",
    )

    exact_execution = _core._deduplicate_receipts(
        [
            *[
                row
                for row in execution_rows
                if not _is_graph_aggregate(row)
            ],
            *explicit_execution,
            *nested_execution,
        ]
    )
    exact_verification = _core._deduplicate_receipts(
        [
            *[
                row
                for row in verification_rows
                if not _is_graph_aggregate(row)
            ],
            *explicit_verification,
            *nested_verification,
        ]
    )
    return {
        "execution_aggregates": execution_aggregates,
        "verification_aggregates": verification_aggregates,
        "aggregates": aggregates,
        "exact_execution": exact_execution,
        "exact_verification": exact_verification,
    }


def _projection(
    source: dict[str, Any],
    *,
    exact_execution: list[dict[str, Any]],
    exact_verification: list[dict[str, Any]],
) -> dict[str, Any]:
    projected = dict(source)
    for key in (
        "observer_receipts",
        "oracle_invocation_receipts",
        "oracle_trace_receipts",
    ):
        value = source.get(key)
        projected[key] = (
            list(value)
            if isinstance(value, list)
            else [value]
            if isinstance(value, dict)
            else []
        )
    projected["cleanup_execution_receipts"] = list(exact_execution)
    projected.pop("cleanup_execution_receipt", None)
    projected["cleanup_verification_receipts"] = list(exact_verification)
    projected.pop("cleanup_verification", None)
    projected.pop("cleanup_equivalence_receipt", None)
    return projected


def synchronize_scoped_receipts_from_observations(
    ledger: Any,
    observations: dict[str, Any] | None,
) -> dict[str, Any]:
    """Bind only exact per-step receipts; keep graph aggregates bundle-scoped."""

    source = observations if isinstance(observations, dict) else {}
    partition = _partition_cleanup_receipts(source)
    projected = _projection(
        source,
        exact_execution=partition["exact_execution"],
        exact_verification=partition["exact_verification"],
    )
    audit = _core.synchronize_scoped_receipts_from_observations(
        ledger,
        projected,
    )

    if isinstance(observations, dict):
        for key in (
            "observer_receipts",
            "oracle_invocation_receipts",
            "oracle_trace_receipts",
        ):
            _core._publish_rows(
                observations,
                key,
                [
                    row
                    for row in _core._list(projected.get(key))
                    if isinstance(row, dict)
                ],
            )

        _core._publish_rows(
            observations,
            "process_step_cleanup_execution_receipts",
            partition["exact_execution"],
        )
        _core._publish_rows(
            observations,
            "process_step_cleanup_verification_receipts",
            partition["exact_verification"],
        )

        if not partition["execution_aggregates"]:
            _core._publish_rows(
                observations,
                "cleanup_execution_receipts",
                partition["exact_execution"],
            )
        if not partition["verification_aggregates"]:
            _core._publish_rows(
                observations,
                "cleanup_verification_receipts",
                partition["exact_verification"],
            )

    aggregate_ids = [
        _core.receipt_id(row)
        for row in partition["aggregates"]
        if _core.receipt_id(row)
    ]
    audit = {
        **audit,
        "aggregate_cleanup_receipt_ids": aggregate_ids,
        "aggregate_cleanup_receipt_count": len(
            partition["aggregates"]
        ),
        "aggregate_cleanup_execution_receipt_ids": [
            _core.receipt_id(row)
            for row in partition["execution_aggregates"]
            if _core.receipt_id(row)
        ],
        "aggregate_cleanup_verification_receipt_ids": [
            _core.receipt_id(row)
            for row in partition["verification_aggregates"]
            if _core.receipt_id(row)
        ],
        "aggregate_cleanup_receipts_excluded_from_step_scope": True,
        "step_cleanup_execution_receipt_ids": [
            _core.receipt_id(row)
            for row in partition["exact_execution"]
            if _core.receipt_id(row)
        ],
        "step_cleanup_verification_receipt_ids": [
            _core.receipt_id(row)
            for row in partition["exact_verification"]
            if _core.receipt_id(row)
        ],
    }
    if isinstance(observations, dict):
        observations["process_step_receipt_scope_binding"] = audit
        observations["unbound_process_step_receipts"] = list(
            audit.get("unbound_receipts") or []
        )
    return audit


__all__ = sorted(
    {
        *[
            name
            for name in dir(_core)
            if not name.startswith("__")
        ],
        "synchronize_scoped_receipts_from_observations",
    }
)
