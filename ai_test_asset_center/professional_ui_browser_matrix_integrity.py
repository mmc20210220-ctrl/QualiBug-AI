"""Integrity fixes for the browser-matrix installer composition.

The formal observer registry stores the callable present at registration time, so
replacing ``formal_ui_surface._ui_observer_handler`` alone is insufficient.  This
installer rebinds that registered handler to the matrix-aware wrapper.  It also
avoids sending unsupported false mobile-emulation options to Firefox and converts
unexpected matrix orchestration exceptions into a typed blocked result.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import formal_ui_surface as _formal
from . import observer_contracts_base as _observers
from . import professional_ui_browser_matrix as _matrix
from . import ui_execution_adapter as _adapter

_INSTALL_MARKER = "_qualibug_browser_matrix_integrity_installed"
_ORIGINAL_CONTEXT = "_qualibug_matrix_context_before_integrity"
_ORIGINAL_ADAPTER = "_qualibug_matrix_final_adapter_before_integrity"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any, *, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def install_professional_ui_browser_matrix_integrity() -> None:
    if getattr(_matrix, _INSTALL_MARKER, False):
        return
    original_context = getattr(
        _matrix._MatrixBrowser,
        ORIGINAL_CONTEXT,
        _matrix._MatrixBrowser.new_context,
    )
    # This must capture the CURRENT adapter, which is the matrix-aware wrapper
    # installed immediately before this function. Reading the matrix module's
    # pre-matrix snapshot would silently restore the single-browser path.
    original_adapter = getattr(
        _adapter,
        ORIGINAL_ADAPTER,
        _adapter._playwright_request_result,
    )
    setattr(_matrix._MatrixBrowser, ORIGINAL_CONTEXT, original_context)
    setattr(_adapter, ORIGINAL_ADAPTER, original_adapter)

    def new_context_with_engine_compatibility(
        self: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        options = dict(kwargs)
        profile = _dict(getattr(self, "_profile", {}))
        options.update({
            "viewport": {
                "width": int(profile["viewport_width"]),
                "height": int(profile["viewport_height"]),
            },
            "device_scale_factor": float(profile["device_scale_factor"]),
            "locale": _text(profile.get("locale"), limit=40),
            "timezone_id": _text(profile.get("timezone_id"), limit=100),
            "color_scheme": _text(profile.get("color_scheme"), limit=20),
            "reduced_motion": _text(profile.get("reduced_motion"), limit=20),
        })
        engine = _text(profile.get("browser_engine"), limit=20).lower()
        if engine != "firefox" and profile.get("is_mobile") is True:
            options["is_mobile"] = True
        if profile.get("has_touch") is True:
            options["has_touch"] = True
        user_agent = _text(profile.get("user_agent"), limit=500)
        if user_agent:
            options["user_agent"] = user_agent
        browser = getattr(self, "_browser")
        return browser.new_context(*args, **options)

    def matrix_adapter_fail_closed(
        project_id: str,
        request: dict[str, Any],
        runtime_contract: dict[str, Any],
        *,
        root: Path,
        run_id: str,
    ) -> dict[str, Any]:
        try:
            return original_adapter(
                project_id,
                request,
                runtime_contract,
                root=root,
                run_id=run_id,
            )
        except Exception as exc:  # noqa: BLE001
            if not isinstance(_dict(request).get("browser_matrix"), dict):
                raise
            return _adapter._blocked_request_result(
                request,
                "UI_BROWSER_MATRIX_ORCHESTRATION_ERROR_"
                + _matrix._fingerprint(type(exc).__name__),
                root=root,
            )

    _matrix._MatrixBrowser.new_context = new_context_with_engine_compatibility
    _adapter._playwright_request_result = matrix_adapter_fail_closed
    # register_observer captured the old callable; update the one registered slot
    # rather than registering a second observer or changing its identity.
    if _formal.OBSERVER_ID in _observers._REGISTERED_OBSERVER_HANDLERS:
        _observers._REGISTERED_OBSERVER_HANDLERS[
            _formal.OBSERVER_ID
        ] = _formal._ui_observer_handler
    setattr(_matrix, _INSTALL_MARKER, True)


__all__ = ["install_professional_ui_browser_matrix_integrity"]
