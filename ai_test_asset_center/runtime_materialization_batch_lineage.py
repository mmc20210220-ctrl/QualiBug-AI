"""Preserve Runtime Materialization lineage across every formal batch outcome.

``execute_one_experiment`` has several correct fail-closed early returns before the Finalizer
(parameter/fixture/proof/observer/cleanup preflight).  The existing batch executor is the common
formal product entry for all of them.  This additive wrapper stamps the already-frozen, secret-free
materialization lineage on those outcomes and their delivery/evidence receipts without changing any
execution, Oracle, cleanup or delivery decision.
"""
from __future__ import annotations

import functools
import sys
from typing import Any

from . import experiment_batch_executor as _batch
from .runtime_materialization_experiment_bridge import (
    attach_materialization_lineage_to_result,
)

_INSTALL_MARKER = "__qualibug_runtime_materialization_batch_lineage_v1__"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _experiment_indexes(
    experiments_by_obligation: dict[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    by_obligation: dict[str, dict[str, Any]] = {}
    by_experiment: dict[str, dict[str, Any]] = {}
    for key, raw in _dict(experiments_by_obligation).items():
        if not isinstance(raw, dict):
            continue
        row = raw
        obligation_id = _text(row.get("obligation_id") or key)
        experiment_id = _text(row.get("experiment_id"))
        if obligation_id:
            by_obligation[obligation_id] = row
        if experiment_id:
            by_experiment[experiment_id] = row
    return by_obligation, by_experiment


def _experiment_for_row(
    row: dict[str, Any],
    *,
    by_obligation: dict[str, dict[str, Any]],
    by_experiment: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    for key in (
        "obligation_id",
        "executed_obligation_id",
        "selected_obligation_id",
    ):
        obligation_id = _text(row.get(key))
        if obligation_id and obligation_id in by_obligation:
            return by_obligation[obligation_id]
    experiment_id = _text(row.get("experiment_id"))
    return by_experiment.get(experiment_id, {})


def _stamp_receipt(receipt: Any, lineage: dict[str, Any]) -> Any:
    if not isinstance(receipt, dict) or not lineage:
        return receipt
    return {**receipt, "runtime_materialization_lineage": lineage}


def _stamp_outcome(row: dict[str, Any], experiment: dict[str, Any]) -> dict[str, Any]:
    stamped = attach_materialization_lineage_to_result(row, experiment=experiment)
    if not isinstance(stamped, dict):
        return row
    lineage = _dict(stamped.get("runtime_materialization_lineage"))
    if not lineage:
        return stamped
    for key in (
        "delivery_execution_receipt",
        "reproduction_receipt",
        "delivery_gate_receipt",
        "operational_receipt",
        "oracle_verdict",
    ):
        if isinstance(stamped.get(key), dict):
            stamped[key] = _stamp_receipt(stamped[key], lineage)
    for key in ("contract_evidence_receipts", "observer_receipts"):
        if isinstance(stamped.get(key), list):
            stamped[key] = [
                _stamp_receipt(item, lineage) if isinstance(item, dict) else item
                for item in stamped[key]
            ]
    return stamped


def attach_batch_materialization_lineage(
    result: Any,
    *,
    experiments_by_obligation: dict[str, dict[str, Any]],
) -> Any:
    if not isinstance(result, dict):
        return result
    output = dict(result)
    by_obligation, by_experiment = _experiment_indexes(experiments_by_obligation)

    stamped_results: list[Any] = []
    for raw in _list(output.get("results")):
        if not isinstance(raw, dict):
            stamped_results.append(raw)
            continue
        experiment = _experiment_for_row(
            raw,
            by_obligation=by_obligation,
            by_experiment=by_experiment,
        )
        stamped_results.append(_stamp_outcome(dict(raw), experiment))
    if isinstance(output.get("results"), list):
        output["results"] = stamped_results

    stamped_findings: list[Any] = []
    for raw in _list(output.get("findings")):
        if not isinstance(raw, dict):
            stamped_findings.append(raw)
            continue
        experiment = _experiment_for_row(
            raw,
            by_obligation=by_obligation,
            by_experiment=by_experiment,
        )
        stamped = _stamp_outcome({"finding": dict(raw)}, experiment)
        stamped_findings.append(stamped.get("finding", raw))
    if isinstance(output.get("findings"), list):
        output["findings"] = stamped_findings

    for collection in (
        "compile_results",
        "execution_results",
        "gate_results",
    ):
        rows = output.get(collection)
        if not isinstance(rows, dict):
            continue
        stamped_rows: dict[str, Any] = {}
        for key, raw in rows.items():
            if not isinstance(raw, dict):
                stamped_rows[key] = raw
                continue
            experiment = by_obligation.get(_text(key)) or _experiment_for_row(
                raw,
                by_obligation=by_obligation,
                by_experiment=by_experiment,
            )
            stamped_rows[key] = _stamp_outcome(dict(raw), _dict(experiment))
        output[collection] = stamped_rows
    return output


def install_runtime_materialization_batch_lineage() -> None:
    original = getattr(_batch, "execute_selected_experiments", None)
    if not callable(original) or getattr(original, _INSTALL_MARKER, False):
        return

    @functools.wraps(original)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        experiments = kwargs.get("experiments_by_obligation")
        if not isinstance(experiments, dict):
            experiments = {}
        return attach_batch_materialization_lineage(
            result,
            experiments_by_obligation=experiments,
        )

    setattr(wrapped, _INSTALL_MARKER, True)
    setattr(wrapped, "__qualibug_original__", original)
    _batch.execute_selected_experiments = wrapped
    # Hot reload/tests may have imported the compatibility alias first. Keep that alias on the
    # same existing batch authority instead of leaving two callable versions in one process.
    executor = sys.modules.get(f"{__package__}.experiment_executor")
    if executor is not None:
        executor.execute_selected_experiments = wrapped


__all__ = [
    "attach_batch_materialization_lineage",
    "install_runtime_materialization_batch_lineage",
]
