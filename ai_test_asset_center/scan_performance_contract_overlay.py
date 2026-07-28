"""Overlay explicitly typed latency-budget contracts from immutable scan context.

The stable ``external_signal_requests`` channel is reused, but admission is strict: only rows
explicitly typed as a formal performance/latency contract are considered. Ordinary telemetry,
monitoring and health-check requests remain outside formal defect authority.

The first increment measures successful GET/HEAD latency only. Functional non-2xx responses,
retries, load, throughput and long-duration stability are deliberately outside this contract.
"""
from __future__ import annotations

import contextvars
import copy
import hashlib
import json
from typing import Any

SCAN_PERFORMANCE_CONTEXT: contextvars.ContextVar[dict[str, Any] | None] = (
    contextvars.ContextVar("qualibug_scan_performance_contract_context", default=None)
)
OVERLAY_SCHEMA = "qualibug.scan-performance-contract-overlay.v1"
_ALLOWED_TYPES = frozenset({
    "formal_performance_contract",
    "latency_budget_contract",
    "source_declared_latency_budget",
})
_SAFE_METHODS = frozenset({"GET", "HEAD"})


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _stable_id(*parts: Any) -> str:
    canonical = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return "scan_perf_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]


def bind_scan_performance_contract_context(context: dict[str, Any] | None) -> contextvars.Token:
    return SCAN_PERFORMANCE_CONTEXT.set(dict(context or {}))


def reset_scan_performance_contract_context(token: contextvars.Token) -> None:
    SCAN_PERFORMANCE_CONTEXT.reset(token)


def current_scan_performance_contract_context() -> dict[str, Any]:
    return dict(SCAN_PERFORMANCE_CONTEXT.get() or {})


def _is_formal_contract(row: dict[str, Any]) -> bool:
    schema = _text(row.get("schema_version"))
    kind = _text(
        row.get("signal_type")
        or row.get("contract_type")
        or row.get("kind")
        or row.get("type")
    ).lower()
    return bool(
        schema.startswith("qualibug.formal-performance-contract")
        or kind in _ALLOWED_TYPES
    )


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
        "kind": "formal_performance_contract",
        "quote_hash": _text(row.get("quote_hash")),
    }]


def _gap(contract_id: str, reason_code: str, refs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "gap_type": "performance_contract_not_source_bound",
        "reason_code": reason_code,
        "contract_id": contract_id,
        "source_id": _text(_dict((refs or [{}])[0]).get("source_id")) if refs else "",
        "description": "Explicit latency contract could not enter formal performance authority",
        "status": "unsupported",
    }


def _normalize(raw: dict[str, Any], *, index: int) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    row = copy.deepcopy(_dict(raw))
    contract_id = _text(row.get("contract_id") or row.get("id")) or _stable_id(index, row)
    refs = _source_refs(row)
    if not refs:
        return None, [_gap(contract_id, "FORMAL_PERFORMANCE_SOURCE_REF_MISSING")]
    operation_ref = _text(row.get("operation_ref") or row.get("operation_id"))
    method = _text(row.get("method") or row.get("http_method")).upper()
    operation_path = _text(row.get("operation_path") or row.get("api_path") or row.get("endpoint"))
    if not operation_ref and not (method and operation_path):
        return None, [_gap(contract_id, "FORMAL_PERFORMANCE_OPERATION_IDENTITY_MISSING", refs)]
    if not operation_ref and method not in _SAFE_METHODS:
        return None, [_gap(contract_id, "FORMAL_PERFORMANCE_GET_OR_HEAD_REQUIRED", refs)]
    actor_ref = _text(row.get("actor_ref") or row.get("actor_id"))
    actor_role = _text(row.get("actor_role") or row.get("role"))
    if not actor_ref and not actor_role:
        return None, [_gap(contract_id, "FORMAL_PERFORMANCE_ACTOR_IDENTITY_MISSING", refs)]
    try:
        sample_count = int(row.get("sample_count"))
        warmup_count = int(row.get("warmup_count") or 0)
        max_latency_ms = float(row.get("max_latency_ms"))
        max_error_rate = float(row.get("max_error_rate"))
        expected_status_class = int(row.get("expected_status_class"))
    except (TypeError, ValueError):
        return None, [_gap(contract_id, "FORMAL_PERFORMANCE_NUMERIC_FIELD_INVALID", refs)]
    percentile = _text(row.get("percentile")).lower()
    if sample_count < 3 or sample_count > 20:
        return None, [_gap(contract_id, "FORMAL_PERFORMANCE_SAMPLE_COUNT_INVALID", refs)]
    if warmup_count < 0 or warmup_count > 3:
        return None, [_gap(contract_id, "FORMAL_PERFORMANCE_WARMUP_COUNT_INVALID", refs)]
    if percentile not in {"p50", "p90", "p95", "p99", "max"}:
        return None, [_gap(contract_id, "FORMAL_PERFORMANCE_PERCENTILE_INVALID", refs)]
    if max_latency_ms <= 0 or max_latency_ms > 120_000:
        return None, [_gap(contract_id, "FORMAL_PERFORMANCE_THRESHOLD_INVALID", refs)]
    if max_error_rate != 0:
        return None, [_gap(contract_id, "FORMAL_PERFORMANCE_ZERO_ERROR_RATE_REQUIRED", refs)]
    if expected_status_class != 2:
        return None, [_gap(contract_id, "FORMAL_PERFORMANCE_SUCCESS_STATUS_CLASS_REQUIRED", refs)]

    normalized = {
        **row,
        "schema_version": "qualibug.formal-performance-contract.v1",
        "contract_id": contract_id,
        "source_refs": refs,
        "source_id": _text(refs[0].get("source_id")),
        "source_locator": _text(refs[0].get("locator")),
        "sample_count": sample_count,
        "warmup_count": warmup_count,
        "percentile": percentile,
        "max_latency_ms": max_latency_ms,
        "max_error_rate": 0.0,
        "expected_status_class": 2,
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
    return normalized, []


def overlay_scan_performance_contracts(
    asset: dict[str, Any] | None,
    campaign_context: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    merged = copy.deepcopy(_dict(asset))
    context = _dict(campaign_context) or current_scan_performance_contract_context()
    explicit = [
        copy.deepcopy(row)
        for row in _list(context.get("performance_formal_contracts"))
        if isinstance(row, dict)
    ]
    external = [
        copy.deepcopy(row)
        for row in _list(context.get("external_signal_requests"))
        if isinstance(row, dict) and _is_formal_contract(row)
    ]
    raw_contracts = [*explicit, *external]
    existing = [
        copy.deepcopy(row)
        for row in _list(merged.get("performance_formal_contracts"))
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
            gaps.append(_gap(contract_id, "FORMAL_PERFORMANCE_CONTRACT_ID_DUPLICATE", _source_refs(contract)))
            continue
        by_id[contract_id] = contract
        added += 1
    merged["performance_formal_contracts"] = list(by_id.values())
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
        "status": "OVERLAID" if added else "BLOCKED" if raw_contracts else "NOT_REQUESTED",
        "scan_contract_count": len(raw_contracts),
        "explicit_contract_count": len(explicit),
        "typed_external_contract_count": len(external),
        "contract_added_count": added,
        "coverage_gap_count": len(gaps),
        "telemetry_inferred_as_contract": False,
        "functional_non_2xx_judged_as_performance": False,
    }
    merged["scan_performance_contract_overlay_receipt"] = receipt
    return merged, receipt


__all__ = [
    "bind_scan_performance_contract_context",
    "overlay_scan_performance_contracts",
    "reset_scan_performance_contract_context",
]
