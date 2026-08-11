"""Privacy-field protocol facade over the current validation-aware compiler."""
from __future__ import annotations

import re
from typing import Any

from . import experiment_protocols_privacy_base as _base
from .experiment_protocols_privacy_base import *  # noqa: F401,F403


PRIVACY_FIELD_ASSERTION_KIND = "privacy_field_policy"

# ── V1.5.0: Lazy one-time registration of multi-step protocols ──
_v150_protocols_registered = False


def _ensure_v150_protocols() -> None:
    """Register V1.5.0 multi-step protocols once (idempotent)."""
    global _v150_protocols_registered
    if _v150_protocols_registered:
        return
    _v150_protocols_registered = True
    try:
        from .multi_step_protocol import register_v150_multi_step_protocols
        register_v150_multi_step_protocols()
    except Exception:  # noqa: BLE001 - registration failure must not abort compile
        pass


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


def _compile_registered_protocol(
    registration: dict[str, Any],
    **envelope: Any,
) -> dict[str, Any]:
    """Run a registered protocol compiler and validate its plan.

    A registered protocol is not trusted more than a built-in one: its result goes through
    the same shape validation, and any failure becomes BLOCKED with the protocol id in the
    detail so the cause is attributable to the registration rather than to the obligation.
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
    # V1.5.0: ensure multi-step protocols are registered before first resolution
    _ensure_v150_protocols()

    # Registered protocol, consulted first and additively.
    #
    # On a MISS everything below runs verbatim, so all six family branches, both actor
    # guards, the built-in template dispatch and the terminal fallback behave exactly as
    # before. On a HIT the registered compiler answers and the result is stamped with
    # _registry_protocol_id so the obligation compiler can tell a registered plan from a
    # built-in one.
    #
    # Placed in this outermost facade rather than the base for two reasons: it leaves the
    # 626-line family if-chain unedited, and it bypasses the middle privacy facade whose
    # validation rewrite hard-requires exactly one control and one treatment step -- an
    # N-step registered plan routed through that guard would be blocked by a check written
    # for a different shape.
    #
    # A compiler that raises, or a result that fails validation, becomes a visible BLOCKED.
    # An exception must never escape into the compile loop, where it would abort a whole
    # batch of unrelated obligations.
    _registration = _resolve_family_protocol(risk_family, _text(property_spec.get("template")))
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
