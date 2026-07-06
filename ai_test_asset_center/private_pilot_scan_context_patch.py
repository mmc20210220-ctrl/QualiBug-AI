from __future__ import annotations

"""Scan campaign-context patch installer for private-pilot deployments.

This module owns the runtime wiring that binds frontend scan metadata to the
legacy scanner call path. It deliberately reuses the proven source-manifest
helpers and context variables from ``private_pilot_server`` so the first
extraction step changes ownership of the installer without changing the P0 scan
semantics.
"""

from pathlib import Path
from typing import Any

from ai_test_asset_center import private_pilot_server as _server_patch
from ai_test_asset_center import private_pilot_service as _service


def restore_scan_campaign_context_patch() -> None:
    """Restore only the scan campaign-context bridge, leaving delivery gate intact."""
    original_scan = getattr(_service, "_ORIGINAL_V12_SCAN", None)
    original_handler = getattr(_service, "_ORIGINAL_HANDLE_V12_SCAN", None)
    original_continuous_start = getattr(_service, "_ORIGINAL_HANDLE_CONTINUOUS_START", None)
    original_continuous_loop = getattr(_service, "_ORIGINAL_CONTINUOUS_SCAN_LOOP", None)
    if original_scan is not None:
        from ai_test_asset_center import __main__ as scanner_module

        scanner_module.scan = original_scan
    if original_handler is not None:
        _service.PrivatePilotHandler._handle_v12_scan = original_handler
    if original_continuous_start is not None:
        _service.PrivatePilotHandler._handle_continuous_start = original_continuous_start
    if original_continuous_loop is not None:
        _service._continuous_scan_loop = original_continuous_loop

    _service._SCAN_CAMPAIGN_CONTEXT_PATCHED = False  # type: ignore[attr-defined]
    _service._SCAN_CAMPAIGN_CONTEXT_PATCH_SOURCE = ""  # type: ignore[attr-defined]
    _service._ORIGINAL_V12_SCAN = None  # type: ignore[attr-defined]
    _service._ORIGINAL_HANDLE_V12_SCAN = None  # type: ignore[attr-defined]
    _service._ORIGINAL_HANDLE_CONTINUOUS_START = None  # type: ignore[attr-defined]
    _service._ORIGINAL_CONTINUOUS_SCAN_LOOP = None  # type: ignore[attr-defined]
    _server_patch._CONTINUOUS_CAMPAIGN_CONTEXTS.clear()  # type: ignore[attr-defined]


def install_scan_campaign_context_patch(*, patch_source: str) -> None:
    """Install the scan and continuous-discovery campaign context bridge."""
    if getattr(_service, "_SCAN_CAMPAIGN_CONTEXT_PATCHED", False):
        return

    from ai_test_asset_center import __main__ as scanner_module

    original_scan = getattr(scanner_module, "scan")
    original_handler = getattr(_service.PrivatePilotHandler, "_handle_v12_scan")
    original_continuous_start = getattr(_service.PrivatePilotHandler, "_handle_continuous_start")
    original_continuous_loop = getattr(_service, "_continuous_scan_loop")

    def _scan_with_campaign_context(*args: Any, **kwargs: Any) -> dict[str, Any]:
        pending_context = _server_patch._SCAN_CAMPAIGN_CONTEXT.get()  # type: ignore[attr-defined]
        if pending_context:
            explicit_context = kwargs.get("campaign_context")
            merged = dict(explicit_context) if isinstance(explicit_context, dict) else {}
            for key, value in pending_context.items():
                if key == "base_url":
                    continue
                if value and (key not in merged or not merged.get(key)):
                    merged[key] = value
            kwargs["campaign_context"] = merged
            if pending_context.get("base_url") and not kwargs.get("base_url"):
                kwargs["base_url"] = str(pending_context["base_url"]).rstrip("/")
        return original_scan(*args, **kwargs)

    def _handle_v12_scan_with_campaign_context(
        self: Any,
        project: str,
        root: Path,
        actor: dict[str, str],
        body: dict[str, Any],
    ) -> Any:
        prepared_body = _server_patch._prepare_scan_body_for_campaign(project, root, body)  # type: ignore[attr-defined]
        campaign_context = _server_patch._build_campaign_context_from_scan_body(prepared_body)  # type: ignore[attr-defined]
        token = _server_patch._SCAN_CAMPAIGN_CONTEXT.set(campaign_context or None)  # type: ignore[attr-defined]
        try:
            return original_handler(self, project, root, actor, prepared_body)
        finally:
            _server_patch._SCAN_CAMPAIGN_CONTEXT.reset(token)  # type: ignore[attr-defined]

    def _handle_continuous_start_with_campaign_context(
        self: Any,
        project: str,
        root: Path,
        actor: dict[str, str],
        body: dict[str, Any],
    ) -> Any:
        prepared_body = _server_patch._prepare_scan_body_for_campaign(project, root, body)  # type: ignore[attr-defined]
        campaign_context = _server_patch._build_campaign_context_from_scan_body(prepared_body)  # type: ignore[attr-defined]
        if campaign_context:
            key = _server_patch._continuous_context_key(root, project)  # type: ignore[attr-defined]
            _server_patch._CONTINUOUS_CAMPAIGN_CONTEXTS[key] = campaign_context  # type: ignore[attr-defined]
        return original_continuous_start(self, project, root, actor, prepared_body)

    def _continuous_scan_loop_with_campaign_context(root: Path, project: str, tenant_id: str, interval_s: int) -> Any:
        key = _server_patch._continuous_context_key(root, project)  # type: ignore[attr-defined]
        campaign_context = _server_patch._CONTINUOUS_CAMPAIGN_CONTEXTS.get(key)  # type: ignore[attr-defined]
        token = _server_patch._SCAN_CAMPAIGN_CONTEXT.set(campaign_context or None)  # type: ignore[attr-defined]
        try:
            return original_continuous_loop(root, project, tenant_id, interval_s)
        finally:
            _server_patch._SCAN_CAMPAIGN_CONTEXT.reset(token)  # type: ignore[attr-defined]

    scanner_module.scan = _scan_with_campaign_context
    _service.PrivatePilotHandler._handle_v12_scan = _handle_v12_scan_with_campaign_context
    _service.PrivatePilotHandler._handle_continuous_start = _handle_continuous_start_with_campaign_context
    _service._continuous_scan_loop = _continuous_scan_loop_with_campaign_context
    _service._ORIGINAL_V12_SCAN = original_scan  # type: ignore[attr-defined]
    _service._ORIGINAL_HANDLE_V12_SCAN = original_handler  # type: ignore[attr-defined]
    _service._ORIGINAL_HANDLE_CONTINUOUS_START = original_continuous_start  # type: ignore[attr-defined]
    _service._ORIGINAL_CONTINUOUS_SCAN_LOOP = original_continuous_loop  # type: ignore[attr-defined]
    _service._SCAN_CAMPAIGN_CONTEXT_PATCHED = True  # type: ignore[attr-defined]
    _service._SCAN_CAMPAIGN_CONTEXT_PATCH_SOURCE = patch_source  # type: ignore[attr-defined]
