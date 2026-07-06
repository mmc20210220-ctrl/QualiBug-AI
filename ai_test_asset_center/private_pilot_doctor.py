from __future__ import annotations

"""Private-pilot deployment diagnostics.

The doctor is intentionally read-mostly: it reports runtime patch status,
version/port contract, credential-safety posture, browser UI smoke readiness,
and scan-context contract wiring without requiring the HTTP server to be running.
"""

import argparse
import importlib
import json
import os
import platform
import sys
from pathlib import Path
from typing import Any

from ai_test_asset_center import private_pilot_service as _service
from ai_test_asset_center.private_pilot_health_contract import build_private_pilot_health_payload
from ai_test_asset_center.version import (
    CANONICAL_HEALTH_PATH,
    DEFAULT_PRIVATE_PILOT_PORT,
    LEGACY_HEALTH_PATH,
    PRODUCT_CHANNEL,
    PRODUCT_NAME,
    PRODUCT_PHASE,
    PRODUCT_VERSION,
)

PRIVATE_PILOT_PATCH_MODULES = [
    "ai_test_asset_center.private_pilot_entrypoint",
    "ai_test_asset_center.private_pilot_scan_context_contract",
    "ai_test_asset_center.private_pilot_scan_context_patch",
    "ai_test_asset_center.private_pilot_credentials_patch",
    "ai_test_asset_center.private_pilot_browser_bridge",
    "ai_test_asset_center.private_pilot_customer_report_patch",
    "ai_test_asset_center.private_pilot_deployment_patch",
    "ai_test_asset_center.private_pilot_health_contract",
]


def _resolve_root(root: str | Path | None = None) -> Path:
    if root:
        return Path(root).expanduser().resolve()
    try:
        return Path(_service._root()).resolve()
    except Exception:
        return Path.cwd().resolve()


def _module_status(module_name: str) -> dict[str, Any]:
    try:
        module = importlib.import_module(module_name)
        return {"ok": True, "module": module_name, "file": str(getattr(module, "__file__", ""))}
    except Exception as exc:
        return {"ok": False, "module": module_name, "error": str(exc)[:300]}


def _credential_key_status(root: Path) -> dict[str, Any]:
    env_value = os.environ.get("QUALIBUG_CRED_ENC_KEY", "").strip()
    key_path = root / "platform_workspace" / ".secrets" / "credential_encryption.key"
    if env_value:
        key_source = "env"
    elif key_path.exists():
        key_source = "local_private_key_file"
    else:
        key_source = "missing_until_first_save"
    return {
        "mode": "encrypted_at_rest",
        "key_source": key_source,
        "env_configured": bool(env_value),
        "local_key_file_exists": key_path.exists(),
        "local_key_file": str(key_path),
        "returns_plaintext": False,
        "frontend_secret_policy": "masked_refs_only",
        "config_file_policy": "encrypt_before_write",
    }


def _runtime_patch_status() -> dict[str, Any]:
    return {
        "customer_delivery_gate": {
            "patched": bool(getattr(_service, "_CUSTOMER_DELIVERY_GATE_PATCHED", False)),
            "source": str(getattr(_service, "_CUSTOMER_DELIVERY_GATE_PATCH_SOURCE", "")),
        },
        "scan_campaign_context": {
            "patched": bool(getattr(_service, "_SCAN_CAMPAIGN_CONTEXT_PATCHED", False)),
            "source": str(getattr(_service, "_SCAN_CAMPAIGN_CONTEXT_PATCH_SOURCE", "")),
            "has_original_scan": bool(getattr(_service, "_ORIGINAL_V12_SCAN", None)),
            "has_original_v12_handler": bool(getattr(_service, "_ORIGINAL_HANDLE_V12_SCAN", None)),
            "has_original_continuous_loop": bool(getattr(_service, "_ORIGINAL_CONTINUOUS_SCAN_LOOP", None)),
        },
        "credential_safety": {
            "patched": bool(getattr(_service, "_ORIGINAL_HANDLE_GET_SERVICE_CREDENTIALS", None))
            and bool(getattr(_service, "_ORIGINAL_HANDLE_SAVE_SERVICE_CREDENTIALS", None)),
            "source": str(getattr(_service, "_SERVICE_CREDENTIALS_PATCH_SOURCE", "")),
        },
        "browser_ui_smoke": {
            "patched": bool(getattr(_service, "_BROWSER_UI_SMOKE_PATCHED", False)),
            "source": str(getattr(_service, "_BROWSER_UI_SMOKE_PATCH_SOURCE", "")),
        },
        "customer_report": {
            "patched": bool(getattr(_service, "_CUSTOMER_REPORT_PATCHED", False)),
            "source": str(getattr(_service, "_CUSTOMER_REPORT_PATCH_SOURCE", "")),
        },
        "deployment_contract": {
            "patched": bool(getattr(_service, "_DEPLOYMENT_CONTRACT_PATCHED", False)),
            "source": str(getattr(_service, "_DEPLOYMENT_CONTRACT_PATCH_SOURCE", "")),
        },
    }


