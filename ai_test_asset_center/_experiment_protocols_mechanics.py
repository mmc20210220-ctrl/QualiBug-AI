"""Privacy-field protocol facade over the current validation-aware compiler."""
from __future__ import annotations

import re
from typing import Any

from . import experiment_protocols_privacy_base as _base
from .experiment_protocols_privacy_base import *  # noqa: F401,F403


PRIVACY_FIELD_ASSERTION_KIND = "privacy_field_policy"

# ── V1.5.0: Lazy one-time registration of multi-step protocols ──
_v150_protocols_registered = False
_v150_protocol_registration_error = ""
_V150_OWNED_PROTOCOLS = frozenset({
    ("process", "multi_step_business_process"),
    ("state", "state_chain_process"),
    ("process", "sequence_verification"),
})


def _ensure_v150_protocols() -> str:
    """Register V1.5.0 multi-step protocols and expose bootstrap failure.

    Registration is retryable.  The previous implementation marked the bootstrap
    complete before importing/registering the protocols and swallowed every exception;
    one transient or partial registration failure therefore disabled multi-step
    protocols for the rest of the process while compilation silently fell through to
    unrelated built-in logic.  That is a false capability claim.

    The registry is keyed assignment, so retrying after a partial registration is
    idempotent: already-written entries are replaced by the same governed definitions.
    """
    global _v150_protocols_registered, _v150_protocol_registration_error
    if _v150_protocols_registered:
        return ""
    try:
        from .multi_step_protocol import register_v150_multi_step_protocols

        register_v150_multi_step_protocols()
    except Exception as exc:  # noqa: BLE001 - returned as a typed compile blocker
        _v150_protocol_registration_error = (
            f"{type(exc).__name__}:{exc}"
        )[:180]
        return _v150_protocol_registration_error
    _v150_protocols_registered = True
    _v150_protocol_registration_error = ""
    return ""


def __getattr__(name: str) -> Any:
    return getattr(_base, name)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


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


def _compile_registered_protocol(
    registration: dict[str, Any],
    **envelope: Any,
) -> dict[str, Any]:
    """Run a registered protocol compiler and validate its plan.

    The registration's ``assertion_kind`` is a real authority, not decorative
    metadata.  If the compiler emits no assertion kind, that declared kind is
    projected into the plan.  A compiler may still emit a more specific dynamic
    kind (for example the async variant of a process graph); the explicit result
    wins, while absence is never allowed to erase the registry declaration.
    """
    from .experiment_protocol_registry import (
        ProtocolRegistryError,
        validate_registered_protocol_result,
    )

    protocol_id = _text(registration.get("protocol_id"))
    try:
        raw = registration["compiler"](dict(envelope))
        result = validate_registered_protocol_result(raw, registration=registration)
    except ProtocolRegistryError as exc:
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_REGISTERED_PROTOCOL_INVALID",
            "detail": f"registered_protocol_invalid:{protocol_id}:{exc}"[:200],
        }
    except Exception as exc:  # noqa: BLE001 - reported as BLOCKED, never escapes the loop
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_REGISTERED_PROTOCOL_INVALID",
            "detail": (
                f"registered_protocol_raised:{protocol_id}:{type(exc).__name__}:{exc}"
            )[:200],
        }
    result["_registry_protocol_id"] = protocol_id
    declared_assertion_kind = _text(registration.get("assertion_kind"))
    emitted_assertion = dict(_dict(result.get("assertion")))
    if declared_assertion_kind and not _text(emitted_assertion.get("kind")):
        emitted_assertion["kind"] = declared_assertion_kind
        result["assertion"] = emitted_assertion
    if registration.get("observers"):
        result.setdefault("observers", [
            {"observer_id": observer_id} for observer_id in registration["observers"]
        ])
    if registration.get("per_step_evidence"):
        result["per_step_evidence"] = True
    return result


def _resolve_family_protocol(risk_family: str, template: str) -> dict[str, Any] | None:
    from .experiment_protocol_registry import resolve_family_protocol

    return resolve_family_protocol(risk_family, template)


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
    # V1.5.0: ensure multi-step protocols are registered before first resolution.
    # A bootstrap failure blocks only the three templates owned by that bootstrap;
    # unrelated built-in protocols keep working.  Crucially, an owned template may
    # never fall through to a different single-step implementation.
    _template = _text(property_spec.get("template"))
    _family = _text(risk_family).lower()
    _v150_error = _ensure_v150_protocols()
    if _v150_error and (_family, _template) in _V150_OWNED_PROTOCOLS:
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_REGISTERED_PROTOCOL_INVALID",
            "detail": f"v150_protocol_registration_failed:{_v150_error}"[:200],
        }

    _registration = _resolve_family_protocol(risk_family, _template)
    if _registration is not None:
        return _compile_registered_protocol(
            _registration,
            risk_family=risk_family,
            operation=operation,
            operation_ref=operation_ref,
            control_actor_ref=control_actor_ref,
            treatment_actor_ref=treatment_actor_ref,
            property_spec=property_spec,
            behavior_ir=behavior_ir,
        )

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
