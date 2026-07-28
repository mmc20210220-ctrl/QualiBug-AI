"""Overlay explicit scan UI requests onto the enterprise knowledge asset.

The repository already has one formal UI authority:

    ui_formal_contracts -> Behavior IR invariant/relation
    -> Test Obligation -> experiment protocol -> observer -> assertion -> Oracle

This module only translates customer-submitted scan contracts into that existing
source shape. It does not create obligations or findings itself.

Admission is deliberately strict:

* auto-generated landing-page screenshots remain ordinary smoke evidence;
* source references are mandatory;
* the provider and browser plan remain exactly what the customer submitted;
* operation and actor identities are copied, never guessed;
* invalid rows become visible source coverage gaps rather than disappearing.
"""
from __future__ import annotations

import contextvars
import copy
import hashlib
import json
from typing import Any


SCAN_UI_CONTRACT_CONTEXT: contextvars.ContextVar[dict[str, Any] | None] = (
    contextvars.ContextVar(
        "qualibug_scan_ui_contract_context",
        default=None,
    )
)
OVERLAY_SCHEMA = "qualibug.scan-ui-contract-overlay.v1"
_EXPECTATION_ACTIONS = frozenset({"expect_text", "expect_url"})


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _stable_id(prefix: str, *parts: Any) -> str:
    canonical = json.dumps(
        parts,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return f"{prefix}_{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:20]}"


def bind_scan_ui_contract_context(
    context: dict[str, Any] | None,
) -> contextvars.Token:
    return SCAN_UI_CONTRACT_CONTEXT.set(dict(context or {}))


def reset_scan_ui_contract_context(token: contextvars.Token) -> None:
    SCAN_UI_CONTRACT_CONTEXT.reset(token)


def current_scan_ui_contract_context() -> dict[str, Any]:
    return dict(SCAN_UI_CONTRACT_CONTEXT.get() or {})


def _source_refs(request: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        copy.deepcopy(row)
        for row in _list(request.get("source_refs"))
        if isinstance(row, dict) and _text(row.get("source_id"))
    ]


def _steps(request: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        copy.deepcopy(row)
        for row in _list(_dict(request.get("browser_plan")).get("steps"))
        if isinstance(row, dict)
    ]


def _is_auto_generated(request: dict[str, Any]) -> bool:
    return _dict(request.get("metadata")).get("auto_generated") is True


def _looks_formal(request: dict[str, Any]) -> bool:
    if _is_auto_generated(request):
        return False
    return bool(
        _source_refs(request)
        or _dict(request.get("success_criteria"))
        or any(
            _text(step.get("action")).lower() in _EXPECTATION_ACTIONS
            for step in _steps(request)
        )
    )


def _gap(
    *,
    request_id: str,
    reason_code: str,
    source_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "gap_type": "scan_ui_contract_not_source_bound",
        "reason_code": reason_code,
        "source_id": (
            _text(_dict((source_refs or [{}])[0]).get("source_id"))
            if source_refs
            else ""
        ),
        "contract_id": request_id,
        "description": "Explicit scan UI contract could not enter the formal source UI chain",
        "status": "unsupported",
    }


def _contract_from_request(
    request: dict[str, Any],
    *,
    index: int,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    request_id = _text(request.get("request_id") or request.get("id")) or f"scan_ui_{index}"
    refs = _source_refs(request)
    gaps: list[dict[str, Any]] = []
    if not refs:
        gaps.append(_gap(
            request_id=request_id,
            reason_code="FORMAL_UI_SOURCE_REF_MISSING",
        ))
        return None, gaps

    provider = _text(request.get("provider")).lower()
    if provider != "playwright_browser_plan":
        gaps.append(_gap(
            request_id=request_id,
            reason_code="FORMAL_UI_PROVIDER_NOT_SOURCE_DECLARED",
            source_refs=refs,
        ))
        return None, gaps

    start_url = _text(request.get("start_url") or request.get("url"))
    if not start_url:
        gaps.append(_gap(
            request_id=request_id,
            reason_code="FORMAL_UI_START_URL_MISSING",
            source_refs=refs,
        ))
        return None, gaps

    steps = _steps(request)
    if not steps:
        gaps.append(_gap(
            request_id=request_id,
            reason_code="FORMAL_UI_BROWSER_PLAN_MISSING",
            source_refs=refs,
        ))
        return None, gaps
    if not any(
        _text(step.get("action")).lower() in _EXPECTATION_ACTIONS
        for step in steps
    ):
        gaps.append(_gap(
            request_id=request_id,
            reason_code="FORMAL_UI_EXPECTATION_MISSING",
            source_refs=refs,
        ))
        return None, gaps

    operation_ref = _text(request.get("operation_ref") or request.get("operation_id"))
    business_operation = _dict(request.get("business_operation"))
    operation_ref = operation_ref or _text(
        business_operation.get("operation_ref")
        or business_operation.get("operation_id")
    )
    method = _text(request.get("method") or business_operation.get("method")).upper()
    operation_path = _text(
        request.get("path")
        or request.get("operation_path")
        or business_operation.get("path")
    )
    if not operation_ref and not (method and operation_path):
        gaps.append(_gap(
            request_id=request_id,
            reason_code="FORMAL_UI_OPERATION_IDENTITY_MISSING",
            source_refs=refs,
        ))
        return None, gaps

    actor_ref = _text(request.get("actor_ref") or request.get("actor_id"))
    actor_role = _text(request.get("actor_role") or request.get("role"))
    if not actor_ref and not actor_role:
        gaps.append(_gap(
            request_id=request_id,
            reason_code="FORMAL_UI_ACTOR_IDENTITY_MISSING",
            source_refs=refs,
        ))
        return None, gaps

    source_request = copy.deepcopy(request)
    source_request["request_id"] = request_id
    source_request["provider"] = "playwright_browser_plan"
    source_request["start_url"] = start_url
    source_request["browser_plan"] = {
        **_dict(request.get("browser_plan")),
        "steps": steps,
    }
    source_request["source_refs"] = refs

    contract: dict[str, Any] = {
        "contract_id": request_id,
        "title": _text(request.get("title") or request.get("task")) or request_id,
        "source_refs": refs,
        "source_id": _text(refs[0].get("source_id")),
        "source_locator": _text(refs[0].get("locator")),
        "ui_request": source_request,
    }
    if operation_ref:
        contract["operation_ref"] = operation_ref
    else:
        contract["method"] = method
        contract["operation_path"] = operation_path
    if actor_ref:
        contract["actor_ref"] = actor_ref
    else:
        contract["actor_role"] = actor_role
    return contract, gaps


def overlay_scan_ui_contracts(
    asset: dict[str, Any] | None,
    campaign_context: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a copied asset plus source-backed formal UI contracts and gaps."""
    merged = copy.deepcopy(_dict(asset))
    context = _dict(campaign_context) or current_scan_ui_contract_context()
    raw_requests = [
        dict(row)
        for row in _list(context.get("ui_execution_requests"))
        if isinstance(row, dict)
    ]
    formal_requests = [row for row in raw_requests if _looks_formal(row)]

    existing = [
        copy.deepcopy(row)
        for row in _list(merged.get("ui_formal_contracts"))
        if isinstance(row, dict)
    ]
    contracts_by_id = {
        _text(row.get("contract_id")): row
        for row in existing
        if _text(row.get("contract_id"))
    }
    gaps: list[dict[str, Any]] = []
    admitted = 0
    for index, request in enumerate(formal_requests, start=1):
        contract, row_gaps = _contract_from_request(request, index=index)
        gaps.extend(row_gaps)
        if contract is None:
            continue
        contract_id = _text(contract.get("contract_id"))
        if contract_id in contracts_by_id:
            gaps.append(_gap(
                request_id=contract_id,
                reason_code="FORMAL_UI_CONTRACT_ID_DUPLICATE",
                source_refs=_source_refs(request),
            ))
            continue
        contracts_by_id[contract_id] = contract
        admitted += 1

    merged["ui_formal_contracts"] = list(contracts_by_id.values())
    merged["coverage_gaps"] = [
        *[
            copy.deepcopy(row)
            for row in _list(merged.get("coverage_gaps"))
            if isinstance(row, dict)
        ],
        *gaps,
    ]
    receipt = {
        "schema_version": OVERLAY_SCHEMA,
        "status": (
            "OVERLAID"
            if admitted
            else "BLOCKED"
            if formal_requests
            else "NOT_REQUESTED"
        ),
        "scan_request_count": len(raw_requests),
        "formal_candidate_count": len(formal_requests),
        "contract_added_count": admitted,
        "coverage_gap_count": len(gaps),
        "auto_generated_request_count": sum(
            1 for row in raw_requests if _is_auto_generated(row)
        ),
        "provider_findings_consumed": False,
    }
    merged["scan_ui_contract_overlay_receipt"] = receipt
    return merged, receipt


__all__ = [
    "OVERLAY_SCHEMA",
    "bind_scan_ui_contract_context",
    "current_scan_ui_contract_context",
    "overlay_scan_ui_contracts",
    "reset_scan_ui_contract_context",
]
