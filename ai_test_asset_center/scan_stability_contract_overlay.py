"""Overlay source-declared short-window read-stability contracts.

Only explicitly typed contracts are admitted. Telemetry and ordinary health checks
are never promoted into defect authority. The first increment is limited to exact
GET/HEAD operations and 5-20 sequential samples.
"""
from __future__ import annotations

import contextvars
import copy
import hashlib
import json
from typing import Any

STABILITY_CONTEXT: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "qualibug_scan_stability_contract_context", default=None
)
OVERLAY_SCHEMA = "qualibug.scan-stability-contract-overlay.v1"
_ALLOWED_TYPES = frozenset({
    "formal_stability_contract",
    "read_reliability_contract",
    "source_declared_read_stability",
})
_SAFE_METHODS = frozenset({"GET", "HEAD"})


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _stable_id(*parts: Any) -> str:
    blob = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return "scan_stability_" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:20]


def bind_scan_stability_contract_context(context: dict[str, Any] | None) -> contextvars.Token:
    return STABILITY_CONTEXT.set(dict(context or {}))


def reset_scan_stability_contract_context(token: contextvars.Token) -> None:
    STABILITY_CONTEXT.reset(token)


def current_scan_stability_contract_context() -> dict[str, Any]:
    return dict(STABILITY_CONTEXT.get() or {})


def _source_refs(row: dict[str, Any]) -> list[dict[str, Any]]:
    refs = [
        copy.deepcopy(ref)
        for ref in _list(row.get("source_refs"))
        if isinstance(ref, dict) and _text(ref.get("source_id"))
    ]
    if refs:
        return refs
    source_id = _text(row.get("source_id"))
    if not source_id:
        return []
    return [{
        "source_id": source_id,
        "version": _text(row.get("source_version")),
        "locator": _text(row.get("source_locator")),
        "kind": "formal_stability_contract",
        "quote_hash": _text(row.get("quote_hash")),
    }]


def _is_typed(row: dict[str, Any]) -> bool:
    schema = _text(row.get("schema_version"))
    kind = _text(
        row.get("contract_type") or row.get("signal_type") or row.get("kind") or row.get("type")
    ).lower()
    return schema.startswith("qualibug.formal-stability-contract") or kind in _ALLOWED_TYPES


def _gap(contract_id: str, reason: str, refs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "gap_type": "stability_contract_not_source_bound",
        "reason_code": reason,
        "contract_id": contract_id,
        "source_id": _text(_dict((refs or [{}])[0]).get("source_id")) if refs else "",
        "description": "Explicit stability contract could not enter formal authority",
        "status": "unsupported",
    }


