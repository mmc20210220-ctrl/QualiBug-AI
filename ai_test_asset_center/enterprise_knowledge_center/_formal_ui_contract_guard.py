"""Fail-closed guard for direct JSON-array formal UI contract parsing.

A UI/UX source can legitimately be a JSON array of components, frames or labels. Treating every
object in such an array as an attempted formal contract creates false coverage gaps. Direct
array entries are admitted only when the source explicitly marks them as a formal contract or
they carry the complete contract envelope identities. Objects nested under the named
``ui_formal_contracts`` / ``ui_contracts`` keys retain the existing validation path, including
visible incomplete-contract gaps.
"""
from __future__ import annotations

from typing import Any

from . import _formal_ui_contracts as _contracts

_INSTALL_MARKER = "_qualibug_formal_ui_root_array_guard_installed"
_ORIGINAL_MARKER = "_qualibug_original_contract_rows_from_json"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _explicit_direct_contract(value: Any) -> bool:
    row = _dict(value)
    if not row:
        return False
    if _text(row.get("schema_version")).startswith("qualibug.ui-formal-contract"):
        return True

    request = _dict(row.get("ui_request"))
    operation_identity = bool(
        _text(row.get("operation_ref") or row.get("operation_id"))
        or (
            _text(row.get("method") or row.get("http_method"))
            and _text(row.get("operation_path") or row.get("api_path") or row.get("endpoint"))
        )
    )
    actor_identity = bool(
        _text(row.get("actor_ref") or row.get("actor_id"))
        or _text(row.get("actor_role") or row.get("role"))
    )
    return bool(
        _text(row.get("contract_id") or request.get("request_id"))
        and request
        and operation_identity
        and actor_identity
    )


def install_formal_ui_root_array_guard() -> None:
    """Replace only direct-array candidate collection; named contract containers are unchanged."""
    if getattr(_contracts, _INSTALL_MARKER, False):
        return
    original = getattr(
        _contracts,
        _ORIGINAL_MARKER,
        _contracts._contract_rows_from_json,
    )
    setattr(_contracts, _ORIGINAL_MARKER, original)

    def contract_rows_from_json_guarded(text: str) -> list[tuple[str, dict[str, Any]]]:
        rows: list[tuple[str, dict[str, Any]]] = []
        for root_locator, root in _contracts._json_roots(text):
            candidates: list[Any] = []
            if isinstance(root, dict):
                for key in ("ui_formal_contracts", "ui_contracts"):
                    candidates.extend(_contracts._list(root.get(key)))
                if _text(root.get("schema_version")).startswith("qualibug.ui-formal-contract"):
                    candidates.append(root)
            elif isinstance(root, list):
                candidates.extend(item for item in root if _explicit_direct_contract(item))
            for index, candidate in enumerate(candidates, start=1):
                if isinstance(candidate, dict):
                    rows.append((
                        f"{root_locator}:ui_formal_contracts[{index}]",
                        dict(candidate),
                    ))
        return rows

    _contracts._contract_rows_from_json = contract_rows_from_json_guarded
    setattr(_contracts, _INSTALL_MARKER, True)


__all__ = ["install_formal_ui_root_array_guard"]
