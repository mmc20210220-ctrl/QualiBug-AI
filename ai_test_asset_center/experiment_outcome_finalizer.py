"""Single exact-scope façade for experiment outcome finalization.

The historical implementation lives in ``experiment_outcome_finalizer_core``.
This module preserves its public surface while making one authority change:
Observation, Oracle, and Cleanup receipts are synchronized through the
ProcessStepSemanticView, and the legacy unscoped ``append_receipt_ref`` fallback
is not exposed to the core finalizer.
"""
from __future__ import annotations

from typing import Any

from . import experiment_outcome_finalizer_core as _core
from .process_step_receipt_scope import synchronize_scoped_receipts_from_observations
from .process_step_semantic_view import ProcessStepSemanticView


_DERIVED_STEP_SNAPSHOT_FIELDS = (
    "process_step_receipts",
    "process_step_ledger_hash",
    "process_step_semantic_projection",
    "recorded_step_ids",
    "attempted_step_ids",
    "executed_step_ids",
    "accepted_step_ids",
    "completed_step_ids",
    "failed_step_ids",
    "pending_semantic_step_ids",
    "per_step_evidence_completeness",
    "process_step_balance",
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _merge_receipt_rows(*groups: Any) -> list[dict[str, Any]]:
    """Merge existing receipts without inventing identity or evidence."""
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in groups:
        rows = [group] if isinstance(group, dict) else _list(group)
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            row = dict(raw)
            receipt_id = _text(
                row.get("receipt_id")
                or row.get("verification_id")
                or _dict(row.get("payload")).get("receipt_id")
            )
            key = receipt_id or f"anonymous:{len(merged)}"
            if key in seen:
                continue
            seen.add(key)
            merged.append(row)
    return merged


def _invalidate_derived_step_snapshot(observations: dict[str, Any]) -> None:
    """Force all derived step facts to be resealed after final evidence exists."""
    for field in _DERIVED_STEP_SNAPSHOT_FIELDS:
        observations.pop(field, None)


class _ExactScopeFinalizerLedger:
    """Delegate ledger authority while hiding the legacy broadcast mutation API."""

    def __init__(self, ledger: Any):
        self._ledger = ledger

    def __getattr__(self, name: str) -> Any:
        if name == "append_receipt_ref":
            raise AttributeError(name)
        return getattr(self._ledger, name)

    @property
    def exact_scope_ledger(self) -> Any:
        return self._ledger


_ORIGINAL_OBSERVER_ATTR = "_qualibug_exact_scope_original_observer"
_ORIGINAL_ORACLE_ATTR = "_qualibug_exact_scope_original_oracle"
if not hasattr(_core, _ORIGINAL_OBSERVER_ATTR):
    setattr(
        _core,
        _ORIGINAL_OBSERVER_ATTR,
        _core.observe_experiment_requirements,
    )
if not hasattr(_core, _ORIGINAL_ORACLE_ATTR):
    setattr(
        _core,
        _ORIGINAL_ORACLE_ATTR,
        _core.evaluate_contract_oracle,
    )
_original_observe_experiment_requirements = getattr(
    _core,
    _ORIGINAL_OBSERVER_ATTR,
)
_original_evaluate_contract_oracle = getattr(
    _core,
    _ORIGINAL_ORACLE_ATTR,
)


def _observe_experiment_requirements_exact(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    observations = kwargs.get("observations")
    existing = _dict(observations) if isinstance(observations, dict) else {}
    generated = _original_observe_experiment_requirements(*args, **kwargs)
    merged = _merge_receipt_rows(
        generated,
        existing.get("observation_receipts"),
        existing.get("process_step_observation_receipts"),
    )
    if isinstance(observations, dict):
        observations["observer_receipts"] = merged
    return merged


def _evaluate_contract_oracle_exact(*args: Any, **kwargs: Any) -> dict[str, Any]:
    verdict = _original_evaluate_contract_oracle(*args, **kwargs)
    evidence = kwargs.get("evidence")
    if isinstance(evidence, dict):
        evidence["oracle_verdict"] = verdict
    return verdict


def _install_core_hooks() -> None:
    _core.observe_experiment_requirements = _observe_experiment_requirements_exact
    _core.evaluate_contract_oracle = _evaluate_contract_oracle_exact


_install_core_hooks()

for _name in dir(_core):
    if not _name.startswith("__") and _name != "finalize_experiment_execution":
        globals()[_name] = getattr(_core, _name)


def finalize_experiment_execution(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Finalize through one exact-scoped process-step evidence authority."""
    _install_core_hooks()
    observations = kwargs.get("observations")
    if not isinstance(observations, dict):
        return _core.finalize_experiment_execution(*args, **kwargs)

    ledger = observations.get("process_step_ledger")
    if ledger is None:
        return _core.finalize_experiment_execution(*args, **kwargs)

    semantic_view = (
        ledger
        if isinstance(ledger, ProcessStepSemanticView)
        else ProcessStepSemanticView(ledger, observations=observations)
    )
    synchronize_scoped_receipts_from_observations(
        semantic_view.source_ledger,
        observations,
    )
    _invalidate_derived_step_snapshot(observations)
    observations["process_step_ledger"] = _ExactScopeFinalizerLedger(semantic_view)
    observations["process_step_ledger_view"] = "exact_scope_finalizer"
    try:
        result = _core.finalize_experiment_execution(*args, **kwargs)
    finally:
        observations["process_step_ledger"] = semantic_view
        observations["process_step_ledger_view"] = "semantic_completion"
    return result


__all__ = sorted(
    name
    for name in globals()
    if not name.startswith("__")
    and name
    not in {
        "_core",
        "_name",
        "_original_observe_experiment_requirements",
        "_original_evaluate_contract_oracle",
    }
)
