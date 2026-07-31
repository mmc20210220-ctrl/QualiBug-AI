"""Recognize the formal event observer as self-sufficient effect evidence at preflight.

The shared write preflight normally requires a source-declared HTTP/DB state read. That is
correct for state/delta assertions, but a source event-delivery contract is measured by its
registered event observer instead. Without this scoped bridge every POST event experiment is
blocked before the trigger, even though its exact observer, adapter and assertion compiled.

This module does not bypass preflight. It establishes a context only for one exact formal event
experiment and lets the existing preflight continue through actor, operation, adapter, fixture,
write-reversibility and cleanup checks unchanged.
"""
from __future__ import annotations

import contextvars
import functools
from typing import Any

from .formal_event_surface import ADAPTER, ASSERTION_KIND, OBSERVER_ID

_INSTALL_MARKER = "_qualibug_formal_event_execution_preflight_installed"
_ORIGINAL_PREFLIGHT_MARKER = "_qualibug_original_preflight_before_event_effect"
_ORIGINAL_EFFECT_MARKER = "_qualibug_original_declared_effect_before_event"
_ORIGINAL_RESPONSE_BOUND_MARKER = "_qualibug_original_response_bound_before_event"

_EVENT_OPERATION_IDENTITY: contextvars.ContextVar[tuple[str, str, str, str]] = (
    contextvars.ContextVar(
        "qualibug_formal_event_preflight_operation_identity",
        default=("", "", "", ""),
    )
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _formal_event_operation_identity(
    experiment: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> tuple[str, str, str, str]:
    exp = _dict(experiment)
    observers = [
        row
        for row in _list(exp.get("observers"))
        if isinstance(row, dict) and _text(row.get("observer_id")) == OBSERVER_ID
    ]
    assertions = [
        row
        for row in _list(exp.get("assertions"))
        if isinstance(row, dict) and _text(row.get("kind")) == ASSERTION_KIND
    ]
    if len(observers) != 1 or len(assertions) != 1:
        return ("", "", "", "")
    compiled_adapters = {
        _text(value) for value in _list(exp.get("compiled_adapters")) if _text(value)
    }
    if ADAPTER not in compiled_adapters:
        return ("", "", "", "")
    property_spec = _dict(assertions[0].get("property"))
    contract = _dict(property_spec.get("event_contract"))
    if not (
        _text(contract.get("observer_path"))
        and _text(contract.get("expected_event_type"))
        and _text(contract.get("event_id_field"))
        and _text(contract.get("correlation_field"))
    ):
        return ("", "", "", "")
    steps = [
        row
        for row in _list(exp.get("treatment_plan"))
        if isinstance(row, dict)
        and _text(row.get("protocol_step")) == "event_trigger"
        and _text(row.get("operation_ref"))
    ]
    if len(steps) != 1:
        return ("", "", "", "")
    operation_ref = _text(steps[0].get("operation_ref"))
    operations = [
        row
        for row in _list(_dict(behavior_ir).get("operations"))
        if isinstance(row, dict)
    ]
    matches = [
        row
        for row in operations
        if operation_ref
        in {
            _text(row.get("id")),
            _text(row.get("operation_id")),
            *[_text(value) for value in _list(row.get("source_operation_refs"))],
        }
    ]
    if len(matches) != 1:
        return ("", "", "", "")
    operation = matches[0]
    return (
        _text(operation.get("id")),
        _text(operation.get("operation_id")),
        _text(operation.get("method")).upper(),
        _text(operation.get("path") or operation.get("raw_path")),
    )


def _operation_matches_event_context(operation: dict[str, Any]) -> bool:
    expected_id, expected_operation_id, expected_method, expected_path = (
        _EVENT_OPERATION_IDENTITY.get()
    )
    if not any((expected_id, expected_operation_id, expected_method, expected_path)):
        return False
    row = _dict(operation)
    identifiers = {
        _text(row.get("id")),
        _text(row.get("operation_id")),
        *[_text(value) for value in _list(row.get("source_operation_refs"))],
    }
    identity_match = bool(
        (expected_id and expected_id in identifiers)
        or (expected_operation_id and expected_operation_id in identifiers)
    )
    return bool(
        identity_match
        and _text(row.get("method")).upper() == expected_method
        and _text(row.get("path") or row.get("raw_path")) == expected_path
    )


def install_formal_event_execution_preflight() -> None:
    """Install one context-scoped extension over the existing write preflight."""
    from . import experiment_executor as executor
    from . import experiment_runtime_support as support

    if getattr(executor, _INSTALL_MARKER, False):
        return
    original_preflight = getattr(
        executor,
        _ORIGINAL_PREFLIGHT_MARKER,
        executor.preflight_experiment_executable,
    )
    original_effect = getattr(
        support,
        _ORIGINAL_EFFECT_MARKER,
        support._declared_effect_observer_available,
    )
    original_response_bound = getattr(
        support,
        _ORIGINAL_RESPONSE_BOUND_MARKER,
        support._has_response_bound_create_observers,
    )
    setattr(executor, _ORIGINAL_PREFLIGHT_MARKER, original_preflight)
    setattr(support, _ORIGINAL_EFFECT_MARKER, original_effect)
    setattr(support, _ORIGINAL_RESPONSE_BOUND_MARKER, original_response_bound)

    @functools.wraps(original_effect)
    def declared_effect_with_formal_event(
        operation: dict[str, Any],
        operations: dict[str, dict[str, Any]],
    ) -> bool:
        return bool(
            original_effect(operation, operations)
            or _operation_matches_event_context(operation)
        )

    @functools.wraps(original_response_bound)
    def response_bound_state_read_with_formal_event(
        operation: dict[str, Any],
        operations: dict[str, dict[str, Any]],
    ) -> bool:
        if _operation_matches_event_context(operation):
            # Event delivery is the asserted effect. A response-bound state GET is not
            # required as a second, unrelated pre-write oracle for this experiment.
            return False
        return bool(original_response_bound(operation, operations))

    @functools.wraps(original_preflight)
    def preflight_with_formal_event_effect(
        experiment: dict[str, Any],
        *,
        behavior_ir: dict[str, Any],
        actor_tokens: dict[str, str],
    ) -> tuple[bool, str, str]:
        identity = _formal_event_operation_identity(experiment, behavior_ir)
        token = _EVENT_OPERATION_IDENTITY.set(identity)
        try:
            return original_preflight(
                experiment,
                behavior_ir=behavior_ir,
                actor_tokens=actor_tokens,
            )
        finally:
            _EVENT_OPERATION_IDENTITY.reset(token)

    support._declared_effect_observer_available = declared_effect_with_formal_event
    support._has_response_bound_create_observers = (
        response_bound_state_read_with_formal_event
    )
    support.preflight_experiment_executable = preflight_with_formal_event_effect
    executor.preflight_experiment_executable = preflight_with_formal_event_effect
    setattr(executor, _INSTALL_MARKER, True)


__all__ = ["install_formal_event_execution_preflight"]
