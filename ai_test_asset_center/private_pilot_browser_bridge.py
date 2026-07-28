from __future__ import annotations

"""Browser UI bridge for private-pilot scans.

Ordinary browser smoke remains non-blocking coverage evidence. Explicit,
source-bound UI contracts additionally enter the typed observer -> assertion ->
Contract Oracle -> reproduction -> Delivery Gate chain.
"""

import os
from pathlib import Path
from typing import Any

from ai_test_asset_center import private_pilot_service as _service
from ai_test_asset_center.private_pilot_scan_context_contract import current_scan_campaign_context
from ai_test_asset_center.scan_post_hooks import register_scan_post_hook


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def scan_project_from_args(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    value = kwargs.get("project")
    if not value and args:
        value = args[0]
    return str(value or os.environ.get("QUALIBUG_PROJECT") or "real_project_demo")


def scan_root_from_args(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Path:
    value = kwargs.get("root")
    if value is None and len(args) >= 2:
        value = args[1]
    try:
        return Path(value).resolve() if value is not None else _service._root()
    except Exception:
        return _service._root()


def scan_base_url_from_context(kwargs: dict[str, Any] | None = None) -> str:
    context = kwargs if isinstance(kwargs, dict) else {}
    for key in ("ui_base_url", "base_url"):
        value = str(context.get(key) or "").strip()
        if value:
            return value.rstrip("/")
    pending = current_scan_campaign_context()
    if isinstance(pending, dict):
        for key in ("ui_base_url", "base_url", "target_url"):
            value = str(pending.get(key) or "").strip()
            if value:
                return value.rstrip("/")
    for env_name in ("QUALIBUG_BROWSER_UI_BASE_URL", "QUALIBUG_TARGET_UI_BASE_URL", "QUALIBUG_TARGET_BASE_URL"):
        value = str(os.environ.get(env_name) or "").strip()
        if value:
            return value.rstrip("/")
    return ""


def browser_ui_error_report(exc: Exception) -> dict[str, Any]:
    return {
        "schema_version": "browser-ui-smoke-v1",
        "enabled": False,
        "status": "error",
        "reason_code": "E_BROWSER_UI_RUNTIME_ERROR",
        "message": str(exc)[:500],
        "page_count": 0,
        "reachable_page_count": 0,
        "console_error_count": 0,
        "network_error_count": 0,
        "screenshot_count": 0,
        "pages": [],
        "evidence_files": [],
    }


def _runtime_contract(result: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    from ai_test_asset_center.ui_formal_runtime import runtime_contract_from_result

    runtime = runtime_contract_from_result(result)
    context_runtime = _dict(context.get("_runtime_contract") or context.get("runtime_contract"))
    if not runtime:
        runtime = dict(context_runtime)
    declared = {
        _text(value)
        for value in (
            _list(runtime.get("declared_adapters"))
            + _list(context_runtime.get("declared_adapters"))
            + _list(context.get("declared_adapters"))
        )
        if _text(value)
    }
    if declared:
        runtime["declared_adapters"] = sorted(declared)
    return runtime


def _formal_contracts(context: dict[str, Any]) -> list[dict[str, Any]]:
    explicit = [
        dict(row)
        for row in _list(context.get("ui_formal_contracts"))
        if isinstance(row, dict)
    ]
    if explicit:
        return explicit
    # Existing scan contracts already carry UI execution requests. A request becomes a
    # formal contract only when it explicitly supplies both source refs and a criterion;
    # auto-generated screenshot requests therefore remain ordinary smoke evidence.
    return [
        dict(row)
        for row in _list(context.get("ui_execution_requests"))
        if isinstance(row, dict)
        and _list(_dict(row).get("source_refs"))
        and _dict(_dict(row).get("success_criteria"))
    ]


def _declared_ui_adapter(
    runtime: dict[str, Any],
    contracts: list[dict[str, Any]],
) -> bool:
    declared = {_text(value) for value in _list(runtime.get("declared_adapters"))}
    # A formal contract may carry its own explicit adapter declaration when the scan
    # body's generic context builder predates ``declared_adapters`` passthrough. This is
    # still a customer declaration, never inference from provider/URL/Playwright.
    declared.update(
        _text(row.get("adapter") or row.get("formal_adapter"))
        for row in contracts
        if _text(row.get("adapter") or row.get("formal_adapter"))
    )
    if "ui_browser" in declared:
        runtime["declared_adapters"] = sorted(declared)
        return True
    return False


def _attach_report_and_gap(
    result: dict[str, Any],
    report: dict[str, Any],
) -> dict[str, Any]:
    updated = dict(result or {})
    updated["browser_ui_health"] = dict(report)
    gaps = [
        dict(row)
        for row in _list(updated.get("coverage_gaps"))
        if isinstance(row, dict)
    ]
    if report.get("enabled") is not True:
        gap = {
            "family": "ui_visual",
            "reason_code": _text(report.get("reason_code")) or "E_BROWSER_UI_DISABLED",
            "message": _text(report.get("message")) or "Browser UI smoke probe is disabled.",
        }
        if gap not in gaps:
            gaps.append(gap)
    elif _text(report.get("status")) != "passed":
        gap = {
            "family": "ui_visual",
            "reason_code": "E_BROWSER_UI_NEEDS_REVIEW",
            "message": "Browser UI smoke probe found console/network/page reachability signals that need review.",
        }
        if gap not in gaps:
            gaps.append(gap)
    updated["coverage_gaps"] = gaps
    return updated


def _browser_ui_smoke_hook(result: dict[str, Any], *, project: str, root: Path) -> dict[str, Any]:
    from ai_test_asset_center.browser_ui_smoke import run_browser_ui_smoke
    from ai_test_asset_center.ui_formal_runtime import formalize_browser_ui_contracts_strict
    from ai_test_asset_center.ui_formal_surface import formal_ui_paths

    context = _dict(current_scan_campaign_context())
    contracts = _formal_contracts(context)
    runtime = _runtime_contract(_dict(result), context)
    adapter_declared = _declared_ui_adapter(runtime, contracts)
    base_url = scan_base_url_from_context({}) or _text(runtime.get("approved_base_url"))
    paths = formal_ui_paths(contracts)
    # Explicit contracts + declared adapter are an explicit request to run the browser.
    # Otherwise preserve the existing environment-variable controlled smoke behavior.
    enabled = True if contracts and adapter_declared else None
    try:
        report = run_browser_ui_smoke(
            project=project,
            root=root,
            base_url=base_url,
            paths=paths or None,
            enabled=enabled,
        )
        updated = _attach_report_and_gap(_dict(result), report)
        if not contracts:
            return updated
        return formalize_browser_ui_contracts_strict(
            updated,
            browser_ui_report=report,
            contracts=contracts,
            runtime_contract=runtime,
        )
    except Exception as exc:
        updated = _attach_report_and_gap(_dict(result), browser_ui_error_report(exc))
        if contracts:
            updated["formal_ui_contracts"] = {
                "schema_version": "qualibug.formal-ui-contracts.v1",
                "requested": len(contracts),
                "evaluated": len(contracts),
                "deliverable_count": 0,
                "blocked_count": len(contracts),
                "rejected_count": 0,
                "outcomes": [
                    {
                        "contract_id": _text(row.get("contract_id") or row.get("request_id") or row.get("id")),
                        "status": "BLOCKED",
                        "reason_codes": ["UI_FORMAL_CHAIN_RUNTIME_ERROR"],
                        "finding": None,
                    }
                    for row in contracts
                ],
                "provider_findings_promoted": 0,
            }
        return updated


def install_browser_ui_smoke_patch(*, patch_source: str) -> None:
    """Register browser evidence and formal UI contracts as one named post-hook."""
    if getattr(_service, "_BROWSER_UI_SMOKE_PATCHED", False):
        return
    register_scan_post_hook("browser_ui_smoke", _browser_ui_smoke_hook)
    _service._ORIGINAL_BROWSER_UI_SMOKE_SCAN = None  # type: ignore[attr-defined]
    _service._BROWSER_UI_SMOKE_PATCHED = True  # type: ignore[attr-defined]
    _service._BROWSER_UI_SMOKE_PATCH_SOURCE = patch_source  # type: ignore[attr-defined]
    _service._BROWSER_UI_SMOKE_MODE = "formal_contract_hook"  # type: ignore[attr-defined]


def restore_browser_ui_smoke_patch() -> None:
    register_scan_post_hook("browser_ui_smoke", None)
    _service._ORIGINAL_BROWSER_UI_SMOKE_SCAN = None  # type: ignore[attr-defined]
    _service._BROWSER_UI_SMOKE_PATCHED = False  # type: ignore[attr-defined]
    _service._BROWSER_UI_SMOKE_PATCH_SOURCE = ""  # type: ignore[attr-defined]
    if hasattr(_service, "_BROWSER_UI_SMOKE_MODE"):
        delattr(_service, "_BROWSER_UI_SMOKE_MODE")
