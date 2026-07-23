"""Privacy-field protocol facade over the current validation-aware compiler."""
from __future__ import annotations

import re
from typing import Any

from . import experiment_protocols_privacy_base as _base
from .experiment_protocols_privacy_base import *  # noqa: F401,F403


PRIVACY_FIELD_ASSERTION_KIND = "privacy_field_policy"


def __getattr__(name: str) -> Any:
    return getattr(_base, name)


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _valid_tokens(value: Any) -> list[str | int]:
    tokens = _list(value)
    if not tokens or not all(
        (isinstance(token, str) and bool(token))
        or (isinstance(token, int) and not isinstance(token, bool) and token >= 0)
        for token in tokens
    ):
        return []
    return list(tokens)


def compile_family_protocol(
    *,
    risk_family: str,
    operation: dict[str, Any],
    operation_ref: str,
    control_actor_ref: str,
    treatment_actor_ref: str,
    property_spec: dict[str, Any],
    behavior_ir: dict[str, Any] | None = None,
) -> dict[str, Any]:
    policy = _text(property_spec.get("privacy_policy"))
    is_field_policy = (
        _text(risk_family) == "privacy"
        and _text(property_spec.get("privacy_test_mode")) == "field_policy"
        and policy in {"absent", "masked"}
    )
    if not is_field_policy:
        return _base.compile_family_protocol(
            risk_family=risk_family,
            operation=operation,
            operation_ref=operation_ref,
            control_actor_ref=control_actor_ref,
            treatment_actor_ref=treatment_actor_ref,
            property_spec=property_spec,
            behavior_ir=behavior_ir,
        )

    actor_ref = _text(treatment_actor_ref or control_actor_ref or property_spec.get("actor_ref"))
    if not actor_ref:
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_ACTOR",
            "detail": "privacy_field_actor",
        }
    if _text(operation.get("method")).upper() != "GET":
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_OPERATION",
            "detail": "privacy_field_protocol_requires_get",
        }
    tokens = _valid_tokens(property_spec.get("field_tokens"))
    if not tokens:
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_BINDING",
            "detail": "privacy_field_tokens_missing",
        }
    mask_pattern = _text(property_spec.get("mask_pattern"))
    if policy == "masked":
        if not mask_pattern:
            return {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_BINDING",
                "detail": "privacy_mask_pattern_missing",
            }
        try:
            re.compile(mask_pattern)
        except re.error:
            return {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_BINDING",
                "detail": "privacy_mask_pattern_invalid",
            }

    return {
        "status": "COMPILED",
        "control_plan": [],
        "treatment_plan": [{
            "step_id": "treatment_1",
            "actor_ref": actor_ref,
            "operation_ref": operation_ref,
            "intent": "privacy_field_observation",
            "protocol_step": "privacy_field_read",
            "property_template": _text(property_spec.get("template")),
        }],
        "assertion": {
            "kind": PRIVACY_FIELD_ASSERTION_KIND,
            "privacy_policy": policy,
            "field_tokens": tokens,
            "json_path": _text(property_spec.get("json_path")),
            "mask_pattern": mask_pattern,
            "allow_absent": property_spec.get("allow_absent") is True,
            "require_meaningful_response": True,
            "invariant_ref": _text(property_spec.get("invariant_ref")),
        },
    }
