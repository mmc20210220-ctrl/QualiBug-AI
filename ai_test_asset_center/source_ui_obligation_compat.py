"""Compatibility guard for source-UI obligation family statistics.

The source UI compiler replaces one misclassified validation obligation with a formal UI
obligation. Its first implementation rebuilt ``by_family`` only from families present in the
run, dropping the zero-valued canonical keys that reporting and regression baselines expect.
This guard keeps the established complete family vector while adding the registered UI family.
"""
from __future__ import annotations

from typing import Any

from . import source_ui_obligation_binding as _ui

_INSTALL_MARKER = "_qualibug_source_ui_family_vector_compat_installed"
_ORIGINAL_MARKER = "_qualibug_original_compile_obligations_with_source_ui"


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def install_source_ui_family_vector_compat() -> None:
    """Normalize ``by_family`` after the one authoritative UI compiler runs."""
    if getattr(_ui, _INSTALL_MARKER, False):
        return
    original = getattr(
        _ui,
        _ORIGINAL_MARKER,
        _ui.compile_obligations_with_source_ui,
    )
    setattr(_ui, _ORIGINAL_MARKER, original)

    def compile_with_complete_family_vector(
        behavior_ir: dict[str, Any],
        *,
        base_compile: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        result = dict(original(behavior_ir, base_compile=base_compile, **kwargs))
        obligations = [
            row
            for row in _list(result.get("obligations"))
            if isinstance(row, dict)
        ]
        from .test_obligation import canonical_risk_families

        families = {
            _text(value)
            for value in canonical_risk_families()
            if _text(value)
        }
        families.update(
            _text(value)
            for value in dict(result.get("by_family") or {})
            if _text(value)
        )
        families.update(
            _text(row.get("risk_family"))
            for row in obligations
            if _text(row.get("risk_family"))
        )
        result["by_family"] = {
            family: sum(
                1 for row in obligations if _text(row.get("risk_family")) == family
            )
            for family in sorted(families)
        }
        receipt = dict(result.get("source_ui_obligation_receipt") or {})
        if receipt:
            receipt["complete_family_vector"] = True
            receipt["family_key_count"] = len(result["by_family"])
            result["source_ui_obligation_receipt"] = receipt
        return result

    _ui.compile_obligations_with_source_ui = compile_with_complete_family_vector
    setattr(_ui, _INSTALL_MARKER, True)


__all__ = ["install_source_ui_family_vector_compat"]
