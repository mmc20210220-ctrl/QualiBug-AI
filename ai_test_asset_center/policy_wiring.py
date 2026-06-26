"""Phase81: Policy wiring — read strategy params from Policy Registry with fallback."""

from __future__ import annotations
from typing import Any


def get_policy_value(section: str, key: str, default: Any) -> Any:
    """Read a value from the active Policy Registry strategy. Falls back to default."""
    try:
        from .policy_registry import get_active_policy
        strategy = get_active_policy()
        section_obj = getattr(strategy, section, None)
        if section_obj and hasattr(section_obj, key):
            return getattr(section_obj, key)
    except Exception:
        pass
    return default


def get_policy_dict(section: str) -> dict:
    """Get all values from a policy section as a dict."""
    try:
        from .policy_registry import get_active_policy
        strategy = get_active_policy()
        section_obj = getattr(strategy, section, None)
        if section_obj:
            return {k: v for k, v in section_obj.__dict__.items() if not k.startswith('_')}
    except Exception:
        pass
    return {}
