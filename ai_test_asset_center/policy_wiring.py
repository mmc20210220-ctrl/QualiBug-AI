"""Policy wiring with non-negotiable runtime safety guardrails."""

from __future__ import annotations

from typing import Any


_REASONER_MAX_HYPOTHESES_PER_ENGINE = 15


def _clamp_reasoner_hypothesis_cap(value: Any, fallback: Any) -> int:
    """Keep policy data from silently widening the product hypothesis budget."""
    try:
        requested = int(value)
    except (TypeError, ValueError):
        try:
            requested = int(fallback)
        except (TypeError, ValueError):
            requested = _REASONER_MAX_HYPOTHESES_PER_ENGINE
    return max(1, min(requested, _REASONER_MAX_HYPOTHESES_PER_ENGINE))


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
        return _clamp_reasoner_hypothesis_cap(value, default)
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
                payload["max_hypotheses_per_engine"] = _clamp_reasoner_hypothesis_cap(
                    payload["max_hypotheses_per_engine"],
                    _REASONER_MAX_HYPOTHESES_PER_ENGINE,
                )
            return payload
    except Exception:
        pass
    return {}
