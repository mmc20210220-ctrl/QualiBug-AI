"""Overlay explicit scan event contracts onto the enterprise knowledge asset.

This module is an admission layer only. It never creates obligations, executes the target or
produces findings. Contracts must preserve exact source, operation, actor and observer
identities; every missing leg becomes a visible gap.
"""
from __future__ import annotations

import contextvars
import copy
import hashlib
import json
from typing import Any

SCAN_EVENT_CONTRACT_CONTEXT: contextvars.ContextVar[dict[str, Any] | None] = (
    contextvars.ContextVar("qualibug_scan_event_contract_context", default=None)
)
OVERLAY_SCHEMA = "qualibug.scan-event-contract-overlay.v1"
_REQUIRED_TEXT_FIELDS = (
    "observer_path",
    "events_path",
    "event_id_field",
    "event_type_field",
    "correlation_field",
    "correlation_query_parameter",
    "expected_event_type",
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _stable_id(prefix: str, *parts: Any) -> str:
    canonical = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return f"{prefix}_{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:20]}"


def bind_scan_event_contract_context(context: dict[str, Any] | None) -> contextvars.Token:
    return SCAN_EVENT_CONTRACT_CONTEXT.set(dict(context or {}))


def reset_scan_event_contract_context(token: contextvars.Token) -> None:
    SCAN_EVENT_CONTRACT_CONTEXT.reset(token)


def current_scan_event_contract_context() -> dict[str, Any]:
    return dict(SCAN_EVENT_CONTRACT_CONTEXT.get() or {})


def _source_refs(contract: dict[str, Any]) -> list[dict[str, Any]]:
    refs = [
        copy.deepcopy(row)
        for row in _list(contract.get("source_refs"))
        if isinstance(row, dict) and _text(row.get("source_id"))
    ]
    if refs:
        return refs
    source_id = _text(contract.get("source_id"))
    if not source_id:
        return []
    return [{
        "source_id": source_id,
        "version": _text(contract.get("source_version")),
        "locator": _text(contract.get("source_locator")),
        "kind": "formal_event_contract",
        "quote_hash": _text(contract.get("quote_hash")),
    }]


def _gap(
    contract_id: str,
    reason_code: str,
    *,
    refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "gap_type": "scan_event_contract_not_source_bound",
        "reason_code": reason_code,
        "contract_id": contract_id,
        "source_id": _text(_dict((refs or [{}])[0]).get("source_id")) if refs else "",
        "description": "Explicit event contract could not enter the formal event chain",
        "status": "unsupported",
    }


def _normalized_contract(
    raw: dict[str, Any],
    *,
    index: int,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    row = copy.deepcopy(_dict(raw))
    contract_id = _text(row.get("contract_id") or row.get("id")) or _stable_id(
        "scan_event", index, row
    )
    refs = _source_refs(row)
    gaps: list[dict[str, Any]] = []
    if not refs:
        return None, [_gap(contract_id, "FORMAL_EVENT_SOURCE_REF_MISSING")]

    operation_ref = _text(row.get("operation_ref") or row.get("operation_id"))
    method = _text(row.get("method") or row.get("http_method")).upper()
    operation_path = _text(row.get("operation_path") or row.get("api_path") or row.get("endpoint"))
    if not operation_ref and not (method and operation_path):
        return None, [_gap(contract_id, "FORMAL_EVENT_OPERATION_IDENTITY_MISSING", refs=refs)]

    actor_ref = _text(row.get("actor_ref") or row.get("actor_id"))
    actor_role = _text(row.get("actor_role") or row.get("role"))
    if not actor_ref and not actor_role:
        return None, [_gap(contract_id, "FORMAL_EVENT_ACTOR_IDENTITY_MISSING", refs=refs)]

    missing = [field for field in _REQUIRED_TEXT_FIELDS if not _text(row.get(field))]
    if missing:
        return None, [_gap(
            contract_id,
            "FORMAL_EVENT_FIELDS_MISSING:" + ",".join(missing),
            refs=refs,
        )]
    observer_path = _text(row.get("observer_path"))
    if not (
        observer_path.startswith("/")
        and not observer_path.startswith("//")
        and "://" not in observer_path
        and ".." not in observer_path.split("?")[0].split("/")
    ):
        return None, [_gap(contract_id, "FORMAL_EVENT_OBSERVER_PATH_INVALID", refs=refs)]

    correlation_source = _dict(row.get("correlation_source"))
    correlation_value = row.get("correlation_value")
    if correlation_value is None and not (
        _text(correlation_source.get("location"))
        and _text(correlation_source.get("path"))
    ):
        return None, [_gap(contract_id, "FORMAL_EVENT_CORRELATION_SOURCE_MISSING", refs=refs)]
    try:
        minimum = int(row.get("expected_min_count"))
        maximum = int(row.get("expected_max_count"))
        window_ms = int(row.get("observation_window_ms"))
    except (TypeError, ValueError):
        return None, [_gap(contract_id, "FORMAL_EVENT_COUNT_OR_WINDOW_INVALID", refs=refs)]
    if minimum < 0 or maximum < minimum or maximum > 200:
        return None, [_gap(contract_id, "FORMAL_EVENT_COUNT_RANGE_INVALID", refs=refs)]
    if window_ms <= 0 or window_ms > 30_000:
        return None, [_gap(contract_id, "FORMAL_EVENT_WINDOW_INVALID", refs=refs)]

    normalized = {
        **row,
        "schema_version": "qualibug.formal-event-contract.v1",
        "contract_id": contract_id,
        "source_refs": refs,
        "source_id": _text(refs[0].get("source_id")),
        "source_locator": _text(refs[0].get("locator")),
        "observer_path": observer_path,
        "expected_min_count": minimum,
        "expected_max_count": maximum,
        "observation_window_ms": window_ms,
        "status": "accepted",
        "derivation": "explicit",
        "confidence": 1.0,
    }
    if operation_ref:
        normalized["operation_ref"] = operation_ref
    else:
        normalized["method"] = method
        normalized["operation_path"] = operation_path
    if actor_ref:
        normalized["actor_ref"] = actor_ref
    else:
        normalized["actor_role"] = actor_role
    return normalized, gaps


def overlay_scan_event_contracts(
    asset: dict[str, Any] | None,
    campaign_context: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    merged = copy.deepcopy(_dict(asset))
    context = _dict(campaign_context) or current_scan_event_contract_context()
    raw_contracts = [
        dict(row)
        for row in _list(context.get("event_formal_contracts"))
        if isinstance(row, dict)
    ]
    existing = [
        copy.deepcopy(row)
        for row in _list(merged.get("event_formal_contracts"))
        if isinstance(row, dict)
    ]
    by_id = {
        _text(row.get("contract_id")): row
        for row in existing
        if _text(row.get("contract_id"))
    }
    gaps: list[dict[str, Any]] = []
    admitted = 0
    for index, raw in enumerate(raw_contracts, start=1):
        contract, row_gaps = _normalized_contract(raw, index=index)
        gaps.extend(row_gaps)
        if contract is None:
            continue
        contract_id = _text(contract.get("contract_id"))
        if contract_id in by_id:
            gaps.append(_gap(
                contract_id,
                "FORMAL_EVENT_CONTRACT_ID_DUPLICATE",
                refs=_source_refs(contract),
            ))
            continue
        by_id[contract_id] = contract
        admitted += 1

    merged["event_formal_contracts"] = list(by_id.values())
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
        "status": "OVERLAID" if admitted else "BLOCKED" if raw_contracts else "NOT_REQUESTED",
        "scan_contract_count": len(raw_contracts),
        "contract_added_count": admitted,
        "coverage_gap_count": len(gaps),
        "raw_event_payloads_consumed": False,
    }
    merged["scan_event_contract_overlay_receipt"] = receipt
    return merged, receipt


__all__ = [
    "OVERLAY_SCHEMA",
    "bind_scan_event_contract_context",
    "current_scan_event_contract_context",
    "overlay_scan_event_contracts",
    "reset_scan_event_contract_context",
]
