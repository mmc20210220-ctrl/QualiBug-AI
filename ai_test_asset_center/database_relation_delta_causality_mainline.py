"""Mainline wrapper for the exact operation-causality projection."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .database_relation_delta_causality_oracle import OBSERVER_ID
from .database_relation_delta_causality_projection import (
    ASSERTION_KIND,
    project_database_relation_delta_causality as _project,
)


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
        has_causal = any(
            isinstance(row, dict) and _text(row.get("kind")) == ASSERTION_KIND
            for row in _list(experiment.get("assertions"))
        )
        if has_causal:
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
            receipt = dict(experiment.get("compile_receipt") or {})
            receipt["operation_causality_observer_required"] = True
            receipt["operation_causality_observer_id"] = OBSERVER_ID
            experiment["compile_receipt"] = receipt
        experiments.append(experiment)
    projected["experiments"] = experiments
    return projected


__all__ = ["project_database_relation_delta_causality"]
