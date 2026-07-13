"""Validation-aware protocol facade over the stable family compiler."""
from __future__ import annotations

from typing import Any

from . import experiment_protocols_base as _base
from .experiment_protocols_base import *  # noqa: F401,F403


def _text(value: Any) -> str:
    return str(value or "").strip()


def compile_family_protocol(
    *,
    risk_family: str,
    operation: dict[str, Any],
    operation_ref: str,
    control_actor_ref: str,
    treatment_actor_ref: str,
    property_spec: dict[str, Any],
) -> dict[str, Any]:
    result = _base.compile_family_protocol(
        risk_family=risk_family,
        operation=operation,
        operation_ref=operation_ref,
        control_actor_ref=control_actor_ref,
        treatment_actor_ref=treatment_actor_ref,
        property_spec=property_spec,
    )
    if (
        _text(risk_family) != "validation"
        or _text(result.get("status")) != "COMPILED"
    ):
        return result

    assertion = dict(result.get("assertion") or {})
    assertion.update({
        "kind": "validation_rejection",
        "expected_class": 4,
        "expected_effect_count": 0,
        "expected_control_effect_min": 1,
        "compare_field": "status_code",
        "effect_field": "treatment_effect_count",
        "control_effect_field": "control_effect_count",
    })
    return {
        **result,
        "assertion": assertion,
    }