def _browser_ui_smoke_status() -> dict[str, Any]:
    try:
        from ai_test_asset_center.browser_ui_smoke import is_browser_ui_enabled

        enabled = is_browser_ui_enabled()
    except Exception:
        enabled = False
    try:
        importlib.import_module("playwright.sync_api")
        playwright_available = True
    except Exception:
        playwright_available = False
    return {
        "enabled": enabled,
        "env_flag": "QUALIBUG_BROWSER_UI_SMOKE",
        "base_url_env": os.environ.get("QUALIBUG_BROWSER_UI_BASE_URL")
        or os.environ.get("QUALIBUG_TARGET_UI_BASE_URL")
        or os.environ.get("QUALIBUG_TARGET_BASE_URL")
        or "",
        "playwright_available": playwright_available,
        "evidence": ["page_reachability", "console_errors", "network_errors", "screenshots", "har"],
        "status": "ready" if (enabled and playwright_available) else ("disabled" if not enabled else "missing_playwright"),
    }


def _scan_context_contract_status() -> dict[str, Any]:
    try:
        from ai_test_asset_center import private_pilot_scan_context_contract as contract

        return {
            "ok": True,
            "context_var": "SCAN_CAMPAIGN_CONTEXT",
            "continuous_context_count": len(contract.CONTINUOUS_CAMPAIGN_CONTEXTS),
            "helpers": {
                "source_manifest_from_body": callable(getattr(contract, "source_manifest_from_body", None)),
                "prepare_scan_body_for_campaign": callable(getattr(contract, "prepare_scan_body_for_campaign", None)),
                "build_campaign_context_from_scan_body": callable(getattr(contract, "build_campaign_context_from_scan_body", None)),
                "current_scan_campaign_context": callable(getattr(contract, "current_scan_campaign_context", None)),
            },
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300], "helpers": {}}


def _health_payload_preview(root: Path) -> dict[str, Any]:
    class DoctorHandler:
        def _root(self) -> Path:
            return root

        def _llm_health(self) -> dict[str, Any]:
            return {"available": False, "status": "not_checked_by_doctor", "label": "not_checked"}

    try:
        return build_private_pilot_health_payload(
            DoctorHandler(),
            fallback_root=root,
            patch_source="ai_test_asset_center.private_pilot_doctor",
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300]}


def _collect_warnings(payload: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    modules = payload.get("modules", {})
    missing_modules = [name for name, item in modules.items() if isinstance(item, dict) and not item.get("ok")]
    if missing_modules:
        warnings.append("missing_private_pilot_modules:" + ",".join(missing_modules))
    credential = payload.get("credential_security", {})
    if credential.get("key_source") == "missing_until_first_save":
        warnings.append("credential_key_missing_until_first_save")
    browser = payload.get("browser_ui_smoke", {})
    if browser.get("enabled") and not browser.get("playwright_available"):
        warnings.append("browser_ui_enabled_but_playwright_missing")
    scan_contract = payload.get("scan_context_contract", {})
    helpers = scan_contract.get("helpers") if isinstance(scan_contract, dict) else {}
    if not scan_contract.get("ok") or not all(bool(value) for value in helpers.values()):
        warnings.append("scan_context_contract_incomplete")
    return warnings


def diagnose_private_pilot(root: str | Path | None = None, *, install_patches: bool = False) -> dict[str, Any]:
    resolved_root = _resolve_root(root)
    if install_patches:
        from ai_test_asset_center.private_pilot_entrypoint import install_runtime_patches

        install_runtime_patches()

    payload: dict[str, Any] = {
        "ok": True,
        "product": {
            "name": PRODUCT_NAME,
            "version": PRODUCT_VERSION,
            "phase": PRODUCT_PHASE,
            "channel": PRODUCT_CHANNEL,
        },
        "environment": {
            "root": str(resolved_root),
            "python_version": sys.version.split()[0],
            "platform": platform.system(),
            "bind_host": os.environ.get("QUALIBUG_BIND_HOST", "127.0.0.1"),
            "port": int(os.environ.get("QUALIBUG_PORT", DEFAULT_PRIVATE_PILOT_PORT) or DEFAULT_PRIVATE_PILOT_PORT),
            "default_port": DEFAULT_PRIVATE_PILOT_PORT,
            "canonical_health_path": CANONICAL_HEALTH_PATH,
            "legacy_health_path": LEGACY_HEALTH_PATH,
        },
        "modules": {name: _module_status(name) for name in PRIVATE_PILOT_PATCH_MODULES},
        "runtime_patches": _runtime_patch_status(),
        "credential_security": _credential_key_status(resolved_root),
        "browser_ui_smoke": _browser_ui_smoke_status(),
        "scan_context_contract": _scan_context_contract_status(),
        "health_payload_preview": _health_payload_preview(resolved_root),
    }
    payload["warnings"] = _collect_warnings(payload)
    payload["ok"] = not any(name for name, item in payload["modules"].items() if not item.get("ok")) and not any(
        warning.endswith("_incomplete") for warning in payload["warnings"]
    )
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run QualiBug private-pilot deployment diagnostics.")
    parser.add_argument("--root", default=None, help="Private pilot root/workspace directory.")
    parser.add_argument("--install-patches", action="store_true", help="Install runtime patches before reporting status.")
    parser.add_argument("--compact", action="store_true", help="Print compact JSON instead of pretty JSON.")
    args = parser.parse_args(argv)

    payload = diagnose_private_pilot(root=args.root, install_patches=args.install_patches)
    print(json.dumps(payload, ensure_ascii=False, indent=None if args.compact else 2, sort_keys=True))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
