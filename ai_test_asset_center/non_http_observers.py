"""Formal non-HTTP observers installed on the experiment mainline."""
from __future__ import annotations

from typing import Any

from . import experiment_compiler_obligation as _experiment_compiler
from .database_numeric_oracle import install_database_numeric_assertions
from .database_observer_experiment_runtime import (
    PHASE_AGGREGATE_OBSERVER_ID,
    install_experiment_database_observer,
)
from .database_relation_causality_runtime import (
    install_database_relation_causality_runtime,
)
from .database_relation_delta_causality_authority import (
    install_database_relation_causal_delta_assertion,
)
from .database_relation_delta_lineage import (
    install_database_relation_delta_assertion,
)
from .database_relation_numeric_oracle import (
    install_database_relation_numeric_assertion,
)
from .database_relation_observer_experiment_runtime import (
    install_database_relation_phase_execution,
    install_database_relation_phase_observer,
)
from .database_state_transition_oracle import (
    install_database_state_transition_assertion,
)
from .observer_contracts_base import (
    OBSERVER_REGISTRY,
    _receipt,
    register_observer,
    registered_observer_ids,
)
from .operation_causality_runtime import install_operation_causality_runtime
from .operation_causality_runtime_attachment import (
    install_operation_causality_attachment,
)

_PROCESS_OBSERVER_ID = "process_timeline"
_COMPILER_MARKER = "_qualibug_process_timeline_observer_installed"
_ORIGINAL_COMPILER_MARKER = "_qualibug_original_compile_observer_requirements"
_PROCESS_LEDGER_FAMILIES = frozenset({"temporal"})
_DATABASE_PHASE_RECEIPT_KEY = "approved_database_observer_phase_receipts"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _process_timeline_handler(envelope: dict[str, Any]) -> dict[str, Any]:
    observations = _dict(_dict(envelope).get("observations"))
    timeline = [
        dict(row)
        for row in _list(observations.get("process_timeline"))
        if isinstance(row, dict)
    ]
    required_ids = [
        _text(value)
        for value in _list(observations.get("required_step_ids"))
        if _text(value)
    ]
    planned_ids = [
        _text(value)
        for value in _list(observations.get("planned_step_ids"))
        if _text(value)
    ]
    executed_ids = [
        _text(value)
        for value in _list(observations.get("executed_step_ids"))
        if _text(value)
    ]
    transport_receipt_ids = [
        _text(value)
        for value in _list(observations.get("transport_receipt_ids"))
        if _text(value)
    ]

    event_ids = []
    timestamps = []
    statuses = []
    for row in timeline:
        event_id = _text(row.get("step_id") or row.get("event_id") or row.get("id"))
        if event_id:
            event_ids.append(event_id)
        for key in (
            "occurred_at",
            "timestamp",
            "transport_started",
            "transport_completed",
            "started_at",
            "started_at_utc",
            "completed_at",
            "completed_at_utc",
        ):
            value = row.get(key)
            if value not in (None, ""):
                timestamps.append(str(value))
        status = _text(
            row.get("event_type")
            or row.get("final_status")
            or row.get("status")
            or row.get("outcome")
        )
        if status:
            statuses.append(status)

    evidence = {
        "process_step_ledger_id": _text(observations.get("process_step_ledger_id")),
        "process_step_ledger_hash": _text(observations.get("process_step_ledger_hash")),
        "timeline_event_count": len(timeline),
        "timeline_event_ids": event_ids,
        "timeline_statuses": statuses,
        "timestamp_count": len(timestamps),
        "timestamps": timestamps,
        "required_step_ids": required_ids,
        "planned_step_ids": planned_ids,
        "executed_step_ids": executed_ids,
        "transport_receipt_ids": transport_receipt_ids,
        "required_steps_executed": (
            bool(required_ids) and set(required_ids).issubset(set(executed_ids))
        ),
    }
    if not timeline:
        return _receipt(
            observer_id=_PROCESS_OBSERVER_ID,
            status="INDETERMINATE",
            reason_code="PROCESS_TIMELINE_MISSING",
            evidence=evidence,
        )
    if not timestamps:
        return _receipt(
            observer_id=_PROCESS_OBSERVER_ID,
            status="INDETERMINATE",
            reason_code="PROCESS_TIMELINE_TIMESTAMP_MISSING",
            evidence=evidence,
        )
    if required_ids and not set(required_ids).issubset(set(executed_ids)):
        return _receipt(
            observer_id=_PROCESS_OBSERVER_ID,
            status="INDETERMINATE",
            reason_code="PROCESS_TIMELINE_REQUIRED_STEPS_INCOMPLETE",
            evidence=evidence,
        )
    return _receipt(
        observer_id=_PROCESS_OBSERVER_ID,
        status="OBSERVED",
        evidence=evidence,
    )


