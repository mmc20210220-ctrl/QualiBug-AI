from __future__ import annotations

"""Customer-facing private-pilot acceptance smoke runner.

The acceptance smoke wraps the doctor diagnostics into a handoff-oriented report.
It can also perform an optional live HTTP `/api/health` check when a server URL is
provided. The default mode is safe for customer sites: no scans are executed and
no customer documents are read.
"""

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from ai_test_asset_center.private_pilot_doctor import (
    DEFAULT_DOCTOR_REPORT_RELATIVE_PATH,
    diagnose_private_pilot,
    write_doctor_report,
)
from ai_test_asset_center.version import CANONICAL_HEALTH_PATH, PRODUCT_VERSION

DEFAULT_ACCEPTANCE_REPORT_RELATIVE_PATH = Path("platform_outputs") / "private_pilot_acceptance_smoke_report.json"


def _resolve_root(root: str | Path | None = None) -> Path:
    if root:
        return Path(root).expanduser().resolve()
    return Path.cwd().resolve()


def default_acceptance_report_path(root: str | Path | None = None) -> Path:
    return _resolve_root(root) / DEFAULT_ACCEPTANCE_REPORT_RELATIVE_PATH


def resolve_acceptance_report_path(output: str | Path | None, root: str | Path | None = None) -> Path:
    if output is None or str(output).strip() in {"", "default"}:
        return default_acceptance_report_path(root)
    path = Path(output).expanduser()
    if path.is_absolute():
        return path
    return _resolve_root(root) / path


def write_acceptance_report(payload: dict[str, Any], output: str | Path | None = None, root: str | Path | None = None) -> Path:
    report_path = resolve_acceptance_report_path(output, root)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    payload["acceptance_report_file"] = str(report_path)
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report_path


def _health_url(server_url: str) -> str:
    return server_url.rstrip("/") + CANONICAL_HEALTH_PATH


def _check_http_health(server_url: str, *, timeout_s: float = 3.0) -> dict[str, Any]:
    if not server_url:
        return {
            "enabled": False,
            "ok": None,
            "status": "skipped",
            "url": "",
            "reason": "server_url_not_provided",
        }
    url = _health_url(server_url)
    started = time.time()
    try:
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            raw = response.read(20000)
            duration_ms = int((time.time() - started) * 1000)
            body = json.loads(raw.decode("utf-8") or "{}") if raw else {}
            return {
                "enabled": True,
                "ok": 200 <= int(response.status) < 300,
                "status": "ok" if 200 <= int(response.status) < 300 else "bad_status",
                "url": url,
                "http_status": int(response.status),
                "duration_ms": duration_ms,
                "version": str(body.get("version") or body.get("product_version") or ""),
                "canonical_health_path": str(body.get("canonical_health_path") or ""),
            }
    except urllib.error.HTTPError as exc:
        return {
            "enabled": True,
            "ok": False,
            "status": "http_error",
            "url": url,
            "http_status": int(exc.code),
            "error": str(exc)[:500],
        }
    except Exception as exc:
        return {
            "enabled": True,
            "ok": False,
            "status": "error",
            "url": url,
            "error": str(exc)[:500],
        }


def _check_runtime_patches(doctor_payload: dict[str, Any]) -> dict[str, Any]:
    runtime = doctor_payload.get("runtime_patches", {}) if isinstance(doctor_payload.get("runtime_patches"), dict) else {}
    required = [
        "customer_delivery_gate",
        "scan_campaign_context",
        "credential_safety",
        "browser_ui_smoke",
        "customer_report",
        "deployment_contract",
    ]
    statuses = {name: bool((runtime.get(name) or {}).get("patched")) for name in required}
    missing = [name for name, ok in statuses.items() if not ok]
    return {
        "ok": not missing,
        "required": required,
        "patched": statuses,
        "missing": missing,
    }


def _check_scan_context_contract(doctor_payload: dict[str, Any]) -> dict[str, Any]:
    contract = doctor_payload.get("scan_context_contract", {}) if isinstance(doctor_payload.get("scan_context_contract"), dict) else {}
    helpers = contract.get("helpers", {}) if isinstance(contract.get("helpers"), dict) else {}
    missing = [name for name, ok in helpers.items() if not ok]
    return {
        "ok": bool(contract.get("ok")) and not missing,
        "context_var": contract.get("context_var", ""),
        "missing_helpers": missing,
    }


def _check_credential_safety(doctor_payload: dict[str, Any]) -> dict[str, Any]:
    credential = doctor_payload.get("credential_security", {}) if isinstance(doctor_payload.get("credential_security"), dict) else {}
    ok = credential.get("returns_plaintext") is False and credential.get("frontend_secret_policy") == "masked_refs_only"
    return {
        "ok": ok,
        "returns_plaintext": credential.get("returns_plaintext"),
        "frontend_secret_policy": credential.get("frontend_secret_policy"),
        "key_source": credential.get("key_source"),
    }


