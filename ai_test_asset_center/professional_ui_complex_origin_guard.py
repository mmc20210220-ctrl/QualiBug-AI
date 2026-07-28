"""Require exact HTTP origins for iframe and popup authority."""
from __future__ import annotations

from typing import Any

from . import professional_ui_complex_interactions as _complex
from . import professional_ui_interaction_cleanup as _interaction

_INSTALL_MARKER = "_qualibug_complex_origin_guard_installed"
_ORIGINAL_FRAME_VALIDATOR = "_qualibug_frame_validator_before_exact_origin"
_ORIGINAL_APPROVED_ORIGINS = "_qualibug_approved_origins_before_exact_guard"


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, *, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def _exact_origin(value: Any) -> str:
    raw = _text(value, limit=2000).rstrip("/")
    origin = _complex._origin(raw)
    return origin if raw and raw.lower() == origin else ""


def install_professional_ui_complex_origin_guard() -> None:
    if getattr(_complex, _INSTALL_MARKER, False):
        return
    original_frame = getattr(
        _complex,
        _ORIGINAL_FRAME_VALIDATOR,
        _complex._validate_frame_scope,
    )
    original_origins = getattr(
        _complex,
        _ORIGINAL_APPROVED_ORIGINS,
        _complex._approved_origins,
    )
    setattr(_complex, _ORIGINAL_FRAME_VALIDATOR, original_frame)
    setattr(_complex, _ORIGINAL_APPROVED_ORIGINS, original_origins)

    def validate_frame_with_exact_origin(step: dict[str, Any]) -> None:
        raw = _text(step.get("frame_origin"), limit=2000)
        if raw and not _exact_origin(raw):
            raise _interaction._browser.BrowserExecutionError(
                "browser_frame_origin_exact_http_origin_required"
            )
        original_frame(step)

    def approved_exact_origins(
        runtime_contract: dict[str, Any],
        field: str,
    ) -> set[str]:
        origins = {_complex._origin(runtime_contract.get("approved_base_url"))}
        for value in _list(runtime_contract.get(field)):
            exact = _exact_origin(value)
            if not exact:
                raise RuntimeError(f"UI_{field.upper()}_EXACT_ORIGIN_REQUIRED")
            origins.add(exact)
        return {value for value in origins if value}

    _complex._validate_frame_scope = validate_frame_with_exact_origin
    _complex._approved_origins = approved_exact_origins
    setattr(_complex, _INSTALL_MARKER, True)


__all__ = [
    "install_professional_ui_complex_origin_guard",
]
