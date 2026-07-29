"""Fail closed when direct-scan formal UI requests invent source identity.

Primary source registries are authoritative and only active records may grant
formal UI authority.  Derived operations/rules are used only as a compatibility
fallback when the asset has no registry surface at all; stale provenance attached
to a rule can therefore never override an explicit active inventory.
"""
from __future__ import annotations

import contextvars
import copy
import hashlib
import sys
from typing import Any

from . import scan_ui_contract_overlay as _overlay

_SCHEMA_VERSION = "qualibug.scan-ui-source-registry-guard.v2"
_INSTALL_MARKER = "_qualibug_scan_ui_source_registry_guard_installed"
_ORIGINAL_MARKER = "_qualibug_overlay_before_source_registry_guard"
_PRIMARY_KEYS = frozenset({
    "sources",
    "source_registry",
    "source_inventory",
    "enterprise_sources",
    "knowledge_sources",
})
_FALLBACK_KEYS = frozenset({
    "operations",
    "interfaces",
    "rules",
    "rule_library",
    "roles",
    "permissions",
    "permission_matrix",
    "state_machines",
    "tickets",
    "tables",
    "data_tables",
    "field_dictionary",
    "ui_specs",
    "ui_design_specs",
    "documents",
})
_ENVELOPES = (
    "enterprise_knowledge",
    "knowledge_asset",
    "business_knowledge",
)
_INACTIVE = frozenset({
    "archived",
    "deleted",
    "disabled",
    "inactive",
    "revoked",
    "superseded",
})


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, *, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:20]


def _active(row: dict[str, Any]) -> bool:
    status = _text(row.get("status"), limit=40).lower()
    return status not in _INACTIVE


def _collect_source_ids(
    value: Any,
    output: set[str],
    *,
    active_only: bool,
) -> None:
    if isinstance(value, dict):
        source_id = _text(value.get("source_id"), limit=300)
        if source_id and (not active_only or _active(value)):
            output.add(source_id)
        for key, child in value.items():
            if key in {
                "ui_formal_contracts",
                "ui_contracts",
                "ui_execution_requests",
            }:
                # Requests/contracts are claims, never independent source proof.
                continue
            if isinstance(child, (dict, list)):
                _collect_source_ids(child, output, active_only=active_only)
    elif isinstance(value, list):
        for child in value:
            _collect_source_ids(child, output, active_only=active_only)


def _primary_surfaces(root: dict[str, Any]) -> list[Any]:
    surfaces: list[Any] = []
    for key in _PRIMARY_KEYS:
        if key in root:
            surfaces.append(root.get(key))
    for envelope_key in _ENVELOPES:
        envelope = root.get(envelope_key)
        if not isinstance(envelope, dict):
            continue
        for key in _PRIMARY_KEYS:
            if key in envelope:
                surfaces.append(envelope.get(key))
    return surfaces


def _fallback_surfaces(root: dict[str, Any]) -> list[Any]:
    surfaces: list[Any] = []
    for key in _FALLBACK_KEYS:
        if key in root:
            surfaces.append(root.get(key))
    for envelope_key in _ENVELOPES:
        envelope = root.get(envelope_key)
        if not isinstance(envelope, dict):
            continue
        for key in _FALLBACK_KEYS:
            if key in envelope:
                surfaces.append(envelope.get(key))
    return surfaces


def _trusted_source_identity(
    asset: dict[str, Any] | None,
) -> tuple[set[str], str, bool]:
    root = _dict(asset)
    primary = _primary_surfaces(root)
    trusted: set[str] = set()
    if primary:
        for surface in primary:
            _collect_source_ids(surface, trusted, active_only=True)
        return trusted, "primary_active_source_inventory", True
    fallback = _fallback_surfaces(root)
    for surface in fallback:
        _collect_source_ids(surface, trusted, active_only=True)
    return (
        trusted,
        "derived_provenance_fallback" if fallback else "no_source_surface",
        False,
    )


def _bound_scan_context() -> dict[str, Any] | None:
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
        refs = [
            row
            for row in _list(request.get("source_refs"))
            if isinstance(row, dict)
        ]
        claimed = [
            _text(row.get("source_id"), limit=300)
            for row in refs
            if _text(row.get("source_id"), limit=300)
        ]
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
                "reason_code": "UI_SCAN_SOURCE_ID_NOT_IN_ACTIVE_ENTERPRISE_ASSET",
            })
        sanitized.append(request)
    context["ui_execution_requests"] = sanitized
    return context, rejected


def _base_overlay() -> Any:
    return getattr(
        _overlay,
        _ORIGINAL_MARKER,
        _overlay.overlay_scan_ui_contracts,
    )


def overlay_scan_ui_contracts_with_source_registry(
    asset: dict[str, Any] | None,
    scan_context: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    trusted, trust_mode, primary_present = _trusted_source_identity(asset)
    effective_context = scan_context if scan_context is not None else _bound_scan_context()
    base = _base_overlay()
    if effective_context is None:
        overlaid, receipt = base(asset)
        guarded_receipt = dict(_dict(receipt))
        guarded_receipt["source_registry_guard"] = {
            "schema_version": _SCHEMA_VERSION,
            "status": "NOT_APPLICABLE",
            "trust_mode": trust_mode,
            "provenance_surface_present": primary_present,
            "trusted_source_count": len(trusted),
            "rejected_request_count": 0,
            "rejections": [],
            "raw_source_ids_in_receipt": False,
        }
        return overlaid, guarded_receipt
    sanitized, rejected = _sanitized_scan_context(effective_context, trusted)
    overlaid, receipt = base(asset, sanitized)
    guarded_receipt = dict(_dict(receipt))
    guarded_receipt["source_registry_guard"] = {
        "schema_version": _SCHEMA_VERSION,
        "status": "REJECTED" if rejected else "ACCEPTED",
        "trust_mode": trust_mode,
        "provenance_surface_present": primary_present,
        "trusted_source_count": len(trusted),
        "rejected_request_count": len(rejected),
        "rejections": rejected,
        "raw_source_ids_in_receipt": False,
    }
    return overlaid, guarded_receipt


def install_scan_ui_source_registry_guard() -> None:
    current = _overlay.overlay_scan_ui_contracts
    if getattr(current, _INSTALL_MARKER, False):
        setattr(_overlay, _INSTALL_MARKER, True)
        return
    original = getattr(_overlay, _ORIGINAL_MARKER, current)
    setattr(_overlay, _ORIGINAL_MARKER, original)
    setattr(overlay_scan_ui_contracts_with_source_registry, _INSTALL_MARKER, True)
    _overlay.overlay_scan_ui_contracts = (
        overlay_scan_ui_contracts_with_source_registry
    )
    setattr(_overlay, _INSTALL_MARKER, True)
    # Hot-loaded planning modules may already hold the old alias.
    for module_name in (
        "ai_test_asset_center.discovery_runtime_semantic_binding",
        "ai_test_asset_center.discovery_runtime_planning",
    ):
        module = sys.modules.get(module_name)
        if module is not None and getattr(
            module,
            "overlay_scan_ui_contracts",
            None,
        ) is original:
            module.overlay_scan_ui_contracts = (
                overlay_scan_ui_contracts_with_source_registry
            )


__all__ = [
    "install_scan_ui_source_registry_guard",
    "overlay_scan_ui_contracts_with_source_registry",
]
