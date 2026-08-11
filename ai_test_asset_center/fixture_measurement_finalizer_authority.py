"""Project sealed fixture/precondition measurement blocks into Finalizer truth.

``experiment_fixture_materializer_with_preconditions._block_measurement`` keeps
its state ``ready`` on purpose: fixture/setup writes may already have occurred
and the normal executor path must still run cleanup. It clears precondition /
control / treatment plans and seals the cause on ``exp.execution_*_blocked``.

Historically that sealed cause never reached ``pre_transport_block_reasons``. A
block that required no fixture HTTP therefore ended with zero measured steps and
could be classified as ``HARNESS_REQUEST_BUILD_FAILED`` even though the product
had correctly refused to measure an unproven fixture/precondition.

This module composes through the canonical Finalizer hook registry. Cleanup
failures retain priority; only a successfully-cleaned pre-measurement block is
projected as the terminal typed blocker.
"""
from __future__ import annotations

from typing import Any, Callable


SCHEMA_VERSION = "qualibug.fixture-measurement-first-loss.v1"
_PHASES = (
    "fixture_activation_reconciliation",
    "fixture_precondition",
    "flow_data_materialization",
    "precondition",
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def fixture_measurement_first_loss_receipt(
    exp: dict[str, Any],
    *,
    steps_out: list[dict[str, Any]] | None = None,
    cleanup_failures: int = 0,
) -> dict[str, Any]:
    """Return the earliest sealed pre-measurement block, if any."""

    for phase in _PHASES:
        raw = _dict(_dict(exp).get(f"execution_{phase}_blocked"))
        reason = _text(raw.get("reason_code"))
        if not raw or not reason:
            continue
        detail = _text(raw.get("detail")) or reason
        target_activity = any(
            isinstance(step, dict)
            and int(step.get("status_code") or 0) > 0
            for step in _list(steps_out)
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "BLOCKED",
            "phase": phase,
            "reason_code": reason,
            "detail": detail,
            "measured_business_transport_attempted": False,
            "premeasurement_target_activity_observed": target_activity,
            "cleanup_failures": int(cleanup_failures or 0),
            "cleanup_failure_has_priority": bool(cleanup_failures),
            "harness_failure_claimed": False,
            "source_receipt": dict(raw),
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "NOT_APPLICABLE",
        "reason_code": "",
        "cleanup_failures": int(cleanup_failures or 0),
        "harness_failure_claimed": False,
    }


def fixture_measurement_finalizer_hook(
    next_call: Callable[[tuple[Any, ...], dict[str, Any]], dict[str, Any]],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    """Inject the sealed blocker before finalization, preserving cleanup priority."""

    call_kwargs = dict(kwargs)
    exp = _dict(call_kwargs.get("exp"))
    steps_out = [
        dict(step)
        for step in _list(call_kwargs.get("steps_out"))
        if isinstance(step, dict)
    ]
    cleanup_failures = int(call_kwargs.get("cleanup_failures") or 0)
    receipt = fixture_measurement_first_loss_receipt(
        exp,
        steps_out=steps_out,
        cleanup_failures=cleanup_failures,
    )
    applies = _text(receipt.get("status")) == "BLOCKED"

    observations = call_kwargs.get("observations")
    if applies and isinstance(observations, dict):
        observations["fixture_measurement_first_loss_receipt"] = dict(receipt)

    # Cleanup/environment safety outranks the earlier measurement blocker. A
    # failed cleanup must remain a cleanup terminal, never be hidden by this
    # projection.
    if applies and cleanup_failures == 0:
        reasons = [
            _text(value)
            for value in _list(call_kwargs.get("pre_transport_block_reasons"))
            if _text(value)
        ]
        token = (
            _text(receipt.get("reason_code"))
            + ":"
            + _text(receipt.get("detail"))
        ).rstrip(":")
        if token and token not in reasons:
            reasons.append(token)
        call_kwargs["pre_transport_block_reasons"] = reasons

    result = next_call(args, call_kwargs)
    governed = dict(_dict(result))
    if not applies:
        return governed

    governed["fixture_measurement_first_loss_receipt"] = dict(receipt)
    if cleanup_failures:
        return governed

    # The canonical mechanics maps the textual pre-transport category to a
    # broad BLOCKED_* code. Preserve the more precise, already-sealed fixture /
    # precondition reason on the public terminal without changing its status,
    # Oracle evidence or cleanup result.
    if _text(governed.get("status")).upper() == "BLOCKED":
        reason = _text(receipt.get("reason_code"))
        if reason:
            governed["reason_code"] = reason
            execution_receipt = dict(_dict(governed.get("execution_receipt")))
            execution_receipt["reason_code"] = reason
            governed["execution_receipt"] = execution_receipt
    return governed


def install_fixture_measurement_finalizer_authority() -> None:
    """Register once on the canonical public Finalizer composition surface."""

    from .experiment_outcome_finalizer import register_finalizer_hook

    register_finalizer_hook(
        "fixture_measurement_first_loss",
        fixture_measurement_finalizer_hook,
    )


__all__ = [
    "SCHEMA_VERSION",
    "fixture_measurement_first_loss_receipt",
    "fixture_measurement_finalizer_hook",
    "install_fixture_measurement_finalizer_authority",
]
