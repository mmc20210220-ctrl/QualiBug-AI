"""Experiment outcome finalizer facade with receipt-derived harness attribution.

The durable finalization implementation lives in
``_experiment_outcome_finalizer_core_mechanics``.  This facade changes one
truthfulness boundary only: cleanup harness failures are classified from the
formal cleanup execution/equivalence receipts when those receipts exist.

The historical classifier defaulted every non-described cleanup failure to
``HARNESS_CLEANUP_TRANSPORT_FAILED``.  That turns missing attribution into a
transport claim that may be false.  The governed classifier distinguishes
transport failure, response rejection and equivalence failure from evidence;
when the evidence cannot prove which happened it emits an explicit
``HARNESS_CLEANUP_FAILURE_UNATTRIBUTED`` instead of inventing a cause.
"""
from __future__ import annotations

from typing import Any

from . import _experiment_outcome_finalizer_core_mechanics as _core
from ._experiment_outcome_finalizer_core_mechanics import *  # noqa: F401,F403

_original_classify_harness_failure = _core._classify_harness_failure

HARNESS_CLEANUP_FAILURE_UNATTRIBUTED = "HARNESS_CLEANUP_FAILURE_UNATTRIBUTED"
HARNESS_FAILURE_SUBTYPES = tuple(
    dict.fromkeys(
        [
            *_core.HARNESS_FAILURE_SUBTYPES,
            HARNESS_CLEANUP_FAILURE_UNATTRIBUTED,
        ]
    )
)


def __getattr__(name: str) -> Any:
    return getattr(_core, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_core)))


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _status_code(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _cleanup_failure_subtype(observations: dict[str, Any]) -> str:
    """Classify one cleanup failure from formal receipt evidence only."""

    evidence = _dict(observations)
    equivalence = _dict(evidence.get("cleanup_equivalence_receipt"))
    equivalence_status = _text(equivalence.get("equivalence_status")).upper()
    equivalence_reason = _text(equivalence.get("reason_code")).upper()
    if (
        equivalence_status == "NOT_EQUIVALENT"
        or equivalence_reason in {
            "CLEANUP_EQUIVALENCE_FAILED",
            "ENTITY_STILL_PRESENT_AFTER_CLEANUP",
            "FIELD_VALUE_NOT_RESTORED",
            "BUSINESS_STATE_NOT_RESTORED",
        }
    ):
        return "HARNESS_CLEANUP_EQUIVALENCE_FAILED"

    cleanup = _dict(evidence.get("cleanup_execution_receipt"))
    if not cleanup:
        cleanup_result = _dict(evidence.get("cleanup_result"))
        cleanup = _dict(cleanup_result.get("cleanup_execution_receipt"))
        if not cleanup and _text(cleanup_result.get("schema_version")) == (
            "qualibug.cleanup-execution-receipt.v1"
        ):
            cleanup = cleanup_result

    if cleanup:
        status = _text(cleanup.get("status")).upper()
        reason = _text(cleanup.get("reason_code") or cleanup.get("error")).upper()
        attempted = cleanup.get("attempted") is True
        transport_reached = cleanup.get("transport_reached") is True
        status_code = _status_code(cleanup.get("status_code"))

        # Transport failure requires evidence that the cleanup was attempted
        # but did not reach a response-producing target boundary, or an explicit
        # transport reason from the cleanup executor.
        if (
            attempted
            and transport_reached is False
            and status_code == 0
        ) or any(
            marker in reason
            for marker in (
                "TRANSPORT",
                "CONNECTION",
                "TIMEOUT",
                "NETWORK",
            )
        ):
            return "HARNESS_CLEANUP_TRANSPORT_FAILED"

        # A received non-success response is a target-side cleanup rejection,
        # not a transport failure.
        if (
            attempted
            and transport_reached
            and status_code >= 400
        ) or status in {"REJECTED", "RESPONSE_REJECTED"}:
            return "HARNESS_CLEANUP_RESPONSE_REJECTED"

        # A formally successful transport may still fail restoration.  Prefer
        # the equivalence receipt for that claim; when the execution receipt
        # itself explicitly names restoration failure it is still usable.
        if any(
            marker in reason
            for marker in (
                "EQUIVALENCE",
                "NOT_RESTORED",
                "STATE_NOT_RESTORED",
            )
        ):
            return "HARNESS_CLEANUP_EQUIVALENCE_FAILED"

    # Legacy structured status is secondary evidence only.  It may classify a
    # known state but never causes a generic cleanup failure to be called
    # transport failure by default.
    cleanup_status = _text(evidence.get("cleanup_status")).upper()
    if cleanup_status in {"TRANSPORT_ERROR", "CONNECTION_FAILED"}:
        return "HARNESS_CLEANUP_TRANSPORT_FAILED"
    if cleanup_status in {"REJECTED", "RESPONSE_REJECTED"}:
        return "HARNESS_CLEANUP_RESPONSE_REJECTED"
    if cleanup_status in {"EQUIVALENCE_FAILED", "STATE_NOT_RESTORED"}:
        return "HARNESS_CLEANUP_EQUIVALENCE_FAILED"

    return HARNESS_CLEANUP_FAILURE_UNATTRIBUTED


def _classify_harness_failure(
    steps_out: list[dict[str, Any]],
    observations: dict[str, Any],
    pre_transport_block_reasons: list[str],
    cleanup_failures: int = 0,
) -> str:
    """Use exact cleanup receipts; delegate every non-cleanup case unchanged."""

    if cleanup_failures:
        return _cleanup_failure_subtype(observations)
    return _original_classify_harness_failure(
        steps_out,
        observations,
        pre_transport_block_reasons,
        cleanup_failures=0,
    )


# The mechanics finalizer resolves this global at call time. Installing the
# classifier here changes attribution only; execution, Oracle, cleanup and
# finding construction remain owned by the historical implementation.
_core._classify_harness_failure = _classify_harness_failure
_core.HARNESS_FAILURE_SUBTYPES = HARNESS_FAILURE_SUBTYPES

__all__ = sorted(
    {
        *[
            name
            for name in dir(_core)
            if not name.startswith("__")
        ],
        "HARNESS_CLEANUP_FAILURE_UNATTRIBUTED",
        "HARNESS_FAILURE_SUBTYPES",
    }
)
