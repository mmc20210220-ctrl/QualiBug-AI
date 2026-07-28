"""Professional source-contract admission guard for direct scan UI requests.

Direct scan requests and enterprise UI documents must obey the same formal
contract boundary. This installer reuses the single enterprise contract
validator after the scan overlay has resolved source, operation and actor
identities. Invalid responsive, accessibility or governed interaction requests
remain visible scan coverage gaps and never enter Behavior IR.
"""
from __future__ import annotations

import copy
from typing import Any

from . import scan_ui_contract_overlay as _overlay
from .enterprise_knowledge_center import _formal_ui_contracts as _source_contracts
from .professional_ui_interaction_cleanup import INTERACTIVE_ACTIONS

_INSTALL_MARKER = "_qualibug_scan_ui_interaction_contract_guard_installed"
_ORIGINAL_MARKER = "_qualibug_original_scan_ui_contract_from_request"
_MODE_MATCH_REQUIREMENT = "ui_request_and_browser_plan_execution_mode_match"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _validation_gap(
    *,
    request: dict[str, Any],
    contract: dict[str, Any],
    missing: list[str],
) -> dict[str, Any]:
    request_id = _text(
        request.get("request_id")
        or request.get("id")
        or contract.get("contract_id")
    )
    refs = _overlay._source_refs(request)
    steps = _overlay._steps(request)
    interactive = any(
        _text(row.get("action")).lower() in INTERACTIVE_ACTIONS
        for row in steps
    )
    reason_code = (
        "FORMAL_UI_EXECUTION_MODE_MISMATCH"
        if missing == [_MODE_MATCH_REQUIREMENT]
        else "FORMAL_UI_INTERACTION_CONTRACT_INCOMPLETE"
        if interactive
        else "FORMAL_UI_SCAN_CONTRACT_INCOMPLETE"
    )
    return {
        "gap_type": "scan_ui_contract_not_source_bound",
        "reason_code": reason_code,
        "source_id": (
            _text(_dict(refs[0]).get("source_id")) if refs else ""
        ),
        "contract_id": request_id,
        "missing_requirements": list(dict.fromkeys(missing)),
        "description": (
            "Explicit scan UI contract failed the professional source-contract "
            "admission boundary"
        ),
        "status": "unsupported",
    }


def install_scan_ui_interaction_contract_guard() -> None:
    if getattr(_overlay, _INSTALL_MARKER, False):
        return
    original = getattr(
        _overlay,
        _ORIGINAL_MARKER,
        _overlay._contract_from_request,
    )
    setattr(_overlay, _ORIGINAL_MARKER, original)

    def contract_from_request_guarded(
        request: dict[str, Any],
        *,
        index: int,
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        contract, gaps = original(request, index=index)
        if contract is None:
            return None, gaps
        source_id = _text(contract.get("source_id"))
        locator = _text(contract.get("source_locator")) or (
            f"scan_ui_execution_requests[{int(index)}]"
        )
        validated, source_gap = _source_contracts._validate_contract(
            contract,
            source_id=source_id,
            locator=locator,
        )
        if not source_gap and validated is not None:
            # Preserve the original immutable source refs and exact resolved identities,
            # but retain parser normalization of request/plan mode and metadata. Dropping
            # this normalized request would let downstream adapter defaults reintroduce
            # a false safe-read-only mode.
            normalized = copy.deepcopy(contract)
            normalized["ui_request"] = copy.deepcopy(
                _dict(validated.get("ui_request"))
            )
            return normalized, gaps
        missing = [
            _text(value)
            for value in _list(_dict(source_gap).get("missing_requirements"))
            if _text(value)
        ]
        return None, [
            *gaps,
            _validation_gap(
                request=request,
                contract=contract,
                missing=missing or ["professional_ui_contract_validation"],
            ),
        ]

    _overlay._contract_from_request = contract_from_request_guarded
    setattr(_overlay, _INSTALL_MARKER, True)


__all__ = ["install_scan_ui_interaction_contract_guard"]
