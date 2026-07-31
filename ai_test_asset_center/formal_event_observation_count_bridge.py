"""Preserve privacy-safe total event cardinality on formal event receipts.

The event observer already keeps raw rows private and exposes only correlated fingerprints and
counts. Correlation mismatch classification additionally needs to know that some event existed
in the completed window while none matched the trigger correlation. This bridge records only
the total row count returned by the existing bounded poller, then reseals the typed receipt
through the shared observer receipt authority.
"""
from __future__ import annotations

import contextvars
import copy
import functools
from typing import Any

from . import formal_event_surface as _surface

_INSTALL_MARKER = "_qualibug_formal_event_observation_count_installed"
_ORIGINAL_POLL_MARKER = "_qualibug_original_poll_before_event_total_count"
_TOTAL_COUNT: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "qualibug_formal_event_total_observed_count",
    default=None,
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def install_formal_event_observation_count_bridge() -> None:
    """Wrap the bounded poller and current registered observer once."""
    from . import observer_contracts_base as observers

    if getattr(observers, _INSTALL_MARKER, False):
        return
    original_poll = getattr(
        _surface,
        _ORIGINAL_POLL_MARKER,
        _surface._poll_event_endpoint,
    )
    setattr(_surface, _ORIGINAL_POLL_MARKER, original_poll)
    original_handler = observers._REGISTERED_OBSERVER_HANDLERS.get(
        _surface.OBSERVER_ID
    )
    if not callable(original_handler):
        raise RuntimeError("formal_event_observer_handler_not_registered")

    @functools.wraps(original_poll)
    def poll_with_total_count(*args: Any, **kwargs: Any) -> dict[str, Any]:
        authority = getattr(_surface, _ORIGINAL_POLL_MARKER)
        result = dict(_dict(authority(*args, **kwargs)))
        _TOTAL_COUNT.set(len([
            row for row in _list(result.get("events")) if isinstance(row, dict)
        ]))
        return result

    @functools.wraps(original_handler)
    def observe_with_total_count(envelope: dict[str, Any]) -> dict[str, Any]:
        token = _TOTAL_COUNT.set(None)
        try:
            receipt = original_handler(envelope)
            total = _TOTAL_COUNT.get()
        finally:
            _TOTAL_COUNT.reset(token)
        validated = observers.validate_observer_receipt(_dict(receipt))
        if total is None or _text(validated.get("observer_id")) != _surface.OBSERVER_ID:
            return validated
        evidence = copy.deepcopy(_dict(validated.get("evidence")))
        observation = copy.deepcopy(_dict(evidence.get(_surface.EVIDENCE_KEY)))
        if not observation:
            return validated
        observation["observed_total_count"] = max(0, int(total))
        evidence[_surface.EVIDENCE_KEY] = observation
        return observers.build_observer_receipt(
            observer_id=_surface.OBSERVER_ID,
            status=_text(validated.get("status")),
            reason_code=_text(validated.get("reason_code")),
            evidence=evidence,
            campaign_id=_text(validated.get("campaign_id")),
            execution_id=_text(validated.get("execution_id")),
        )

    _surface._poll_event_endpoint = poll_with_total_count
    observers._REGISTERED_OBSERVER_HANDLERS[_surface.OBSERVER_ID] = (
        observe_with_total_count
    )
    setattr(observers, _INSTALL_MARKER, True)


__all__ = ["install_formal_event_observation_count_bridge"]
