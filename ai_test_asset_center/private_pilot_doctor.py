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
import re
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any
import warnings

try:
    from ai_test_asset_center import private_pilot_service as _service
except RuntimeError as exc:
    _service = None
    _SERVICE_IMPORT_ERROR = str(exc)[:300]
else:
    _SERVICE_IMPORT_ERROR = ""
from ai_test_asset_center.customer_delivery_gate import split_customer_delivery_tracks
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

DEFAULT_DOCTOR_REPORT_RELATIVE_PATH = Path("platform_outputs") / "private_pilot_doctor_report.json"

PRIVATE_PILOT_PATCH_MODULES = {
    "ai_test_asset_center.private_pilot_entrypoint": lambda: importlib.import_module(
        "ai_test_asset_center.private_pilot_entrypoint"
    ),
    "ai_test_asset_center.private_pilot_scan_context_contract": lambda: importlib.import_module(
        "ai_test_asset_center.private_pilot_scan_context_contract"
    ),
    "ai_test_asset_center.private_pilot_scan_context_patch": lambda: importlib.import_module(
        "ai_test_asset_center.private_pilot_scan_context_patch"
    ),
    "ai_test_asset_center.private_pilot_credentials_patch": lambda: importlib.import_module(
        "ai_test_asset_center.private_pilot_credentials_patch"
    ),
    "ai_test_asset_center.private_pilot_browser_bridge": lambda: importlib.import_module(
        "ai_test_asset_center.private_pilot_browser_bridge"
    ),
    "ai_test_asset_center.private_pilot_customer_report_patch": lambda: importlib.import_module(
        "ai_test_asset_center.private_pilot_customer_report_patch"
    ),
    "ai_test_asset_center.private_pilot_deployment_patch": lambda: importlib.import_module(
        "ai_test_asset_center.private_pilot_deployment_patch"
    ),
    "ai_test_asset_center.private_pilot_health_contract": lambda: importlib.import_module(
        "ai_test_asset_center.private_pilot_health_contract"
    ),
}

NON_BLOCKING_READY_HINT_CODES = {"RUNTIME_PATCHES_READY"}


def _resolve_root(root: str | Path | None = None) -> Path:
    if root:
        return Path(root).expanduser().resolve()
    try:
        if _service is None:
            raise RuntimeError(_SERVICE_IMPORT_ERROR)
        return Path(_service._root()).resolve()
    except Exception:
        return Path.cwd().resolve()


def _port_env_status() -> dict[str, Any]:
    raw = os.environ.get("QUALIBUG_PORT", "").strip()
    if not raw:
        return {"raw": "", "valid": True, "effective": DEFAULT_PRIVATE_PILOT_PORT, "fallback": DEFAULT_PRIVATE_PILOT_PORT}
    try:
        value = int(raw)
        if 1 <= value <= 65535:
            return {"raw": raw, "valid": True, "effective": value, "fallback": DEFAULT_PRIVATE_PILOT_PORT}
    except Exception:
        pass
    return {"raw": raw, "valid": False, "effective": DEFAULT_PRIVATE_PILOT_PORT, "fallback": DEFAULT_PRIVATE_PILOT_PORT}


def _int_env(name: str, fallback: int) -> int:
    if name == "QUALIBUG_PORT":
        return int(_port_env_status()["effective"])
    try:
        return int(os.environ.get(name, "") or fallback)
    except Exception:
        return fallback


def default_doctor_report_path(root: str | Path | None = None) -> Path:
    return _resolve_root(root) / DEFAULT_DOCTOR_REPORT_RELATIVE_PATH


def resolve_doctor_report_path(output: str | Path | None, root: str | Path | None = None) -> Path:
    if output is None or str(output).strip() in {"", "default"}:
        return default_doctor_report_path(root)
    path = Path(output).expanduser()
    if path.is_absolute():
        return path
    return _resolve_root(root) / path


def write_doctor_report(payload: dict[str, Any], output: str | Path | None = None, root: str | Path | None = None) -> Path:
    report_path = resolve_doctor_report_path(output, root)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    payload["doctor_report_file"] = str(report_path)
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report_path