def _normalize(row: dict[str, Any], *, index: int) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    raw = copy.deepcopy(_dict(row))
    contract_id = _text(raw.get("contract_id") or raw.get("id")) or _stable_id(index, raw)
    refs = _source_refs(raw)
    if not refs:
        return None, [_gap(contract_id, "FORMAL_STABILITY_SOURCE_REF_MISSING")]
    operation_ref = _text(raw.get("operation_ref") or raw.get("operation_id"))
    method = _text(raw.get("method") or raw.get("http_method")).upper()
    path = _text(raw.get("operation_path") or raw.get("api_path") or raw.get("endpoint"))
    if not operation_ref and not (method and path):
        return None, [_gap(contract_id, "FORMAL_STABILITY_OPERATION_IDENTITY_MISSING", refs)]
    if not operation_ref and method not in _SAFE_METHODS:
        return None, [_gap(contract_id, "FORMAL_STABILITY_GET_OR_HEAD_REQUIRED", refs)]
    actor_ref = _text(raw.get("actor_ref") or raw.get("actor_id"))
    actor_role = _text(raw.get("actor_role") or raw.get("role"))
    if not actor_ref and not actor_role:
        return None, [_gap(contract_id, "FORMAL_STABILITY_ACTOR_IDENTITY_MISSING", refs)]
    try:
        sample_count = int(raw.get("sample_count"))
        max_failed = int(raw.get("max_failed_samples"))
        max_retried = int(raw.get("max_retried_samples"))
        status_class = int(raw.get("expected_status_class"))
    except (TypeError, ValueError):
        return None, [_gap(contract_id, "FORMAL_STABILITY_NUMERIC_FIELD_INVALID", refs)]
    if not 5 <= sample_count <= 20:
        return None, [_gap(contract_id, "FORMAL_STABILITY_SAMPLE_COUNT_INVALID", refs)]
    if not 0 <= max_failed <= sample_count:
        return None, [_gap(contract_id, "FORMAL_STABILITY_FAILED_SAMPLE_BUDGET_INVALID", refs)]
    if not 0 <= max_retried <= sample_count:
        return None, [_gap(contract_id, "FORMAL_STABILITY_RETRY_SAMPLE_BUDGET_INVALID", refs)]
    if status_class != 2:
        return None, [_gap(contract_id, "FORMAL_STABILITY_SUCCESS_STATUS_CLASS_REQUIRED", refs)]
    normalized = {
        **raw,
        "schema_version": "qualibug.formal-stability-contract.v1",
        "contract_id": contract_id,
        "source_refs": refs,
        "source_id": _text(refs[0].get("source_id")),
        "source_locator": _text(refs[0].get("locator")),
        "sample_count": sample_count,
        "max_failed_samples": max_failed,
        "max_retried_samples": max_retried,
        "expected_status_class": 2,
        "status": "accepted",
        "derivation": "explicit",
        "confidence": 1.0,
    }
    if operation_ref:
        normalized["operation_ref"] = operation_ref
    else:
        normalized["method"] = method
        normalized["operation_path"] = path
    if actor_ref:
        normalized["actor_ref"] = actor_ref
    else:
        normalized["actor_role"] = actor_role
    return normalized, []


def overlay_scan_stability_contracts(
    asset: dict[str, Any] | None,
    campaign_context: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    merged = copy.deepcopy(_dict(asset))
    context = _dict(campaign_context) or current_scan_stability_contract_context()
    explicit = [
        copy.deepcopy(row)
        for row in _list(context.get("stability_formal_contracts"))
        if isinstance(row, dict)
    ]
    external = [
        copy.deepcopy(row)
        for row in _list(context.get("external_signal_requests"))
        if isinstance(row, dict) and _is_typed(row)
    ]
    raw_contracts = [*explicit, *external]
    existing = [
        copy.deepcopy(row)
        for row in _list(merged.get("stability_formal_contracts"))
        if isinstance(row, dict)
    ]
    by_id = {
        _text(row.get("contract_id")): row
        for row in existing
        if _text(row.get("contract_id"))
    }
    gaps: list[dict[str, Any]] = []
    added = 0
    for index, raw in enumerate(raw_contracts, start=1):
        contract, row_gaps = _normalize(raw, index=index)
        gaps.extend(row_gaps)
        if contract is None:
            continue
        contract_id = _text(contract.get("contract_id"))
        if contract_id in by_id:
            gaps.append(_gap(contract_id, "FORMAL_STABILITY_CONTRACT_ID_DUPLICATE", _source_refs(contract)))
            continue
        by_id[contract_id] = contract
        added += 1
    merged["stability_formal_contracts"] = list(by_id.values())
    merged["coverage_gaps"] = [
        *[copy.deepcopy(row) for row in _list(merged.get("coverage_gaps")) if isinstance(row, dict)],
        *gaps,
    ]
    receipt = {
        "schema_version": OVERLAY_SCHEMA,
        "status": "OVERLAID" if added else "BLOCKED" if raw_contracts else "NOT_REQUESTED",
        "scan_contract_count": len(raw_contracts),
        "contract_added_count": added,
        "coverage_gap_count": len(gaps),
        "health_telemetry_inferred_as_contract": False,
        "long_duration_stability_claimed": False,
    }
    merged["scan_stability_contract_overlay_receipt"] = receipt
    return merged, receipt


__all__ = [
    "bind_scan_stability_contract_context",
    "overlay_scan_stability_contracts",
    "reset_scan_stability_contract_context",
]
