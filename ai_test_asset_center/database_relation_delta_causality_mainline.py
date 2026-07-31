"""Mainline wrapper for the exact operation-causality projection."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .database_relation_delta_causality_oracle import OBSERVER_ID
from .database_relation_delta_causality_projection import (
    ASSERTION_KIND,
    project_database_relation_delta_causality as _project,
)


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def project_database_relation_delta_causality(
    experiment_pack: dict[str, Any],
) -> dict[str, Any]:
    projected = _project(experiment_pack)
    experiments: list[dict[str, Any]] = []
    for raw in _list(projected.get("experiments")):
        if not isinstance(raw, dict):
            continue
        experiment = deepcopy(raw)
        causal_assertions = [
            dict(row)
            for row in _list(experiment.get("assertions"))
            if isinstance(row, dict) and _text(row.get("kind")) == ASSERTION_KIND
        ]
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
    projected["experiments"] = experiments
    return projected


__all__ = ["project_database_relation_delta_causality"]