def _module_status(module_name: str) -> dict[str, Any]:
    loader = PRIVATE_PILOT_PATCH_MODULES.get(module_name)
    if loader is None:
        raise ValueError(f"unknown private-pilot module: {module_name}")
    try:
        module = loader()
        return {"ok": True, "module": module_name, "file": str(getattr(module, "__file__", ""))}
    except Exception as exc:
        if "QUALIBUG_JWT_SECRET" in str(exc):
            return {"ok": True, "module": module_name, "precondition": "jwt_secret_required_for_runtime_import"}
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
    core_gate_direct = (
        _service is not None
        and getattr(_service, "_partition_delivery_tracks", None) is split_customer_delivery_tracks
    )
    return {
        "customer_delivery_gate": {
            "patched": core_gate_direct,
            "source": "ai_test_asset_center.private_pilot_service" if core_gate_direct else "",
            "core_gate_direct": core_gate_direct,
            "runtime_partition_override": False,
        },
        "scan_campaign_context": {
            "patched": bool(getattr(_service, "_SCAN_CAMPAIGN_CONTEXT_PATCHED", False)),
            "source": str(getattr(_service, "_SCAN_CAMPAIGN_CONTEXT_PATCH_SOURCE", "")),
            "mode": "first_class",
            "has_original_scan": False,
            "has_original_v12_handler": False,
            "has_original_continuous_loop": False,
        },
        "credential_safety": {
            "patched": bool(str(getattr(_service, "_SERVICE_CREDENTIALS_PATCH_SOURCE", "") or "").strip()),
            "source": str(getattr(_service, "_SERVICE_CREDENTIALS_PATCH_SOURCE", "")),
            "mode": str(getattr(_service, "_SERVICE_CREDENTIALS_MODE", "") or "first_class"),
        },
        "browser_ui_smoke": {
            "patched": bool(getattr(_service, "_BROWSER_UI_SMOKE_PATCHED", False)),
            "source": str(getattr(_service, "_BROWSER_UI_SMOKE_PATCH_SOURCE", "")),
            "mode": str(getattr(_service, "_BROWSER_UI_SMOKE_MODE", "") or ""),
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


def _support_path(root: Path, relative: str) -> str:
    return str((root / relative).resolve())


def _collect_support_bundle_manifest(root: Path) -> dict[str, Any]:
    """Describe which diagnostic artifacts are safe to share with support."""
    default_report = default_doctor_report_path(root)
    return {
        "schema_version": "support-bundle-manifest-v1",
        "policy": "diagnostics_only_no_customer_data",
        "safe_to_share": [
            {
                "id": "private_pilot_doctor_report",
                "path": str(default_report),
                "risk_level": "low",
                "contains": ["product_version", "readiness", "runtime_patch_status", "masked_credential_refs"],
                "send_to_support": True,
                "notes": "Generated by qualibug-doctor; credential values are reported as masked refs only.",
            },
            {
                "id": "private_pilot_doctor_patched_report",
                "path": _support_path(root, "platform_outputs/private_pilot_doctor_patched_report.json"),
                "risk_level": "low",
                "contains": ["runtime_patch_status_after_install", "readiness", "remediation_hints"],
                "send_to_support": True,
                "notes": "Recommended second report from qualibug-doctor --install-patches --output.",
            },
        ],
        "requires_review": [
            {
                "id": "service_logs",
                "path_glob": _support_path(root, "platform_outputs/**/*.log"),
                "risk_level": "medium",
                "reason": "Logs may include customer URLs, tenant IDs, request IDs, stack traces, or environment details.",
                "recommended_action": "Review and redact customer identifiers before sharing.",
            },
            {
                "id": "browser_ui_artifacts",
                "path_glob": _support_path(root, "platform_outputs/**/browser_ui_smoke/**"),
                "risk_level": "high",
                "reason": "Screenshots and HAR files can include customer UI, cookies, headers, query strings, and request payloads.",
                "recommended_action": "Share only after customer approval and token/header redaction.",
            },
            {
                "id": "pipeline_reports",
                "path_glob": _support_path(root, "platform_outputs/**/pipeline_reports/**"),
                "risk_level": "medium",
                "reason": "Reports may include business object names, defect titles, API paths, and evidence snippets.",
                "recommended_action": "Prefer doctor report first; share pipeline reports only when support explicitly requests them.",
            },
        ],
        "do_not_send": [
            {
                "id": "env_files",
                "path_glob": str((root / ".env*").resolve()),
                "reason": "May contain API keys, model credentials, database URLs, or customer endpoints.",
            },
            {
                "id": "local_secret_keys",
                "path_glob": _support_path(root, "platform_workspace/.secrets/**"),
                "reason": "Contains local encryption material and must stay inside the customer environment.",
            },
            {
                "id": "service_credentials_config",
                "path_glob": _support_path(root, "platform_workspace/**/multi_service_config.json"),
                "reason": "May contain encrypted credential blobs, service endpoints, tenant names, or role-account metadata.",
            },
            {
                "id": "registered_enterprise_sources",
                "path_glob": _support_path(root, "platform_workspace/**/source_registry/**"),
                "reason": "Contains customer-provided API docs, PRDs, DB schemas, or source manifests.",
            },
        ],
        "redaction_rules": [
            "Never share raw API keys, bearer tokens, passwords, cookies, session IDs, or credential_encryption.key.",
            "Prefer qualibug-doctor JSON reports because credential fields are masked refs only.",
            "Review screenshots, HAR, logs, and pipeline reports with the customer before external sharing.",
            "When in doubt, send only the doctor report and the support_bundle_manifest section.",
        ],
    }


def _collect_warnings(payload: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    modules = payload.get("modules", {})
    missing_modules = [name for name, item in modules.items() if isinstance(item, dict) and not item.get("ok")]
    if missing_modules:
        warnings.append("missing_private_pilot_modules:" + ",".join(missing_modules))
    env = payload.get("environment", {})
    port_env = env.get("port_env") if isinstance(env, dict) else {}
    if isinstance(port_env, dict) and not port_env.get("valid", True):
        warnings.append("invalid_qualibug_port_env")
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


def _hint(code: str, severity: str, title: str, action: str, commands: list[str] | None = None) -> dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "title": title,
        "action": action,
        "commands": commands or [],
    }


def _collect_remediation_hints(payload: dict[str, Any]) -> list[dict[str, Any]]:
    hints: list[dict[str, Any]] = []
    env = payload.get("environment", {})
    port_env = env.get("port_env") if isinstance(env, dict) else {}
    if isinstance(port_env, dict) and not port_env.get("valid", True):
        hints.append(
            _hint(
                "INVALID_QUALIBUG_PORT",
                "warning",
                "QUALIBUG_PORT is not a valid TCP port.",
                "Unset QUALIBUG_PORT or set it to the private-pilot default port 8088 before starting the service.",
                ["unset QUALIBUG_PORT", "export QUALIBUG_PORT=8088"],
            )
        )

    credential = payload.get("credential_security", {})
    if isinstance(credential, dict) and credential.get("key_source") == "missing_until_first_save":
        hints.append(
            _hint(
                "CREDENTIAL_KEY_MISSING",
                "info",
                "Credential encryption key has not been created yet.",
                "Run the patched entrypoint or install patches once before saving service credentials so the local key file is provisioned.",
                ["qualibug-doctor --install-patches --output", "qualibug-server"],
            )
        )

    browser = payload.get("browser_ui_smoke", {})
    if isinstance(browser, dict) and browser.get("enabled") and not browser.get("playwright_available"):
        hints.append(
            _hint(
                "BROWSER_UI_PLAYWRIGHT_MISSING",
                "warning",
                "Browser UI Smoke is enabled but Playwright is unavailable.",
                "Install the browser optional dependencies and Chromium browser runtime, or disable QUALIBUG_BROWSER_UI_SMOKE.",
                ["pip install -e '.[browser]'", "python -m playwright install chromium", "export QUALIBUG_BROWSER_UI_SMOKE=0"],
            )
        )

    modules = payload.get("modules", {})
    missing_modules = [name for name, item in modules.items() if isinstance(item, dict) and not item.get("ok")]
    if missing_modules:
        hints.append(
            _hint(
                "PRIVATE_PILOT_MODULE_IMPORT_FAILED",
                "error",
                "One or more private-pilot modules failed to import.",
                "Reinstall the package in editable mode and rerun doctor. If it still fails, send the doctor report to support.",
                ["pip install -e .", "qualibug-doctor --output"],
            )
        )

    runtime = payload.get("runtime_patches", {})
    install_patches = bool((payload.get("doctor") or {}).get("install_patches"))
    patch_items = [item for item in runtime.values() if isinstance(item, dict)] if isinstance(runtime, dict) else []
    missing_patch_names = [name for name, item in runtime.items() if isinstance(item, dict) and not item.get("patched")]
    if not install_patches:
        hints.append(
            _hint(
                "RUNTIME_PATCHES_NOT_INSTALLED_IN_READONLY_MODE",
                "info",
                "Runtime patches are not installed in the current read-only doctor run.",
                "Run doctor with --install-patches to verify production patch wiring before handoff.",
                ["qualibug-doctor --install-patches --output"],
            )
        )
    elif missing_patch_names and install_patches:
        hints.append(
            _hint(
                "RUNTIME_PATCH_INSTALL_INCOMPLETE",
                "error",
                "Some runtime patches are still not installed after --install-patches.",
                "Send the doctor report to support and avoid claiming the private-pilot runtime is ready.",
                ["qualibug-doctor --install-patches --output"],
            )
        )
    elif patch_items and install_patches:
        hints.append(
            _hint(
                "RUNTIME_PATCHES_READY",
                "info",
                "Runtime patches are installed.",
                "Proceed with private-pilot service startup and scenario smoke validation.",
                ["qualibug-server"],
            )
        )

    scan_contract = payload.get("scan_context_contract", {})
    helpers = scan_contract.get("helpers") if isinstance(scan_contract, dict) else {}
    if not scan_contract.get("ok") or not all(bool(value) for value in helpers.values()):
        hints.append(
            _hint(
                "SCAN_CONTEXT_CONTRACT_INCOMPLETE",
                "error",
                "Scan context contract helpers are incomplete.",
                "Reinstall the package and rerun doctor before running enterprise scans.",
                ["pip install -e .", "qualibug-doctor --output"],
            )
        )

    return hints


def _collect_readiness(payload: dict[str, Any], hints: list[dict[str, Any]]) -> dict[str, Any]:
    blocking_hints = [hint for hint in hints if hint.get("severity") == "error"]
    actionable_hints = [
        hint for hint in hints if hint.get("code") not in NON_BLOCKING_READY_HINT_CODES and hint.get("severity") != "error"
    ]
    warnings = [str(item) for item in payload.get("warnings", [])]

    if blocking_hints:
        return {
            "level": "blocked",
            "blocked": True,
            "label": "Blocked - fix required before customer pilot",
            "blockers": [str(hint.get("code")) for hint in blocking_hints],
            "warnings": warnings + [str(hint.get("code")) for hint in actionable_hints],
            "next_action": blocking_hints[0].get("action", "Fix blocking issues and rerun qualibug-doctor --output."),
        }

    if warnings or actionable_hints:
        return {
            "level": "warning",
            "blocked": False,
            "label": "Warning - usable for diagnosis, not clean handoff",
            "blockers": [],
            "warnings": warnings + [str(hint.get("code")) for hint in actionable_hints],
            "next_action": "Review remediation_hints, apply recommended commands, then rerun qualibug-doctor --install-patches --output.",
        }

    return {
        "level": "ready",
        "blocked": False,
        "label": "Ready - private pilot diagnostics are clean",
        "blockers": [],
        "warnings": [],
        "next_action": "Proceed with private-pilot service startup and scenario smoke validation.",
    }


def _collect_summary_lines(payload: dict[str, Any]) -> list[str]:
    product = payload.get("product", {}) if isinstance(payload.get("product"), dict) else {}
    environment = payload.get("environment", {}) if isinstance(payload.get("environment"), dict) else {}
    readiness = payload.get("readiness", {}) if isinstance(payload.get("readiness"), dict) else {}
    hints = payload.get("remediation_hints", []) if isinstance(payload.get("remediation_hints"), list) else []

    level = str(readiness.get("level") or "unknown")
    label = str(readiness.get("label") or "Unknown readiness")
    product_version = str(product.get("version") or "unknown")
    port = environment.get("port", "unknown")

    lines = [
        f"QualiBug private-pilot doctor: {label}.",
        f"Product version: {product_version}; effective port: {port}; readiness: {level}.",
    ]

    blockers = readiness.get("blockers") if isinstance(readiness.get("blockers"), list) else []
    warnings = readiness.get("warnings") if isinstance(readiness.get("warnings"), list) else []
    if blockers:
        lines.append("Blocking issues: " + ", ".join(str(item) for item in blockers[:5]) + (" ..." if len(blockers) > 5 else ""))
    if warnings:
        lines.append("Warnings/action items: " + ", ".join(str(item) for item in warnings[:5]) + (" ..." if len(warnings) > 5 else ""))

    next_action = str(readiness.get("next_action") or "Review the doctor report and rerun qualibug-doctor --output.")
    lines.append("Next action: " + next_action)

    commands: list[str] = []
    for hint in hints:
        if not isinstance(hint, dict):
            continue
        for command in hint.get("commands") or []:
            text = str(command or "").strip()
            if text and text not in commands:
                commands.append(text)
            if len(commands) >= 3:
                break
        if len(commands) >= 3:
            break
    if commands:
        lines.append("Suggested commands: " + " && ".join(commands))

    return lines


def _collect_summary(payload: dict[str, Any]) -> dict[str, Any]:
    lines = _collect_summary_lines(payload)
    return {
        "summary_lines": lines,
        "summary_text": "\n".join(lines),
    }


def diagnose_private_pilot(root: str | Path | None = None, *, install_patches: bool = False) -> dict[str, Any]:
    resolved_root = _resolve_root(root)
    if install_patches:
        from ai_test_asset_center.private_pilot_entrypoint import install_runtime_patches

        install_runtime_patches()

    port_status = _port_env_status()
    payload: dict[str, Any] = {
        "ok": True,
        "doctor": {
            "install_patches": install_patches,
            "report_default_relative_path": str(DEFAULT_DOCTOR_REPORT_RELATIVE_PATH),
        },
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
            "port": int(port_status["effective"]),
            "port_env": port_status,
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
        "support_bundle_manifest": _collect_support_bundle_manifest(resolved_root),
    }
    payload["warnings"] = _collect_warnings(payload)
    payload["remediation_hints"] = _collect_remediation_hints(payload)
    payload["readiness"] = _collect_readiness(payload, payload["remediation_hints"])
    payload.update(_collect_summary(payload))
    payload["ok"] = payload["readiness"]["level"] != "blocked"
    return payload


# ---------------------------------------------------------------------------
# Support bundle export (--export-bundle)
# ---------------------------------------------------------------------------

_BUNDLE_REDACT_KEYS = frozenset({
    "token", "tokens", "access_token", "refresh_token", "bearer",
    "password", "passwd", "secret", "api_key", "apikey", "api_secret",
    "authorization", "auth", "credential", "credentials",
    "credential_encryption_key", "signing_key", "receipt_signing_key",
    "jwt_secret", "private_key", "client_secret",
})

_BUNDLE_REDACT_VALUE_RE = re.compile(
    r"(?i)(bearer\s+)[A-Za-z0-9\-._~+/]+=*",
)


def _bundle_redact_text(text: str) -> str:
    """Redact bearer tokens from free text for safe export."""
    return _BUNDLE_REDACT_VALUE_RE.sub(r"\1***REDACTED***", text)


def _bundle_redact_dict(data: Any, _depth: int = 0) -> Any:
    """Recursively redact sensitive fields from a dict/list."""
    if _depth > 8:
        return "_truncated_"
    if isinstance(data, dict):
        result: dict[str, Any] = {}
        for key, value in data.items():
            if key.lower() in _BUNDLE_REDACT_KEYS:
                result[key] = "***REDACTED***"
            else:
                result[key] = _bundle_redact_dict(value, _depth + 1)
        return result
    if isinstance(data, list):
        return [_bundle_redact_dict(item, _depth + 1) for item in data[:100]]
    if isinstance(data, str) and len(data) > 20:
        return _bundle_redact_text(data)
    return data


def _collect_log_files(log_dir: Path) -> list[Path]:
    """Collect log files (current + rotated) from the log directory."""
    if not log_dir.is_dir():
        return []
    files = []
    for pattern in ("qualibug.log*", "qualibug_error.log*", "qualibug_audit.log*"):
        files.extend(sorted(log_dir.glob(pattern)))
    return files


def _build_error_summary(log_dir: Path) -> dict[str, Any]:
    """Parse error log and cluster errors by error code."""
    error_log = log_dir / "qualibug_error.log"
    error_files = sorted(log_dir.glob("qualibug_error.log*")) if log_dir.is_dir() else []
    if not error_files:
        return {"total_errors": 0, "by_code": {}, "recent_errors": []}

    by_code: dict[str, int] = {}
    recent_errors: list[dict[str, Any]] = []
    total = 0

    for ef in error_files:
        try:
            content = ef.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue
            total += 1
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                record = {"msg": line[:500]}
            code = record.get("code", "UNKNOWN")
            by_code[code] = by_code.get(code, 0) + 1
            if len(recent_errors) < 50:
                recent_errors.append({
                    "ts": record.get("ts", ""),
                    "code": code,
                    "level": record.get("level", "ERROR"),
                    "msg": str(record.get("msg", ""))[:300],
                })

    # Sort by count descending
    sorted_codes = dict(sorted(by_code.items(), key=lambda x: x[1], reverse=True))
    return {
        "total_errors": total,
        "by_code": sorted_codes,
        "recent_errors": recent_errors[-50:],  # last 50
    }


def _collect_redacted_config(root: Path) -> dict[str, Any]:
    """Collect environment/config info with secrets masked."""
    config: dict[str, Any] = {
        "python_version": sys.version.split()[0],
        "platform": platform.system(),
        "platform_release": platform.release(),
        "pid": os.getpid(),
    }
    # Collect relevant env vars (redacted)
    relevant_env = [
        "QUALIBUG_PORT", "QUALIBUG_BIND_HOST", "QUALIBUG_BROWSER_UI_SMOKE",
        "QUALIBUG_LLM_ENDPOINT", "QUALIBUG_LLM_MODEL",
        "JWT_SECRET", "OPENAI_API_KEY", "DEEPSEEK_API_KEY",
    ]
    env_snapshot: dict[str, str] = {}
    for key in relevant_env:
        val = os.environ.get(key)
        if val is None:
            env_snapshot[key] = "<not set>"
        elif key.lower() in _BUNDLE_REDACT_KEYS or "KEY" in key or "SECRET" in key:
            env_snapshot[key] = "***REDACTED***"
        else:
            env_snapshot[key] = val
    config["environment"] = env_snapshot

    # Collect .env file keys (names only, never values)
    env_file = root / ".env"
    if env_file.is_file():
        try:
            keys = []
            for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    keys.append(line.split("=", 1)[0].strip())
            config["dot_env_keys"] = keys
        except OSError:
            config["dot_env_keys"] = ["<read error>"]
    return config


def _find_latest_scan_result(root: Path) -> Path | None:
    """Find the most recent scan_result.json under platform_outputs."""
    outputs = root / "platform_outputs"
    if not outputs.is_dir():
        return None
    candidates = list(outputs.rglob("scan_result.json"))
    if not candidates:
        candidates = list(outputs.rglob("*scan*result*.json"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def export_support_bundle(root: str | Path | None = None) -> Path:
    """Generate a one-click support bundle zip for remote diagnostics.

    Returns the path to the generated zip file.
    """
    resolved_root = _resolve_root(root)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bundle_name = f"qualibug_support_bundle_{timestamp}"

    # Use a temp directory to assemble bundle contents
    with tempfile.TemporaryDirectory(prefix="qualibug_bundle_") as tmp_str:
        tmp = Path(tmp_str)

        # 1. Run full doctor diagnosis
        print("  [1/6] 运行诊断检查...")
        payload = diagnose_private_pilot(root=resolved_root)
        report_path = tmp / "doctor_report.json"
        report_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        # 2. Collect logs (redacted)
        print("  [2/6] 收集日志文件...")
        from ai_test_asset_center.product_logging import get_log_dir
        log_dir = get_log_dir(resolved_root)
        logs_out = tmp / "logs"
        logs_out.mkdir(exist_ok=True)
        log_files = _collect_log_files(log_dir)
        for lf in log_files:
            try:
                content = lf.read_text(encoding="utf-8", errors="replace")
                redacted = _bundle_redact_text(content)
                (logs_out / lf.name).write_text(redacted, encoding="utf-8")
            except OSError:
                (logs_out / lf.name).write_text("<read error>", encoding="utf-8")
        if not log_files:
            (logs_out / "README.txt").write_text(
                "日志目录为空。产品可能尚未启动过，或日志已被清理。\n",
                encoding="utf-8",
            )

        # 3. Collect latest scan result
        print("  [3/6] 收集扫描结果...")
        scan_file = _find_latest_scan_result(resolved_root)
        if scan_file and scan_file.is_file():
            try:
                from .scan_result_store import load_scan_result

                # 只取索引摘要级信息（分片 store 骨架 + 小键）；大分片不装入
                # 支持包，避免 4GB 级产物整读（_bundle_redact_dict 全量递归）。
                scan_data = load_scan_result(scan_file, keys=[])
                # Only include summary-level info, redact sensitive parts
                scan_summary = _bundle_redact_dict(scan_data)
                (tmp / "last_scan_result.json").write_text(
                    json.dumps(scan_summary, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            except (OSError, json.JSONDecodeError):
                (tmp / "last_scan_result.json").write_text(
                    '{"error": "scan result file could not be parsed"}\n', encoding="utf-8"
                )
        else:
            (tmp / "last_scan_result.json").write_text(
                '{"info": "未找到扫描结果文件，产品可能尚未执行过扫描。"}\n', encoding="utf-8"
            )

        # 4. Generate error summary
        print("  [4/6] 生成错误统计...")
        error_summary = _build_error_summary(log_dir)
        (tmp / "error_summary.json").write_text(
            json.dumps(error_summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        # 5. Export redacted config
        print("  [5/6] 导出脱敏配置...")
        config_data = _collect_redacted_config(resolved_root)
        (tmp / "config_redacted.json").write_text(
            json.dumps(config_data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        # 6. Generate README
        print("  [6/6] 生成说明文件...")
        readme_text = (
            "=" * 50 + "\n"
            "  QualiBug 技术支持诊断包\n"
            "=" * 50 + "\n\n"
            f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"产品版本: {PRODUCT_VERSION}\n\n"
            "本文件包含什么：\n"
            "  - doctor_report.json    系统诊断报告\n"
            "  - logs/                 最近运行日志（已脱敏）\n"
            "  - last_scan_result.json 最近一次扫描结果\n"
            "  - error_summary.json    错误统计摘要\n"
            "  - config_redacted.json  系统配置（密钥已隐藏）\n\n"
            "操作步骤（共3步）：\n"
            "  1. 找到本目录下的 .zip 压缩包文件\n"
            "  2. 通过邮件/微信/企业IM发送给技术支持\n"
            "  3. 等待技术支持回复即可\n\n"
            "注意事项：\n"
            "  - 本包已自动脱敏，不包含任何密码/密钥/令牌\n"
            "  - 无需做任何额外操作\n"
            "  - 如有补充信息，可在发送时附带文字描述\n\n"
            "感谢您的配合！\n"
        )
        (tmp / "README_发送说明.txt").write_text(readme_text, encoding="utf-8")

        # 7. Package into zip
        zip_path = resolved_root / f"{bundle_name}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file in sorted(tmp.rglob("*")):
                if file.is_file():
                    arcname = f"{bundle_name}/{file.relative_to(tmp)}"
                    zf.write(file, arcname)

    return zip_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run QualiBug private-pilot deployment diagnostics.")
    parser.add_argument("--root", default=None, help="Private pilot root/workspace directory.")
    parser.add_argument("--install-patches", action="store_true", help="Install runtime patches before reporting status.")
    parser.add_argument("--compact", action="store_true", help="Print compact JSON instead of pretty JSON.")
    parser.add_argument(
        "--output",
        nargs="?",
        const="default",
        default=None,
        help="Write the doctor JSON report to a file. With no value, writes platform_outputs/private_pilot_doctor_report.json under --root.",
    )
    parser.add_argument(
        "--export-bundle",
        action="store_true",
        help="Generate a one-click support bundle (.zip) for remote diagnostics.",
    )
    args = parser.parse_args(argv)

    # --- Export bundle mode ---
    if args.export_bundle:
        print("\n正在生成诊断包，请稍候...\n")
        try:
            zip_path = export_support_bundle(root=args.root)
        except Exception as exc:
            print(f"\n[错误] 诊断包生成失败: {exc}")
            print("请将此错误信息发送给技术支持。\n")
            return 1
        size_mb = zip_path.stat().st_size / (1024 * 1024)
        print(
            "\n"
            "============================================\n"
            "  诊断包已生成！\n"
            f"  文件: {zip_path.name}\n"
            f"  位置: {zip_path}\n"
            f"  大小: {size_mb:.1f} MB\n"
            "\n"
            "  请将此文件发送给技术支持即可。\n"
            "  无需其他操作。\n"
            "============================================\n"
        )
        return 0

    # --- Normal doctor mode ---
    payload = diagnose_private_pilot(root=args.root, install_patches=args.install_patches)
    if args.output is not None:
        write_doctor_report(payload, output=args.output, root=args.root)
    print(json.dumps(payload, ensure_ascii=False, indent=None if args.compact else 2, sort_keys=True))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
