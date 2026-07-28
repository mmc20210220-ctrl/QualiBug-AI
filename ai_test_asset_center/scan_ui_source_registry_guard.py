"""Fail closed when direct-scan formal UI requests invent source identity.

A structurally valid ``source_refs`` row is not proof that the referenced
enterprise document exists. This wrapper joins claimed source ids against the
current enterprise knowledge asset before the existing scan overlay can create a
formal contract. Unknown or mixed ids lose formal authority and are handed to
the original overlay with empty ``source_refs`` so its established coverage-gap
path remains the only outcome.

The guard deliberately does not affect ordinary screenshot/smoke requests that
do not claim source authority.
"""
from __future__ import annotations

import contextvars
import copy
import hashlib
from typing import Any

from . import scan_ui_contract_overlay as _overlay

_SCHEMA_VERSION = "qualibug.scan-ui-source-registry-guard.v1"
_AUTHORITATIVE_ASSET_KEYS = frozenset({
    "sources",
    "source_registry",
    "source_inventory",
    "enterprise_sources",
    "knowledge_sources",
    "operations",
    "rules",
    "roles",
    "permissions",
    "state_machines",
    "tickets",
    "tables",
    "field_dictionary",
    "ui_specs",
    "documents",
})


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, *, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:20]


def _collect_source_ids(value: Any, output: set[str]) -> None:
    if isinstance(value, dict):
        source_id = _text(value.get("source_id"), limit=300)
        if source_id:
            output.add(source_id)
        for key, child in value.items():
            if key in {"ui_formal_contracts", "ui_contracts", "ui_execution_requests"}:
                # These may have been created by a previous scan overlay and are
                # therefore not independent proof of enterprise source existence.
                continue
            if isinstance(child, (dict, list)):
                _collect_source_ids(child, output)
    elif isinstance(value, list):
        for child in value:
            _collect_source_ids(child, output)


def _trusted_source_ids(asset: dict[str, Any] | None) -> set[str]:
    root = _dict(asset)
    trusted: set[str] = set()
    for key in _AUTHORITATIVE_ASSET_KEYS:
        if key in root:
            _collect_source_ids(root.get(key), trusted)
    # Some builders place the enterprise payload under one explicit envelope.
    for key in ("enterprise_knowledge", "knowledge_asset", "business_knowledge"):
        envelope = root.get(key)
        if isinstance(envelope, (dict, list)):
            _collect_source_ids(envelope, trusted)
    return trusted


def _bound_scan_context() -> dict[str, Any] | None:
    # The base module intentionally keeps its ContextVar private. Discover only
    # a dict-valued context that actually carries UI execution requests; no
    # mutation or global fallback is attempted.
    for value in vars(_overlay).values():
        if not isinstance(value, contextvars.ContextVar):
            continue
        try:
            candidate = value.get()
        except LookupError:
            continue
        if isinstance(candidate, dict) and "ui_execution_requests" in candidate:
            return candidate
    return None


def _sanitized_scan_context(
    scan_context: dict[str, Any],
    trusted: set[str],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    context = copy.deepcopy(_dict(scan_context))
    requests = _list(context.get("ui_execution_requests"))
    sanitized: list[Any] = []
    rejected: list[dict[str, Any]] = []
    for index, value in enumerate(requests, start=1):
        if not isinstance(value, dict):
            sanitized.append(value)
            continue
        request = copy.deepcopy(value)
        refs = [row for row in _list(request.get("source_refs")) if isinstance(row, dict)]
        claimed = [_text(row.get("source_id"), limit=300) for row in refs]
        claimed = [source_id for source_id in claimed if source_id]
        unknown = sorted(set(claimed) - trusted)
        if claimed and unknown:
            request["source_refs"] = []
            rejected.append({
                "request_id_fingerprint": _fingerprint(
                    request.get("request_id") or request.get("id") or index
                ),
                "claimed_source_count": len(set(claimed)),
                "unknown_source_count": len(unknown),
                "unknown_source_id_fingerprints": [
                    _fingerprint(source_id) for source_id in unknown
                ],
                "reason_code": "UI_SCAN_SOURCE_ID_NOT_IN_ENTERPRISE_ASSET",
            })
        sanitized.append(request)
    context["ui_execution_requests"] = sanitized
    return context, rejected


def overlay_scan_ui_contracts_with_source_registry(
    asset: dict[str, Any] | None,
    scan_context: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    trusted = _trusted_source_ids(asset)
    effective_context = scan_context
    if effective_context is None:
        effective_context = _bound_scan_context()
    if effective_context is None:
        overlaid, receipt = _overlay.overlay_scan_ui_contracts(asset)
        guarded_receipt = dict(_dict(receipt))
        guarded_receipt["source_registry_guard"] = {
            "schema_version": _SCHEMA_VERSION,
            "status": "NOT_APPLICABLE",
            "trusted_source_count": len(trusted),
            "rejected_request_count": 0,
            "rejections": [],
        }
        return overlaid, guarded_receipt

    sanitized, rejected = _sanitized_scan_context(effective_context, trusted)
    overlaid, receipt = _overlay.overlay_scan_ui_contracts(asset, sanitized)
    guarded_receipt = dict(_dict(receipt))
    guarded_receipt["source_registry_guard"] = {
        "schema_version": _SCHEMA_VERSION,
        "status": "REJECTED" if rejected else "ACCEPTED",
        "trusted_source_count": len(trusted),
        "rejected_request_count": len(rejected),
        "rejections": rejected,
        "raw_source_ids_in_receipt": False,
    }
    return overlaid, guarded_receipt


__all__ = [
    "overlay_scan_ui_contracts_with_source_registry",
]
