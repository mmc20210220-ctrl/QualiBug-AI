"""Single exact-scope façade for experiment outcome finalization.

The historical implementation lives in ``experiment_outcome_finalizer_core``.
This module preserves its public surface while making one authority change:
Observation, Oracle, and Cleanup receipts are synchronized through the
ProcessStepSemanticView, and the legacy unscoped ``append_receipt_ref`` fallback
is not exposed to the core finalizer.

Per-step cleanup equivalence receipts are created inside the historical core.
A context-local hook binds those receipts to their exact source steps before the
core assembles the Receipt Bundle and seals the final ledger hash. A lifecycle
ledger that explicitly declares ``fixture_required=False`` may enter the same
bundle validator with ``fixture_id=NOT_APPLICABLE``; no fixture receipt is made.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from . import experiment_outcome_finalizer_core as _core
from .process_step_receipt_scope import (
    extract_receipt_step_scope,
    receipt_id as _scope_receipt_id,
    synchronize_scoped_receipts_from_observations,
)
from .process_step_semantic_view import ProcessStepSemanticView


_NOT_APPLICABLE = "NOT_APPLICABLE"
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


def _invalidate_derived_step_snapshot(
    observations: dict[str, Any],
) -> None:
    """Force derived step facts to be resealed after final evidence exists."""
    for field in _DERIVED_STEP_SNAPSHOT_FIELDS:
        observations.pop(field, None)


def _publish_receipt_rows(
    observations: dict[str, Any],
    key: str,
    rows: list[dict[str, Any]],
) -> None:
    existing = observations.get(key)
    if isinstance(existing, list):
        existing[:] = rows
    else:
        observations[key] = list(rows)


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
_ORIGINAL_CLEANUP_EQUIVALENCE_ATTR = (
    "_qualibug_exact_scope_original_cleanup_equivalence"
)
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
if not hasattr(_core, _ORIGINAL_CLEANUP_EQUIVALENCE_ATTR):
    setattr(
        _core,
        _ORIGINAL_CLEANUP_EQUIVALENCE_ATTR,
        _core.evaluate_cleanup_equivalence,
    )
_original_observe_experiment_requirements = getattr(
    _core,
    _ORIGINAL_OBSERVER_ATTR,
)
_original_evaluate_contract_oracle = getattr(
    _core,
    _ORIGINAL_ORACLE_ATTR,
)
_original_evaluate_cleanup_equivalence = getattr(
    _core,
    _ORIGINAL_CLEANUP_EQUIVALENCE_ATTR,
)

_FinalizerScope = tuple[dict[str, Any], ProcessStepSemanticView]
_active_finalizer_scope: ContextVar[_FinalizerScope | None] = ContextVar(
    "qualibug_active_exact_scope_finalizer",
    default=None,
)


def _observe_experiment_requirements_exact(
    *args: Any,
    **kwargs: Any,
) -> list[dict[str, Any]]:
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


def _evaluate_contract_oracle_exact(
    *args: Any,
    **kwargs: Any,
) -> dict[str, Any]:
    verdict = _original_evaluate_contract_oracle(*args, **kwargs)
    evidence = kwargs.get("evidence")
    if isinstance(evidence, dict):
        evidence["oracle_verdict"] = verdict
    return verdict


def _evaluate_cleanup_equivalence_exact(
    *args: Any,
    **kwargs: Any,
) -> dict[str, Any]:
    """Bind graph step verification before the core seals its receipt bundle."""
    receipt = _original_evaluate_cleanup_equivalence(*args, **kwargs)
    context = _active_finalizer_scope.get()
    if context is None:
        return receipt

    observations, semantic_view = context
    step_receipts = _dict(
        _dict(receipt).get("step_equivalence_receipts_by_id")
    )
    if not step_receipts:
        return receipt

    rows = [
        dict(row)
        for _, row in sorted(step_receipts.items())
        if isinstance(row, dict)
    ]
    merged = _merge_receipt_rows(
        observations.get("cleanup_verification_receipts"),
        rows,
    )
    _publish_receipt_rows(
        observations,
        "cleanup_verification_receipts",
        merged,
    )

    source_ledger = semantic_view.source_ledger
    known_step_ids = list(source_ledger.recorded_step_ids())
    bound: list[dict[str, str]] = []
    unbound: list[dict[str, Any]] = []
    seen_receipt_ids: set[str] = set()
    for row in rows:
        scope = extract_receipt_step_scope(
            row,
            known_step_ids=known_step_ids,
        )
        receipt_id = _scope_receipt_id(row)
        step_id = _text(scope.get("step_id"))
        if (
            not receipt_id
            or receipt_id in seen_receipt_ids
            or scope.get("status") != "EXACT"
        ):
            unbound.append(
                {
                    **scope,
                    "receipt_id": receipt_id,
                    "evidence_kind": "cleanup_verification",
                    "status": (
                        "RECEIPT_REUSED"
                        if receipt_id in seen_receipt_ids
                        else _text(scope.get("status"))
                        or "RECEIPT_ID_MISSING"
                    ),
                }
            )
            continue
        seen_receipt_ids.add(receipt_id)
        if not semantic_view.append_scoped_receipt_ref(
            step_id=step_id,
            field="cleanup_receipt_ids",
            receipt_id=receipt_id,
            receipt_step_id=step_id,
        ):
            unbound.append(
                {
                    **scope,
                    "evidence_kind": "cleanup_verification",
                    "status": "LEDGER_SCOPE_BINDING_REJECTED",
                }
            )
            continue
        bound.append(
            {
                "receipt_id": receipt_id,
                "step_id": step_id,
                "evidence_kind": "cleanup_verification",
            }
        )

    observations["process_step_cleanup_verification_binding"] = {
        "bound": bound,
        "unbound": unbound,
        "complete": bool(rows) and not unbound and len(bound) == len(rows),
        "broadcast_fallback_forbidden": True,
    }
    _invalidate_derived_step_snapshot(observations)
    semantic_view.compute_hash()
    return receipt


def _install_core_hooks() -> None:
    _core.observe_experiment_requirements = (
        _observe_experiment_requirements_exact
    )
    _core.evaluate_contract_oracle = _evaluate_contract_oracle_exact
    _core.evaluate_cleanup_equivalence = (
        _evaluate_cleanup_equivalence_exact
    )


_install_core_hooks()

for _name in dir(_core):
    if not _name.startswith("__") and _name != "finalize_experiment_execution":
        globals()[_name] = getattr(_core, _name)


def _fixtureless_bundle_declared(
    semantic_view: ProcessStepSemanticView,
    observations: dict[str, Any],
    fixture_receipts: list[dict[str, Any]],
) -> bool:
    ledger = semantic_view.source_ledger
    return bool(
        getattr(ledger, "fixture_required", True) is False
        and _text(getattr(ledger, "fixture_id", "")) == _NOT_APPLICABLE
        and observations.get("fixture_required") is False
        and _text(observations.get("fixture_id")) == _NOT_APPLICABLE
        and not fixture_receipts
        and not _list(observations.get("fixture_provenance_receipts"))
    )


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
    observations["process_step_ledger"] = _ExactScopeFinalizerLedger(
        semantic_view
    )
    observations["process_step_ledger_view"] = "exact_scope_finalizer"

    fixture_receipts = [
        row
        for row in _list(kwargs.get("fixture_receipts"))
        if isinstance(row, dict)
    ]
    fixtureless = _fixtureless_bundle_declared(
        semantic_view,
        observations,
        fixture_receipts,
    )
    previous_force_present = "force_receipt_bundle" in observations
    previous_force = observations.get("force_receipt_bundle")
    if fixtureless:
        observations["force_receipt_bundle"] = True
        observations["fixtureless_bundle_activation"] = {
            "status": "ACTIVATED",
            "fixture_required": False,
            "fixture_id": _NOT_APPLICABLE,
            "synthetic_fixture_created": False,
            "bundle_validation_bypassed": False,
        }

    scope_token = _active_finalizer_scope.set(
        (observations, semantic_view)
    )
    try:
        result = _core.finalize_experiment_execution(*args, **kwargs)
    finally:
        _active_finalizer_scope.reset(scope_token)
        observations["process_step_ledger"] = semantic_view
        observations["process_step_ledger_view"] = "semantic_completion"
        if previous_force_present:
            observations["force_receipt_bundle"] = previous_force
        else:
            observations.pop("force_receipt_bundle", None)
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
        "_original_evaluate_cleanup_equivalence",
    }
)
