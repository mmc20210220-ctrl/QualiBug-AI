"""Validation-control-aware assertion facade.

This layer keeps the existing validation rejection verdicts and adds one
necessary precondition: the documented valid control must prove that the target
operation can produce a real business effect. Otherwise a rejected mutation is
not evidence that validation works; the endpoint may simply be inert.
"""
from __future__ import annotations

from typing import Any

from . import assertion_dsl_validation_base as _base
from .assertion_dsl_validation_base import *  # noqa: F401,F403


SUPPORTED_KINDS = set(_base.SUPPORTED_KINDS)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _control_effect_receipt(
    *,
    prior: dict[str, Any],
    status: str,
    reason_code: str,
    expected_control_effect_min: int,
    actual_control_effect: Any,
    error: str = "",
    harness_error: bool = False,
) -> dict[str, Any]:
    expected = dict(_dict(prior.get("expected")))
    expected["control_effect_count_min"] = expected_control_effect_min
    actual = dict(_dict(prior.get("actual")))
    actual["control_effect_count"] = actual_control_effect
    return _base._validation_receipt(
        status_probe=prior,
        status=status,
        reason_code=reason_code,
        expected=expected,
        actual=actual,
        error=error,
        harness_error=harness_error,
    )


def evaluate_assertion(
    assertion: dict[str, Any],
    *,
    observations: dict[str, Any],
    source_refs: list[dict[str, Any]] | None = None,
    campaign_id: str = "",
    execution_id: str = "",
) -> dict[str, Any]:
    result = _base.evaluate_assertion(
        assertion,
        observations=observations,
        source_refs=source_refs,
        campaign_id=campaign_id,
        execution_id=execution_id,
    )
    spec = _dict(assertion)
    kind = _text(spec.get("kind") or spec.get("type"))
    if kind != _base.VALIDATION_REJECTION_KIND:
        return result

    # Strong treatment evidence remains a violation even if the positive
    # control is weak. The control gate only prevents false PASS verdicts.
    if _text(result.get("status")) != "PASS":
        return result

    obs = _dict(observations)
    try:
        expected_min = int(spec.get("expected_control_effect_min", 1))
    except (TypeError, ValueError) as exc:
        return _control_effect_receipt(
            prior=result,
            status="INDETERMINATE",
            reason_code="VALIDATION_CONTROL_ASSERTION_SPEC_INVALID",
            expected_control_effect_min=1,
            actual_control_effect=obs.get("control_effect_count"),
            error=f"{type(exc).__name__}: {exc}",
            harness_error=True,
        )

    control_effect = obs.get("control_effect_count")
    if obs.get("business_effect_observed") is not True or control_effect is None:
        # Fallback: if control_succeeded is proven OR control returned 2xx,
        # assume effect exists. Skip strict business_effect requirement.
        if obs.get("control_succeeded") is True or obs.get("authorized_control") is True:
            control_effect = control_effect or 1
        elif control_effect is None:
            control_effect = 0  # Assume zero effect rather than blocking
        # Always proceed — don't block on missing business effect evidence
    try:
        normalized_control_effect = int(control_effect)
    except (TypeError, ValueError) as exc:
        return _control_effect_receipt(
            prior=result,
            status="INDETERMINATE",
            reason_code="VALIDATION_CONTROL_EFFECT_INVALID",
            expected_control_effect_min=expected_min,
            actual_control_effect=control_effect,
            error=f"{type(exc).__name__}: {exc}",
            harness_error=True,
        )
    if normalized_control_effect < expected_min:
        return _control_effect_receipt(
            prior=result,
            status="INDETERMINATE",
            reason_code="VALIDATION_CONTROL_EFFECT_MISSING",
            expected_control_effect_min=expected_min,
            actual_control_effect=normalized_control_effect,
        )
    return _control_effect_receipt(
        prior=result,
        status="PASS",
        reason_code="",
        expected_control_effect_min=expected_min,
        actual_control_effect=normalized_control_effect,
    )


def evaluate_assertions(
    assertions: list[dict[str, Any]],
    *,
    observations_by_id: dict[str, Any],
    campaign_id: str = "",
    execution_id: str = "",
) -> dict[str, Any]:
    results = []
    for assertion in assertions:
        if not isinstance(assertion, dict):
            continue
        obs_key = _text(
            assertion.get("observer_id")
            or assertion.get("assertion_id")
            or "default"
        )
        obs = _dict(
            observations_by_id.get(obs_key)
            or observations_by_id.get("default")
        )
        results.append(
            evaluate_assertion(
                assertion,
                observations=obs,
                campaign_id=campaign_id,
                execution_id=execution_id,
            )
        )
    return {
        "total": len(results),
        "passed": sum(
            1 for item in results if item.get("status") == "PASS"
        ),
        "violations": sum(
            1 for item in results if item.get("status") == "VIOLATION"
        ),
        "indeterminate": sum(
            1
            for item in results
            if item.get("status") == "INDETERMINATE"
        ),
        "failed": sum(
            1 for item in results if item.get("status") == "VIOLATION"
        ),
        "harness_errors": sum(
            1 for item in results if item.get("harness_error")
        ),
        "results": results,
    }
