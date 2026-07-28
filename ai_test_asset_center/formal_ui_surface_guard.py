"""Safety facade for the first formal UI protocol increment.

The generic browser adapter can execute approved click/fill/select actions.  The formal UI
surface does not yet have a browser-side cleanup-equivalence contract, so admitting those
steps would let an observer mutate customer state outside the governed write finalizer.  This
facade keeps the first formal increment read-only and re-registers the same protocol identities
with the guarded compiler.
"""
from __future__ import annotations

from typing import Any

from . import formal_ui_surface as _ui

_READ_ONLY_ACTIONS = frozenset({
    "goto",
    "expect_text",
    "expect_url",
    "wait_for_load",
    "screenshot",
})
_INSTALL_MARKER = "_qualibug_formal_ui_read_only_guard_installed"
_ORIGINAL_MARKER = "_qualibug_original_compile_ui_protocol"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def install_formal_ui_read_only_guard() -> None:
    """Install a fail-closed compiler guard and replace the registered protocol entries."""

    if getattr(_ui, _INSTALL_MARKER, False):
        return
    original = getattr(_ui, _ORIGINAL_MARKER, _ui._compile_ui_protocol)
    setattr(_ui, _ORIGINAL_MARKER, original)

    def compile_read_only_ui_protocol(envelope: dict[str, Any]) -> dict[str, Any]:
        property_spec = _dict(_dict(envelope).get("property_spec"))
        request = _ui._declared_ui_request(property_spec)
        if not request:
            return original(envelope)
        browser_plan = _dict(request.get("browser_plan"))
        if not browser_plan:
            return {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_BINDING",
                "detail": "ui_browser_plan_not_an_object",
            }
        steps = [
            dict(row)
            for row in _list(browser_plan.get("steps"))
            if isinstance(row, dict)
        ]
        unsupported = sorted({
            _text(row.get("action")).lower()
            for row in steps
            if _text(row.get("action")).lower() not in _READ_ONLY_ACTIONS
        })
        if unsupported:
            return {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_TARGET_POLICY",
                "detail": (
                    "formal_ui_interaction_requires_cleanup_equivalence:"
                    + ",".join(unsupported)
                ),
            }
        return original(envelope)

    _ui._compile_ui_protocol = compile_read_only_ui_protocol

    from .experiment_protocol_registry import register_family_protocol

    for family in (_ui.RISK_FAMILY, "visibility", "state"):
        register_family_protocol(
            family,
            _ui.PROTOCOL_TEMPLATE,
            compiler=compile_read_only_ui_protocol,
            observers=(_ui.OBSERVER_ID,),
            assertion_kind=_ui.ASSERTION_KIND,
            emits_control=False,
            per_step_evidence=False,
        )
    setattr(_ui, _INSTALL_MARKER, True)


__all__ = ["install_formal_ui_read_only_guard"]
