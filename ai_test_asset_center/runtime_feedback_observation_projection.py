"""Preserve governed runtime observations in the execution projection.

Runtime feedback consumes ``execution_results`` rather than raw executor outcomes.
Historically BLOCKED/HARNESS projections kept only reason/receipt ids, so already
observed readback paths disappeared before Runtime Fact Candidate extraction.
This module mirrors only receipt-backed evidence that already exists on the raw
outcome. It never changes execution status, creates observations, or elevates
runtime candidates to accepted facts.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _receipt_backed_rows(value: Any) -> list[dict[str, Any]]:
    """Return only evidence rows carrying an existing receipt identity."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in _list(value):
        if not isinstance(raw, dict):
            continue
        receipt_id = _text(raw.get("receipt_id"))
        if not receipt_id or receipt_id in seen:
            continue
        seen.add(receipt_id)
        rows.append(deepcopy(raw))
    return rows


def attach_runtime_feedback_observation_evidence(
    batch: dict[str, Any],
) -> dict[str, Any]:
    """Mirror governed raw-outcome evidence into ``execution_results``.

    The target projection row is located by ``selected_obligation_id`` first,
    matching the batch executor's identity index. Only evidence with an existing
    receipt id is copied. The Effect Observation Graph is copied only when it is
    receipt-backed. Existing projection evidence wins; this function is additive.
    """
    output = deepcopy(_dict(batch))
    execution_results = {
        _text(key): deepcopy(value)
        for key, value in _dict(output.get("execution_results")).items()
        if _text(key) and isinstance(value, dict)
    }
    projected_outcomes = 0
    projected_observers = 0
    projected_contracts = 0
    projected_graphs = 0

    for raw in _list(output.get("results")):
        if not isinstance(raw, dict):
            continue
        selected_id = _text(
            raw.get("selected_obligation_id") or raw.get("obligation_id")
        )
        if not selected_id or selected_id not in execution_results:
            continue
        target = execution_results[selected_id]
        changed = False

        observer_rows = _receipt_backed_rows(raw.get("observer_receipts"))
        if observer_rows and not _list(target.get("observer_receipts")):
            target["observer_receipts"] = observer_rows
            projected_observers += len(observer_rows)
            changed = True

        contract_rows = _receipt_backed_rows(raw.get("contract_evidence_receipts"))
        if contract_rows and not _list(target.get("contract_evidence_receipts")):
            target["contract_evidence_receipts"] = contract_rows
            projected_contracts += len(contract_rows)
            changed = True

        graph = _dict(raw.get("effect_observation_graph"))
        if (
            graph
            and _text(graph.get("receipt_id"))
            and not _dict(target.get("effect_observation_graph"))
        ):
            target["effect_observation_graph"] = deepcopy(graph)
            projected_graphs += 1
            changed = True

        if changed:
            projected_outcomes += 1

    output["execution_results"] = execution_results
    output["runtime_feedback_observation_projection_receipt"] = {
        "schema_version": "qualibug.runtime-feedback-observation-projection.v1",
        "status": "APPLIED",
        "raw_outcome_count": len(_list(output.get("results"))),
        "projected_outcome_count": projected_outcomes,
        "projected_observer_receipt_count": projected_observers,
        "projected_contract_receipt_count": projected_contracts,
        "projected_effect_graph_count": projected_graphs,
        "authority": "existing_receipt_backed_runtime_evidence_only",
        "execution_status_mutated": False,
        "fact_authority_elevated": False,
    }
    return output


__all__ = ["attach_runtime_feedback_observation_evidence"]
