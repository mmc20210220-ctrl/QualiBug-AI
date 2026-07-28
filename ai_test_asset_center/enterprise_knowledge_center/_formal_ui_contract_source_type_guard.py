"""Classify explicit formal-UI JSON documents as ``uiux_spec`` by structure.

The generic source classifier historically relied on filename/UI vocabulary.
A contract-helper export named ``orders-visual-contract.json`` can contain a
perfect ``ui_formal_contracts`` array but no words such as wireframe, prototype
or component, so it was stored as a collaboration document and the formal UI
parser never ran.

This guard recognizes only explicit formal contract structures. It does not
upgrade ordinary screenshots, prototypes or arbitrary JSON into executable
contracts.
"""
from __future__ import annotations

import json
from typing import Any

from . import _crud
from . import _parsing

_INSTALL_MARKER = "_qualibug_formal_ui_contract_source_type_guard_installed"
_ORIGINAL_MULTI = "_qualibug_source_multi_before_formal_ui_guard"
_ORIGINAL_PRIMARY = "_qualibug_source_primary_before_formal_ui_guard"


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _formal_contract_json(text: str) -> bool:
    stripped = str(text or "").strip()
    if not stripped.startswith(("{", "[")):
        return False
    try:
        root = json.loads(stripped)
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    if isinstance(root, dict):
        if _list(root.get("ui_formal_contracts")) or _list(root.get("ui_contracts")):
            return True
        return str(root.get("schema_version") or "").startswith(
            "qualibug.ui-formal-contract"
        ) and isinstance(root.get("ui_request"), dict)
    if isinstance(root, list):
        return any(
            isinstance(row, dict)
            and (
                isinstance(row.get("ui_request"), dict)
                or str(row.get("schema_version") or "").startswith(
                    "qualibug.ui-formal-contract"
                )
            )
            for row in root
        )
    return False


def install_formal_ui_contract_source_type_guard() -> None:
    if getattr(_parsing, _INSTALL_MARKER, False):
        return
    original_multi = getattr(
        _parsing,
        _ORIGINAL_MULTI,
        _parsing._classify_source_multi,
    )
    original_primary = getattr(
        _parsing,
        _ORIGINAL_PRIMARY,
        _parsing._classify_source,
    )
    setattr(_parsing, _ORIGINAL_MULTI, original_multi)
    setattr(_parsing, _ORIGINAL_PRIMARY, original_primary)

    def classify_multi_with_formal_ui(
        name: str,
        text: str,
        explicit: str | None = None,
    ) -> list[str]:
        labels = list(original_multi(name, text, explicit))
        if not _formal_contract_json(text):
            return labels
        return ["uiux_spec", *[label for label in labels if label != "uiux_spec"]]

    def classify_primary_with_formal_ui(
        name: str,
        text: str,
        explicit: str | None = None,
    ) -> str:
        labels = classify_multi_with_formal_ui(name, text, explicit)
        return labels[0] if labels else original_primary(name, text, explicit)

    _parsing._classify_source_multi = classify_multi_with_formal_ui
    _parsing._classify_source = classify_primary_with_formal_ui
    # ``_crud`` imported the function directly during package composition, so
    # update that bound authority as well; otherwise HTTP/file ingestion would
    # continue using the stale classifier while direct parser callers differed.
    _crud._classify_source = classify_primary_with_formal_ui
    setattr(_parsing, _INSTALL_MARKER, True)


__all__ = ["install_formal_ui_contract_source_type_guard"]