def _classify_acceptance(checks: dict[str, Any], doctor_payload: dict[str, Any]) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []

    doctor_readiness = doctor_payload.get("readiness", {}) if isinstance(doctor_payload.get("readiness"), dict) else {}
    if doctor_readiness.get("level") == "blocked":
        blockers.extend(str(item) for item in doctor_readiness.get("blockers", []) or [])
    elif doctor_readiness.get("level") == "warning":
        warnings.extend(str(item) for item in doctor_readiness.get("warnings", []) or [])

    if not checks["runtime_patches"].get("ok"):
        blockers.append("runtime_patches_missing:" + ",".join(checks["runtime_patches"].get("missing", [])))
    if not checks["scan_context_contract"].get("ok"):
        blockers.append("scan_context_contract_incomplete")
    if not checks["credential_safety"].get("ok"):
        blockers.append("credential_safety_not_enforced")
    health = checks.get("http_health", {})
    if health.get("enabled") and not health.get("ok"):
        blockers.append("http_health_check_failed")

    if blockers:
        return {
            "level": "blocked",
            "accepted": False,
            "label": "Blocked - private pilot acceptance failed",
            "blockers": blockers,
            "warnings": warnings,
            "next_action": "Fix blockers, rerun qualibug-acceptance-smoke --output, then proceed to customer scenario validation.",
        }
    if warnings:
        return {
            "level": "warning",
            "accepted": True,
            "label": "Warning - acceptance passed with action items",
            "blockers": [],
            "warnings": warnings,
            "next_action": "Review warnings and remediation hints before claiming a clean handoff.",
        }
    return {
        "level": "accepted",
        "accepted": True,
        "label": "Accepted - private pilot smoke checks passed",
        "blockers": [],
        "warnings": [],
        "next_action": "Proceed with customer scenario smoke validation.",
    }


def run_acceptance_smoke(
    root: str | Path | None = None,
    *,
    server_url: str = "",
    install_patches: bool = True,
    write_doctor: bool = True,
    doctor_output: str | Path | None = None,
) -> dict[str, Any]:
    resolved_root = _resolve_root(root)
    doctor_payload = diagnose_private_pilot(root=resolved_root, install_patches=install_patches)
    if write_doctor:
        write_doctor_report(doctor_payload, output=doctor_output, root=resolved_root)

    checks = {
        "doctor_readiness": doctor_payload.get("readiness", {}),
        "runtime_patches": _check_runtime_patches(doctor_payload),
        "scan_context_contract": _check_scan_context_contract(doctor_payload),
        "credential_safety": _check_credential_safety(doctor_payload),
        "browser_ui_smoke": doctor_payload.get("browser_ui_smoke", {}),
        "http_health": _check_http_health(server_url),
    }
    acceptance = _classify_acceptance(checks, doctor_payload)
    return {
        "schema_version": "private-pilot-acceptance-smoke-v1",
        "ok": bool(acceptance.get("accepted")) and acceptance.get("level") != "blocked",
        "product_version": PRODUCT_VERSION,
        "root": str(resolved_root),
        "install_patches": install_patches,
        "doctor_report_default": str(resolved_root / DEFAULT_DOCTOR_REPORT_RELATIVE_PATH),
        "acceptance": acceptance,
        "checks": checks,
        "doctor_summary_text": doctor_payload.get("summary_text", ""),
        "support_bundle_manifest": doctor_payload.get("support_bundle_manifest", {}),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run QualiBug private-pilot customer acceptance smoke checks.")
    parser.add_argument("--root", default=None, help="Private pilot root/workspace directory.")
    parser.add_argument("--server-url", default="", help="Optional running server base URL, for example http://localhost:8088.")
    parser.add_argument("--skip-install-patches", action="store_true", help="Do not install runtime patches before acceptance checks.")
    parser.add_argument("--skip-doctor-report", action="store_true", help="Do not write the companion doctor report.")
    parser.add_argument("--doctor-output", default=None, help="Optional path for the companion doctor report.")
    parser.add_argument("--compact", action="store_true", help="Print compact JSON instead of pretty JSON.")
    parser.add_argument(
        "--output",
        nargs="?",
        const="default",
        default=None,
        help="Write the acceptance JSON report to a file. With no value, writes platform_outputs/private_pilot_acceptance_smoke_report.json under --root.",
    )
    args = parser.parse_args(argv)

    payload = run_acceptance_smoke(
        root=args.root,
        server_url=args.server_url,
        install_patches=not args.skip_install_patches,
        write_doctor=not args.skip_doctor_report,
        doctor_output=args.doctor_output,
    )
    if args.output is not None:
        write_acceptance_report(payload, output=args.output, root=args.root)
    print(json.dumps(payload, ensure_ascii=False, indent=None if args.compact else 2, sort_keys=True))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
