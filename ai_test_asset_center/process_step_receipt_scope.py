"""Public exact-step receipt scope over aggregate-aware cleanup evidence.

The original exact-scope binder remains unchanged in
``process_step_receipt_scope_core``. This facade separates graph aggregate
cleanup receipts, which belong to the Receipt Bundle, from exact per-step
cleanup execution and verification receipts, which belong to ProcessStepLedger.
It also materializes one exact invocation-lineage receipt per required executed
step from the immutable Contract Oracle receipt. No total receipt is broadcast.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from . import process_step_receipt_scope_core as _core


for _name in dir(_core):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_core, _name)


PROCESS_STEP_ORACLE_INVOCATION_SCHEMA = (
    "qualibug.process-step-oracle-invocation.v1"
)
_GRAPH_AGGREGATE_SCHEMAS = frozenset(
    {
        "qualibug.process-graph-cleanup-execution-set.v1",
        "qualibug.process-graph-cleanup-equivalence-receipt.v1",
    }
)


def _stable_hash(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _receipt_schema(receipt: dict[str, Any]) -> str:
    row = _core._dict(receipt)
    payload = _core._dict(row.get("payload"))
    return _core._text(
        row.get("schema_version") or payload.get("schema_version")
    )


def _is_graph_aggregate(receipt: dict[str, Any]) -> bool:
    return _receipt_schema(receipt) in _GRAPH_AGGREGATE_SCHEMAS


def _raw_rows(
    observations: dict[str, Any],
    keys: tuple[str, ...],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in keys:
        value = observations.get(key)
        if isinstance(value, dict):
            rows.append(value)
        else:
            rows.extend(
                row
                for row in _core._list(value)
                if isinstance(row, dict)
            )
    return rows


def _required_executed_step_ids(ledger: Any) -> list[str]:
    required = [
        _core._text(value)
        for value in list(getattr(ledger, "required_step_ids", []) or [])
        if _core._text(value)
    ]
    executed = {
        _core._text(value)
        for value in (
            ledger.executed_step_ids()
            if hasattr(ledger, "executed_step_ids")
            else []
        )
        if _core._text(value)
    }
    return [step_id for step_id in required if step_id in executed]


def _build_step_oracle_invocation(
    *,
    step_id: str,
    oracle_receipt: dict[str, Any],
) -> dict[str, Any]:
    oracle_status = _core._text(oracle_receipt.get("status")).upper()
    payload = {
        "schema_version": PROCESS_STEP_ORACLE_INVOCATION_SCHEMA,
        "step_id": _core._text(step_id),
        "source_oracle_receipt_id": _core.receipt_id(oracle_receipt),
        "oracle_status": oracle_status,
        "oracle_verdict": _core._text(oracle_receipt.get("verdict")),
        "evaluated": oracle_status in {"PROPERTY_HELD", "VIOLATION"},
        "activation_receipt_id": _core._text(
            oracle_receipt.get("activation_receipt_id")
        ),
        "assertion_receipt_ids": [
            _core._text(value)
            for value in _core._list(
                oracle_receipt.get("assertion_receipt_ids")
            )
            if _core._text(value)
        ],
    }
    return {
        **payload,
        "receipt_id": "poi_" + _stable_hash(payload)[:24],
    }


def _materialize_step_oracle_invocations(
    ledger: Any,
    source: dict[str, Any],
) -> list[dict[str, Any]]:
    existing = _core._deduplicate_receipts(
        _raw_rows(
            source,
            (
                "oracle_invocation_receipts",
                "process_step_oracle_receipts",
            ),
        )
    )
    targets = _required_executed_step_ids(ledger)
    if not targets:
        return existing

    covered: set[str] = set()
    for row in existing:
        scope = _core.extract_receipt_step_scope(
            row,
            known_step_ids=targets,
        )
        if scope.get("status") == "EXACT":
            covered.add(_core._text(scope.get("step_id")))

    oracle_receipt = _core._dict(source.get("oracle_verdict"))
    if not _core.receipt_id(oracle_receipt):
        return existing

    generated = [
        _build_step_oracle_invocation(
            step_id=step_id,
            oracle_receipt=oracle_receipt,
        )
        for step_id in targets
        if step_id not in covered
    ]
    materialized = _core._deduplicate_receipts([*existing, *generated])
    _core._publish_rows(
        source,
        "oracle_invocation_receipts",
        materialized,
    )
    return materialized


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
    return rows


def _conflicting_receipt_ids(
    rows: list[dict[str, Any]],
) -> tuple[set[str], list[dict[str, Any]]]:
    owners: dict[str, set[str]] = {}
    for row in rows:
        receipt_id = _core.receipt_id(row)
        scope = _core.extract_receipt_step_scope(row)
        step_id = _core._text(scope.get("step_id"))
        if (
            receipt_id
            and scope.get("status") == "EXACT"
            and step_id
        ):
            owners.setdefault(receipt_id, set()).add(step_id)
    conflicts = {
        receipt_id
        for receipt_id, step_ids in owners.items()
        if len(step_ids) > 1
    }
    unbound = [
        {
            "receipt_id": receipt_id,
            "status": "RECEIPT_REUSED_ACROSS_STEPS",
            "step_id": "",
            "declared_step_ids": sorted(owners[receipt_id]),
            "explicit_scalar_step_ids": sorted(owners[receipt_id]),
            "explicit_list_step_ids": [],
            "evidence_kind": "cleanup",
        }
        for receipt_id in sorted(conflicts)
    ]
    return conflicts, unbound


def _partition_cleanup_receipts(
    source: dict[str, Any],
) -> dict[str, Any]:
    execution_rows = _raw_rows(
        source,
        ("cleanup_execution_receipts", "cleanup_execution_receipt"),
    )
    verification_rows = _raw_rows(
        source,
        (
            "cleanup_verification_receipts",
            "cleanup_verification",
            "cleanup_equivalence_receipt",
        ),
    )
    execution_aggregates = _core._deduplicate_receipts(
        [row for row in execution_rows if _is_graph_aggregate(row)]
    )
    verification_aggregates = _core._deduplicate_receipts(
        [row for row in verification_rows if _is_graph_aggregate(row)]
    )
    aggregates = _core._deduplicate_receipts(
        [*execution_aggregates, *verification_aggregates]
    )

    explicit_execution = _raw_rows(
        source,
        (
            "process_step_cleanup_execution_receipts",
            "process_graph_step_cleanup_execution_receipts",
            "process_graph_cleanup_receipts",
        ),
    )
    explicit_verification = _raw_rows(
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

    raw_exact_execution = [
        *[
            row
            for row in execution_rows
            if not _is_graph_aggregate(row)
        ],
        *explicit_execution,
        *nested_execution,
    ]
    raw_exact_verification = [
        *[
            row
            for row in verification_rows
            if not _is_graph_aggregate(row)
        ],
        *explicit_verification,
        *nested_verification,
    ]
    conflicts, conflict_unbound = _conflicting_receipt_ids(
        [*raw_exact_execution, *raw_exact_verification]
    )
    exact_execution = _core._deduplicate_receipts(
        [
            row
            for row in raw_exact_execution
            if _core.receipt_id(row) not in conflicts
        ]
    )
    exact_verification = _core._deduplicate_receipts(
        [
            row
            for row in raw_exact_verification
            if _core.receipt_id(row) not in conflicts
        ]
    )
    return {
        "execution_aggregates": execution_aggregates,
        "verification_aggregates": verification_aggregates,
        "aggregates": aggregates,
        "exact_execution": exact_execution,
        "exact_verification": exact_verification,
        "conflicting_receipt_ids": sorted(conflicts),
        "conflict_unbound": conflict_unbound,
    }


def _projection(
    source: dict[str, Any],
    *,
    exact_execution: list[dict[str, Any]],
    exact_verification: list[dict[str, Any]],
) -> dict[str, Any]:
    projected = dict(source)
    projected.pop("oracle_verdict", None)
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


def _merge_conflict_audit(
    audit: dict[str, Any],
    partition: dict[str, Any],
) -> dict[str, Any]:
    conflict_unbound = [
        dict(row)
        for row in partition["conflict_unbound"]
        if isinstance(row, dict)
    ]
    if not conflict_unbound:
        return audit
    cleanup = dict(_core._dict(audit.get("cleanup")))
    cleanup["duplicate_receipt_ids"] = sorted(
        {
            *[
                _core._text(value)
                for value in _core._list(
                    cleanup.get("duplicate_receipt_ids")
                )
                if _core._text(value)
            ],
            *partition["conflicting_receipt_ids"],
        }
    )
    cleanup["unbound"] = [
        *[
            dict(row)
            for row in _core._list(cleanup.get("unbound"))
            if isinstance(row, dict)
        ],
        *conflict_unbound,
    ]
    cleanup["complete"] = False
    return {
        **audit,
        "cleanup": cleanup,
        "unbound_receipts": [
            *[
                dict(row)
                for row in _core._list(
                    audit.get("unbound_receipts")
                )
                if isinstance(row, dict)
            ],
            *conflict_unbound,
        ],
        "complete": False,
    }


def synchronize_scoped_receipts_from_observations(
    ledger: Any,
    observations: dict[str, Any] | None,
) -> dict[str, Any]:
    """Bind exact step receipts and keep graph aggregates bundle-scoped."""

    source = observations if isinstance(observations, dict) else {}
    oracle_invocations = _materialize_step_oracle_invocations(ledger, source)
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
    audit = _merge_conflict_audit(audit, partition)

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
        graph_cleanup_context = bool(
            partition["execution_aggregates"]
            or partition["verification_aggregates"]
        )
        _core._publish_rows(
            observations,
            "cleanup_verification_receipts",
            (
                partition["verification_aggregates"]
                if graph_cleanup_context
                else partition["exact_verification"]
            ),
        )

    aggregate_ids = [
        _core.receipt_id(row)
        for row in partition["aggregates"]
        if _core.receipt_id(row)
    ]
    generated_oracle_ids = [
        _core.receipt_id(row)
        for row in oracle_invocations
        if _receipt_schema(row) == PROCESS_STEP_ORACLE_INVOCATION_SCHEMA
        and _core.receipt_id(row)
    ]
    audit = {
        **audit,
        "materialized_oracle_invocation_receipt_ids": generated_oracle_ids,
        "materialized_oracle_invocation_receipt_count": len(generated_oracle_ids),
        "oracle_verdict_excluded_from_step_scope": True,
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
        "conflicting_cleanup_receipt_ids": list(
            partition["conflicting_receipt_ids"]
        ),
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
        "PROCESS_STEP_ORACLE_INVOCATION_SCHEMA",
        "synchronize_scoped_receipts_from_observations",
    }
)