def install_non_http_observers() -> None:
    """Register product-ledger, database facts, and database Oracle assertions."""

    if _PROCESS_OBSERVER_ID not in registered_observer_ids():
        register_observer(
            _PROCESS_OBSERVER_ID,
            surface="process_timeline",
            adapter="process_ledger",
            handler=_process_timeline_handler,
            evidence_keys=(
                "process_timeline",
                "step_timestamps",
                "required_steps_executed",
            ),
        )

    # Root row phase receipts are produced by the existing Experiment Executor.
    install_experiment_database_observer()
    aggregate_contract = _dict(OBSERVER_REGISTRY.get(PHASE_AGGREGATE_OBSERVER_ID))
    aggregate_contract["evidence_keys"] = tuple(
        dict.fromkeys(
            [
                *[
                    _text(value)
                    for value in aggregate_contract.get("evidence_keys", ())
                    if _text(value)
                ],
                _DATABASE_PHASE_RECEIPT_KEY,
            ]
        )
    )
    OBSERVER_REGISTRY[PHASE_AGGREGATE_OBSERVER_ID] = aggregate_contract

    # FK-scoped relation aggregation extends the same phase executor. Causality
    # wrappers are installed only after that extension exists, so BEFORE/AFTER,
    # transport proof and the approved relation query share one execution chain.
    install_database_relation_phase_observer()
    install_database_relation_phase_execution()
    install_database_relation_causality_runtime()
    install_operation_causality_runtime()
    install_operation_causality_attachment()

    # Assertion registration happens only after all required evidence keys are declared.
    install_database_state_transition_assertion()
    install_database_numeric_assertions()
    install_database_relation_numeric_assertion()
    install_database_relation_delta_assertion()
    install_database_relation_causal_delta_assertion()

    if hasattr(_experiment_compiler, _ORIGINAL_COMPILER_MARKER):
        original_compile = getattr(
            _experiment_compiler,
            _ORIGINAL_COMPILER_MARKER,
        )
    else:
        original_compile = _experiment_compiler.compile_observer_requirements
        setattr(
            _experiment_compiler,
            _ORIGINAL_COMPILER_MARKER,
            original_compile,
        )

    if getattr(_experiment_compiler, _COMPILER_MARKER, False):
        return

    def compile_observer_requirements_with_process_timeline(
        observer_ids: list[str],
        *,
        risk_family: str,
        available_adapters: set[str],
        require_authorization_comparison: bool = True,
    ) -> tuple[list[dict[str, Any]], str, str]:
        required = list(observer_ids or [])
        if (
            _text(risk_family) in _PROCESS_LEDGER_FAMILIES
            and _PROCESS_OBSERVER_ID not in {_text(value) for value in required}
        ):
            required.append(_PROCESS_OBSERVER_ID)
        return original_compile(
            required,
            risk_family=risk_family,
            available_adapters=available_adapters,
            require_authorization_comparison=require_authorization_comparison,
        )

    _experiment_compiler.compile_observer_requirements = (
        compile_observer_requirements_with_process_timeline
    )
    setattr(_experiment_compiler, _COMPILER_MARKER, True)


install_non_http_observers()

__all__ = ["install_non_http_observers"]
