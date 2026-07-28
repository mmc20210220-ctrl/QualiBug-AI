"""Strict runtime facade for the formal UI surface.

Keeps runtime approval and persisted finding fingerprints outside the generic
contract evaluator. The underlying evaluator builds the receipt chain; this
facade supplies the actual approved runtime contract and removes only
post-adjudication convenience references that are not part of the sealed finding
payload.
"""
from __future__ import annotations

from typing import Any

from .ui_formal_surface import (
    formalize_browser_ui_contracts,
    normalize_ui_formal_contracts,
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def runtime_contract_from_result(result: dict[str, Any]) -> dict[str, Any]:
    for candidate in (
        _dict(result.get("runtime_contract")),
        _dict(_dict(result.get("v12")).get("runtime_contract")),
        _dict(_dict(result.get("discovery_runtime")).get("runtime_contract")),
    ):
        if candidate:
            return dict(candidate)
    return {}


def _strip_unsealed_convenience_refs(finding: dict[str, Any]) -> dict[str, Any]:
    """Return the exact finding payload the gate sealed plus allowed derived fields."""
    return {
        key: value
        for key, value in dict(finding).items()
        if key not in {
            "observer_receipt_ids",
            "oracle_receipt_id",
            "reproduction_receipt_id",
        }
    }


def _blocked_result(
    result: dict[str, Any],
    contracts: Any,
    reason_code: str,
) -> dict[str, Any]:
    updated = dict(result or {})
    normalized = normalize_ui_formal_contracts(contracts)
    updated["formal_ui_contracts"] = {
        "schema_version": "qualibug.formal-ui-contracts.v1",
        "requested": len(normalized),
        "evaluated": len(normalized),
        "deliverable_count": 0,
        "blocked_count": len(normalized),
        "rejected_count": 0,
        "outcomes": [
            {
                "contract_id": _text(row.get("contract_id")),
                "status": "BLOCKED",
                "reason_codes": [reason_code],
                "finding": None,
            }
            for row in normalized
        ],
        "provider_findings_promoted": 0,
    }
    return updated


def formalize_browser_ui_contracts_strict(
    result: dict[str, Any],
    *,
    browser_ui_report: dict[str, Any],
    contracts: Any,
    runtime_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the UI authority only for an approved target and stable sealed payloads."""
    runtime = dict(_dict(runtime_contract) or runtime_contract_from_result(_dict(result)))
    if _text(runtime.get("status")) != "approved" or not _text(
        runtime.get("approved_base_url")
    ):
        return _blocked_result(
            _dict(result),
            contracts,
            "UI_APPROVED_RUNTIME_CONTRACT_REQUIRED",
        )

    updated = formalize_browser_ui_contracts(
        _dict(result),
        browser_ui_report=_dict(browser_ui_report),
        contracts=contracts,
        runtime_contract=runtime,
    )
    updated["findings"] = [
        _strip_unsealed_convenience_refs(row)
        for row in _list(updated.get("findings"))
        if isinstance(row, dict)
    ]
    updated["ui_findings"] = [
        _strip_unsealed_convenience_refs(row)
        for row in _list(updated.get("ui_findings"))
        if isinstance(row, dict)
    ]
    formal = _dict(updated.get("formal_ui_contracts"))
    outcomes = []
    for raw in _list(formal.get("outcomes")):
        row = dict(_dict(raw))
        if isinstance(row.get("finding"), dict):
            row["finding"] = _strip_unsealed_convenience_refs(row["finding"])
        outcomes.append(row)
    if formal:
        updated["formal_ui_contracts"] = {**formal, "outcomes": outcomes}
    return updated


__all__ = [
    "formalize_browser_ui_contracts_strict",
    "runtime_contract_from_result",
]
