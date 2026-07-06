"""Policy wiring with non-negotiable runtime safety guardrails."""

from __future__ import annotations

import importlib
import os
import sys
from typing import Any


_REASONER_MAX_HYPOTHESES_PER_ENGINE = 15
_REASONER_HYPOTHESIS_CAP_ENV = "QUALIBUG_REASONER_MAX_HYPOTHESES_PER_ENGINE"
_BEHAVIOR_SLICE_MAX_PER_ROUND = 15
_INCREMENTAL_DISCOVERY_ROUND_MAX = 12
_BEHAVIOR_SLICE_EXECUTION_DEFAULTS: dict[str, int] = {
    "max_behavior_slices_per_round": _BEHAVIOR_SLICE_MAX_PER_ROUND,
    "incremental_discovery_round": 1,
    "incremental_discovery_round_limit": 3,
}
_BEHAVIOR_SLICE_EXECUTION_ENV: dict[str, str] = {
    "max_behavior_slices_per_round": "QUALIBUG_MAX_BEHAVIOR_SLICES_PER_ROUND",
    "incremental_discovery_round": "QUALIBUG_DISCOVERY_ROUND",
    "incremental_discovery_round_limit": "QUALIBUG_INCREMENTAL_DISCOVERY_ROUND_LIMIT",
}
_REASONER_GUARDRAIL_LIMIT_NAMES = ("MAX_HYPOTHESES", "MAX_HYPOTHESES_HARD_LIMIT")


def _loaded_stage_reasoner_module() -> Any | None:
    """Return the stage Reasoner module when it can be safely reached."""
    module_name = f"{__package__}.stage_reason_all_v2"
    module = sys.modules.get(module_name)
    if module is not None:
        return module
    try:
        return importlib.import_module(module_name)
    except Exception:
        return None


def _enforce_stage_reasoner_static_cap() -> None:
    """Harden the main Reasoner module against legacy widened defaults.

    ``stage_reason_all_v2`` historically exposed larger module-level defaults.
    Product calls resolve policy through this module, so enforce the canonical
    cap on the actual module before returning a reasoner budget. Import failures
    are deliberately ignored because this guardrail must not mask unrelated
    startup errors; explicit assertions below catch drift once the module loads.
    """
    module = _loaded_stage_reasoner_module()
    if module is None:
        return
    for name in _REASONER_GUARDRAIL_LIMIT_NAMES:
        if hasattr(module, name):
            setattr(module, name, _REASONER_MAX_HYPOTHESES_PER_ENGINE)


def assert_reasoner_guardrails() -> None:
    """Fail fast if runtime reasoner guardrails drift below/above policy.

    This is intentionally a runtime assertion rather than a health badge: it only
    validates in-process configuration after code is imported. It does not claim
    external LLM connectivity or product readiness.
    """
    _enforce_stage_reasoner_static_cap()
    module = _loaded_stage_reasoner_module()
    if module is not None:
        for name in _REASONER_GUARDRAIL_LIMIT_NAMES:
            if hasattr(module, name):
                value = getattr(module, name)
                assert value == _REASONER_MAX_HYPOTHESES_PER_ENGINE, (
                    f"{name} must remain {_REASONER_MAX_HYPOTHESES_PER_ENGINE}, got {value}"
                )


def _clamp_reasoner_hypothesis_cap(value: Any, fallback: Any) -> int:
    """Keep policy or environment data from widening the product budget."""
    try:
        requested = int(value)
    except (TypeError, ValueError):
        try:
            requested = int(fallback)
        except (TypeError, ValueError):
            requested = _REASONER_MAX_HYPOTHESES_PER_ENGINE
    return max(1, min(requested, _REASONER_MAX_HYPOTHESES_PER_ENGINE))


def _reasoner_hypothesis_cap(value: Any, fallback: Any) -> int:
    """Apply the optional environment override through the same hard cap."""
    _enforce_stage_reasoner_static_cap()
    environment_value = os.environ.get(_REASONER_HYPOTHESIS_CAP_ENV)
    return _clamp_reasoner_hypothesis_cap(
        environment_value if environment_value not in (None, "") else value,
        fallback,
    )


def _bounded_int(value: Any, fallback: Any, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        try:
            parsed = int(fallback)
        except (TypeError, ValueError):
            parsed = minimum
    return max(minimum, min(parsed, maximum))


def _behavior_slice_execution_value(key: str, value: Any, fallback: Any) -> int:
    """Apply one canonical guardrail to policy and environment inputs."""
    environment_name = _BEHAVIOR_SLICE_EXECUTION_ENV[key]
    environment_value = os.environ.get(environment_name)
    effective = environment_value if environment_value not in (None, "") else value
    if key == "max_behavior_slices_per_round":
        return _bounded_int(effective, fallback, 1, _BEHAVIOR_SLICE_MAX_PER_ROUND)
    return _bounded_int(effective, fallback, 1, _INCREMENTAL_DISCOVERY_ROUND_MAX)


def get_policy_value(section: str, key: str, default: Any) -> Any:
    """Read an active policy value while enforcing product-level guardrails."""
    value = default
    try:
        from .policy_registry import get_active_policy

        strategy = get_active_policy()
        section_obj = getattr(strategy, section, None)
        if section_obj and hasattr(section_obj, key):
            value = getattr(section_obj, key)
    except Exception:
        value = default

    if section == "reasoner" and key == "max_hypotheses_per_engine":
        return _reasoner_hypothesis_cap(value, default)
    if section == "execution" and key in _BEHAVIOR_SLICE_EXECUTION_DEFAULTS:
        return _behavior_slice_execution_value(key, value, default)
    return value


def get_policy_dict(section: str) -> dict[str, Any]:
    """Get active policy values, including guardrail-normalized values."""
    try:
        from .policy_registry import get_active_policy

        strategy = get_active_policy()
        section_obj = getattr(strategy, section, None)
        if section_obj:
            payload = {key: value for key, value in section_obj.__dict__.items() if not key.startswith("_")}
            if section == "reasoner" and "max_hypotheses_per_engine" in payload:
                payload["max_hypotheses_per_engine"] = _reasoner_hypothesis_cap(
                    payload["max_hypotheses_per_engine"],
                    _REASONER_MAX_HYPOTHESES_PER_ENGINE,
                )
            if section == "execution":
                for key, default in _BEHAVIOR_SLICE_EXECUTION_DEFAULTS.items():
                    payload[key] = _behavior_slice_execution_value(key, payload.get(key, default), default)
            return payload
    except Exception:
        pass
    return {}
