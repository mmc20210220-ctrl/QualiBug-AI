"""Mainline wrapper for the exact operation-causality projection."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .database_observer_runtime import _sensitive_field
from .database_relation_delta_causality_oracle import OBSERVER_ID
from .database_relation_delta_causality_projection import (
    ASSERTION_KIND,
    project_database_relation_delta_causality as _project,
)

_BLOCK_REASON = "BLOCKED_DATABASE_RELATION_CAUSALITY_BINDING_INVALID"


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _sensitive_block(
    experiment: dict[str, Any],
    assertion: dict[str, Any],
) -> dict[str, Any]:
    row = deepcopy(experiment)
    causal = _dict(assertion.get("causal_attribution_contract"))
    receipt = _dict(row.get("compile_receipt"))
    receipt.update(
        {
            "status": "BLOCKED",
            "reason_code": _BLOCK_REASON,
            "database_relation_causality_detail": {
                "assertion_id": _text(assertion.get("assertion_id")),
                "causality_reason_code": (
                    "DATABASE_RELATION_CAUSAL_SENSITIVE_FIELD_FORBIDDEN"
                ),
                "child_database_field_id": _text(
                    causal.get("child_database_field_id")
                ),
                "child_database_field_name": _text(
                    causal.get("child_database_field_name")
                ),
                "raw_causal_value_allowed": False,
                "automatic_field_mapping_allowed": False,
            },
        }
    )
    row["compile_receipt"] = receipt
    row["compile_status"] = "BLOCKED"
    row["database_relation_causality_projection_status"] = "BLOCKED"
    return row


def project_database_relation_delta_causality(
    experiment_pack: dict[str, Any],
) -> dict[str, Any]:
    projected = _project(experiment_pack)
    experiments: list[dict[str, Any]] = []
    newly_blocked: list[dict[str, Any]] = []
    for raw in _list(projected.get("experiments")):
        if not isinstance(raw, dict):
            continue
        experiment = deepcopy(raw)
        causal_assertions = [
            dict(row)
            for row in _list(experiment.get("assertions"))
            if isinstance(row, dict) and _text(row.get("kind")) == ASSERTION_KIND
        ]
        sensitive = next(
            (
                assertion
                for assertion in causal_assertions
                if _sensitive_field(
                    _text(
                        _dict(
                            assertion.get("causal_attribution_contract")
                        ).get("child_database_field_name")
                    )
                )
            ),
            None,
        )
        if sensitive is not None:
            newly_blocked.append(_sensitive_block(experiment, sensitive))
            continue
        if causal_assertions:
            observers = [
                dict(row)
                for row in _list(experiment.get("observers"))
                if isinstance(row, dict)
            ]
            if OBSERVER_ID not in {
                _text(row.get("observer_id")) for row in observers
            }:
                observers.append(
                    {
                        "observer_id": OBSERVER_ID,
                        "adapter": "process_ledger",
                    }
                )
            experiment["observers"] = observers

            runtime_contract = _dict(
                experiment.get("field_oracle_runtime_contract")
            )
            if not runtime_contract:
                runtime_contract = {
                    "schema_version": (
                        "qualibug.field-oracle-runtime-contract.v1"
                    ),
                    "status": "RESOLVED",
                }
            runtime_contract.update(
                {
                    "status": "RESOLVED",
                    "assertion_kind": ASSERTION_KIND,
                    "database_relation_delta_bound": True,
                    "operation_causality_bound": True,
                    "operation_causality_observer_id": OBSERVER_ID,
                    "causal_attribution_mode": "EXACT_REQUEST_CORRELATION",
                    "timestamp_window_attribution_allowed": False,
                    "response_generated_identifier_allowed": False,
                }
            )
            experiment["field_oracle_runtime_contract"] = runtime_contract

            receipt = _dict(experiment.get("compile_receipt"))
            receipt.update(
                {
                    "operation_causality_observer_required": True,
                    "operation_causality_observer_id": OBSERVER_ID,
                    "field_oracle_assertion_kind": ASSERTION_KIND,
                    "operation_causality_assertion_count": len(
                        causal_assertions
                    ),
                    "operation_causality_runtime_contract_resolved": True,
                }
            )
            experiment["compile_receipt"] = receipt
        experiments.append(experiment)

    existing_blocked = [
        dict(row)
        for row in _list(projected.get("blocked_experiments"))
        if isinstance(row, dict)
    ]
    all_blocked = [*existing_blocked, *newly_blocked]
    reason_counts = dict(_dict(projected.get("block_reason_counts")))
    if newly_blocked:
        reason_counts[_BLOCK_REASON] = reason_counts.get(_BLOCK_REASON, 0) + len(
            newly_blocked
        )
    projected["experiments"] = experiments
    projected["blocked_experiments"] = all_blocked
    projected["compiled_count"] = len(experiments)
    projected["blocked_count"] = len(all_blocked)
    projected["block_reason_counts"] = reason_counts
    summary = _dict(
        projected.get("database_relation_delta_causality_projection")
    )
    if newly_blocked:
        summary["status"] = "BLOCKED"
        summary["sensitive_field_block_count"] = len(newly_blocked)
        summary["newly_blocked_experiment_count"] = int(
            summary.get("newly_blocked_experiment_count") or 0
        ) + len(newly_blocked)
    projected["database_relation_delta_causality_projection"] = summary
    return projected


__all__ = ["project_database_relation_delta_causality"]
