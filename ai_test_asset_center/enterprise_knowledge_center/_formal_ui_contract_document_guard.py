"""Do not parse a formal-UI contract document container as another contract.

``_contract_rows_from_json`` supports both:

* a document containing ``ui_formal_contracts`` / ``ui_contracts`` arrays; and
* one standalone object whose schema is ``qualibug.ui-formal-contract.*``.

When a document container also declares that schema prefix, the legacy parser
expanded its children and then appended the whole container as a second
candidate. The container has no operation/actor/ui_request fields, so every
valid document produced one phantom ``FORMAL_UI_CONTRACT_INCOMPLETE`` gap.

This guard keeps standalone-contract support while filtering only structural
containers that already yielded child rows.
"""
from __future__ import annotations

from typing import Any

from . import _formal_ui_contracts as _contracts

_INSTALL_MARKER = "_qualibug_formal_ui_contract_document_guard_installed"
_ORIGINAL_MARKER = "_qualibug_contract_rows_before_document_guard"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _is_document_container(row: dict[str, Any]) -> bool:
    has_children = bool(
        _list(row.get("ui_formal_contracts"))
        or _list(row.get("ui_contracts"))
    )
    if not has_children:
        return False
    # A real standalone contract has executable identity at its own level. A
    # pure document envelope does not, even if it labels the document with the
    # same schema family for transport/versioning purposes.
    return not bool(
        _dict(row.get("ui_request"))
        or row.get("operation_ref")
        or row.get("operation_id")
        or row.get("method")
        or row.get("http_method")
        or row.get("actor_ref")
        or row.get("actor_role")
    )


def install_formal_ui_contract_document_guard() -> None:
    if getattr(_contracts, _INSTALL_MARKER, False):
        return
    original = getattr(
        _contracts,
        _ORIGINAL_MARKER,
        _contracts._contract_rows_from_json,
    )
    setattr(_contracts, _ORIGINAL_MARKER, original)

    def rows_without_document_container(
        text: str,
    ) -> list[tuple[str, dict[str, Any]]]:
        return [
            (locator, row)
            for locator, row in original(text)
            if not _is_document_container(row)
        ]

    _contracts._contract_rows_from_json = rows_without_document_container
    setattr(_contracts, _INSTALL_MARKER, True)


__all__ = ["install_formal_ui_contract_document_guard"]
