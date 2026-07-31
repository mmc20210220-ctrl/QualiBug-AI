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

import hashlib
import json
from contextvars import ContextVar
from typing import Any

from . import experiment_outcome_finalizer_core as _core
from .observer_contracts_base import build_observer_receipt
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


def _stable_fingerprint(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _status_code(value: Any) -> int:
    row = _dict(value)
    try:
        return int(row.get("status_code") or row.get("status") or 0)
    except (TypeError, ValueError):
        return 0


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
            receipt_id = _scope_receipt_id(row)
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


def _plan_step_ids(experiment: dict[str, Any]) -> tuple[list[str], list[str]]:
    control = [
        _text(row.get("step_id"))
        for row in _list(_dict(experiment).get("control_plan"))
        if isinstance(row, dict) and _text(row.get("step_id"))
    ]
    treatment = [
        _text(row.get("step_id"))
        for row in _list(_dict(experiment).get("treatment_plan"))
        if isinstance(row, dict) and _text(row.get("step_id"))
    ]
    return list(dict.fromkeys(control)), list(dict.fromkeys(treatment))


def _observer_subject_step(
    experiment: dict[str, Any],
    observer_id: str,
) -> tuple[str, str]:
    declaration = next(
        (
            row
            for row in _list(_dict(experiment).get("observers"))
            if isinstance(row, dict)
            and _text(row.get("observer_id")) == observer_id
        ),
        {},
    )
    declared = _text(
        _dict(declaration).get("subject_step_id")
        or _dict(declaration).get("step_id")
    )
    if declared:
        return declared, "observer_declaration"
    control_ids, treatment_ids = _plan_step_ids(experiment)
    if treatment_ids:
        return treatment_ids[-1], "protocol_final_treatment_subject"
    if control_ids:
        return control_ids[-1], "protocol_final_control_subject"
    return "", ""


def _step_scoped_http_response_receipts(
    *,
    observations: dict[str, Any],
    aggregate_receipt: dict[str, Any],
) -> list[dict[str, Any]]:
    """Create one transport-observation receipt for every explicit plan step."""
    receipts: list[dict[str, Any]] = []
    seen_steps: set[str] = set()
    for raw in _list(observations.get("execution_steps")):
        step = _dict(raw)
        step_id = _text(step.get("step_id"))
        phase = _text(step.get("phase"))
        if not step_id or step_id in seen_steps or phase not in {"control", "treatment"}:
            continue
        seen_steps.add(step_id)
        status_code = _status_code(step)
        response_id = _text(
            step.get("response_receipt_id")
            or _dict(step.get("governance_receipt")).get("receipt_id")
        )
        receipts.append(
            build_observer_receipt(
                observer_id="http_response",
                status="OBSERVED" if status_code > 0 else "FAILED",
                reason_code="" if status_code > 0 else "HTTP_RESPONSE_MISSING",
                evidence={
                    "step_id": step_id,
                    "phase": phase,
                    "operation_ref": _text(step.get("operation_ref")),
                    "status_code": status_code,
                    "response_received": status_code > 0,
                    "response_receipt_id": response_id,
                    "response_body_fingerprint": _stable_fingerprint(step.get("body")),
                    "source_observer_receipt_id": _scope_receipt_id(aggregate_receipt),
                    "scope_basis": "execution_step_identity",
                },
                campaign_id=_text(aggregate_receipt.get("campaign_id")),
                execution_id=_text(aggregate_receipt.get("execution_id")),
            )
        )
    return receipts


def _scope_generated_observer_receipts(
    *,
    experiment: dict[str, Any],
    observations: dict[str, Any],
    generated: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Replace aggregate transport evidence and scope semantic observers exactly."""
    scoped: list[dict[str, Any]] = []
    for raw in generated:
        receipt = _dict(raw)
        observer_id = _text(receipt.get("observer_id"))
        if observer_id == "http_response":
            per_step = _step_scoped_http_response_receipts(
                observations=observations,
                aggregate_receipt=receipt,
            )
            scoped.extend(per_step or [dict(receipt)])
            continue
        if extract_receipt_step_scope(receipt).get("status") == "EXACT":
            scoped.append(dict(receipt))
            continue
        step_id, scope_basis = _observer_subject_step(experiment, observer_id)
        if not step_id:
            scoped.append(dict(receipt))
            continue
        evidence = dict(_dict(receipt.get("evidence")))
        evidence.update(
            {
                "step_id": step_id,
                "scope_basis": scope_basis,
                "source_observer_receipt_id": _scope_receipt_id(receipt),
            }
        )
        scoped.append(
            build_observer_receipt(
                observer_id=observer_id,
                status=_text(receipt.get("status")),
                reason_code=_text(receipt.get("reason_code")),
                evidence=evidence,
                campaign_id=_text(receipt.get("campaign_id")),
                execution_id=_text(receipt.get("execution_id")),
            )
        )
    return _merge_receipt_rows(scoped)


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
    experiment = _dict(args[0] if args else kwargs.get("experiment"))
    generated = _scope_generated_observer_receipts(
        experiment=experiment,
        observations=existing,
        generated=[row for row in _list(generated) if isinstance(row, dict)],
    )
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
