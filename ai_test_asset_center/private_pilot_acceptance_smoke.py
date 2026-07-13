from __future__ import annotations

"""Customer-facing private-pilot acceptance smoke runner.

The acceptance smoke wraps the doctor diagnostics into a handoff-oriented report.
It can also perform an optional live HTTP `/api/health` check when a server URL is
provided. The default mode is safe for customer sites: no scans are executed and
no customer documents are read.
"""

import argparse
import json
import os
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
DEFAULT_PROJECT = "real_project_demo"


def _resolve_root(root: str | Path | None = None) -> Path:
    if root:
        return Path(root).expanduser().resolve()
    return Path.cwd().resolve()


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def _first_text(*values: Any) -> str:
    for value in values:
        text = _as_text(value)
        if text:
            return text
    return ""


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


def _source_registry_status(project: str, root: Path) -> dict[str, Any]:
    if not _as_text(project):
        return {"ok": False, "asset_count": 0, "assets": [], "reason": "project_not_configured"}
    try:
        from ai_test_asset_center.enterprise_source_registry import list_source_assets

        assets = list_source_assets(project, root=root)
    except Exception as exc:
        return {"ok": False, "asset_count": 0, "assets": [], "reason": str(exc)[:300]}
    summarized = [
        {
            "source_id": _as_text(item.get("source_id")),
            "source_type": _as_text(item.get("source_type")),
            "latest_version_id": _as_text(item.get("latest_version_id")),
            "updated_at_utc": _as_text(item.get("updated_at_utc")),
        }
        for item in assets[:20]
        if isinstance(item, dict)
    ]
    return {"ok": bool(summarized), "asset_count": len(assets), "assets": summarized, "reason": "" if summarized else "source_registry_empty"}


def _scenario_readiness_preflight(
    root: Path,
    *,
    project: str = "",
    scan_base_url: str = "",
    scope_id: str = "",
    environment_ref: str = "",
    test_data_strategy: str = "",
    require_ready: bool = False,
) -> dict[str, Any]:
    resolved_project = _first_text(project, os.environ.get("QUALIBUG_PROJECT"), DEFAULT_PROJECT)
    resolved_base_url = _first_text(
        scan_base_url,
        os.environ.get("QUALIBUG_TARGET_BASE_URL"),
        os.environ.get("QUALIBUG_TARGET_UI_BASE_URL"),
        os.environ.get("QUALIBUG_BROWSER_UI_BASE_URL"),
    )
    resolved_scope_id = _first_text(scope_id, os.environ.get("QUALIBUG_SCOPE_ID"))
    resolved_environment_ref = _first_text(environment_ref, os.environ.get("QUALIBUG_ENVIRONMENT_REF"), os.environ.get("QUALIBUG_TARGET_ENVIRONMENT"))
    resolved_test_data_strategy = _first_text(test_data_strategy, os.environ.get("QUALIBUG_TEST_DATA_STRATEGY"))
    source_registry = _source_registry_status(resolved_project, root)

    missing: list[str] = []
    if not resolved_project:
        missing.append("project")
    if not source_registry.get("ok"):
        missing.append("source_registry_asset")
    if not resolved_base_url:
        missing.append("base_url")
    if not resolved_scope_id:
        missing.append("scope_id")
    if not resolved_environment_ref:
        missing.append("environment_ref")
    if not resolved_test_data_strategy:
        missing.append("test_data_strategy")

    ready = not missing
    return {
        "ok": ready,
        "ready": ready,
        "required": require_ready,
        "status": "ready" if ready else ("blocked" if require_ready else "warning"),
        "missing": missing,
        "project": resolved_project,
        "base_url_configured": bool(resolved_base_url),
        "scope_id_configured": bool(resolved_scope_id),
        "environment_ref_configured": bool(resolved_environment_ref),
        "test_data_strategy_configured": bool(resolved_test_data_strategy),
        "source_registry": source_registry,
        "next_action": "Register an enterprise source asset and provide base_url, scope_id, environment_ref, and test_data_strategy before real scans." if missing else "Proceed to customer scenario scan setup.",
    }


def _check_runtime_patches(doctor_payload: dict[str, Any]) -> dict[str, Any]:
    runtime = doctor_payload.get("runtime_patches", {}) if isinstance(doctor_payload.get("runtime_patches"), dict) else {}
    required = [
        "callable_chain",
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
    scenario = checks.get("scenario_readiness", {})
    if isinstance(scenario, dict) and not scenario.get("ready"):
        code = "scenario_readiness_missing:" + ",".join(scenario.get("missing", []) or [])
        if scenario.get("required"):
            blockers.append(code)
        else:
            warnings.append(code)

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
            "next_action": "Review warnings and scenario_readiness before claiming a clean handoff.",
        }
    return {
        "level": "accepted",
        "accepted": True,
        "label": "Accepted - private pilot smoke checks passed",
        "blockers": [],
        "warnings": [],
        "next_action": "Proceed with customer scenario smoke validation.",
    }


def _customer_summary_commands(acceptance: dict[str, Any], checks: dict[str, Any]) -> list[str]:
    commands: list[str] = []
    if acceptance.get("level") == "blocked":
        commands.append("qualibug-acceptance-smoke --output")
    scenario = checks.get("scenario_readiness", {}) if isinstance(checks.get("scenario_readiness"), dict) else {}
    if scenario.get("missing"):
        commands.append(
            "qualibug-acceptance-smoke --project <project> --scan-base-url <url> --scope-id <scope> --environment-ref <env> --test-data-strategy <strategy> --require-scenario-ready --output"
        )
    health = checks.get("http_health", {}) if isinstance(checks.get("http_health"), dict) else {}
    if health.get("enabled") and not health.get("ok"):
        commands.append("python -m ai_test_asset_center.private_pilot_entrypoint")
        commands.append("qualibug-acceptance-smoke --server-url http://localhost:8088 --output")
    if not commands:
        commands.append("Proceed to customer scenario smoke validation.")
    deduped: list[str] = []
    for command in commands:
        if command not in deduped:
            deduped.append(command)
    return deduped[:4]


def _customer_acceptance_summary(payload: dict[str, Any]) -> dict[str, Any]:
    acceptance = payload.get("acceptance", {}) if isinstance(payload.get("acceptance"), dict) else {}
    checks = payload.get("checks", {}) if isinstance(payload.get("checks"), dict) else {}
    scenario = checks.get("scenario_readiness", {}) if isinstance(checks.get("scenario_readiness"), dict) else {}
    report_paths = [
        str(default_acceptance_report_path(payload.get("root"))),
        str(Path(str(payload.get("doctor_report_default") or "")).expanduser()),
    ]
    commands = _customer_summary_commands(acceptance, checks)
    level = _as_text(acceptance.get("level") or "unknown")
    accepted = bool(acceptance.get("accepted"))
    blockers = [str(item) for item in acceptance.get("blockers", []) or []]
    warnings = [str(item) for item in acceptance.get("warnings", []) or []]
    missing = [str(item) for item in scenario.get("missing", []) or []]

    zh_lines = [
        f"客户验收结果：{level}（{'通过' if accepted else '未通过'}）。",
        f"产品版本：{PRODUCT_VERSION}。",
        "可安全发回支持的报告：" + "；".join(report_paths),
    ]
    en_lines = [
        f"Customer acceptance result: {level} ({'accepted' if accepted else 'not accepted'}).",
        f"Product version: {PRODUCT_VERSION}.",
        "Safe reports to share with support: " + "; ".join(report_paths),
    ]
    if blockers:
        zh_lines.append("阻断项：" + "，".join(blockers[:6]))
        en_lines.append("Blockers: " + ", ".join(blockers[:6]))
    if warnings:
        zh_lines.append("待处理事项：" + "，".join(warnings[:6]))
        en_lines.append("Action items: " + ", ".join(warnings[:6]))
    if missing:
        zh_lines.append("真实扫描前置缺失：" + "，".join(missing))
        en_lines.append("Missing real-scan preflight items: " + ", ".join(missing))
    zh_lines.append("下一步：" + _as_text(acceptance.get("next_action")))
    en_lines.append("Next action: " + _as_text(acceptance.get("next_action")))
    zh_lines.append("建议命令：" + " && ".join(commands))
    en_lines.append("Suggested commands: " + " && ".join(commands))

    return {
        "schema_version": "customer-acceptance-summary-v1",
        "accepted": accepted,
        "level": level,
        "safe_report_paths": report_paths,
        "next_commands": commands,
        "zh_lines": zh_lines,
        "zh_text": "\n".join(zh_lines),
        "en_lines": en_lines,
        "en_text": "\n".join(en_lines),
    }


def run_acceptance_smoke(
    root: str | Path | None = None,
    *,
    server_url: str = "",
    install_patches: bool = True,
    write_doctor: bool = True,
    doctor_output: str | Path | None = None,
    project: str = "",
    scan_base_url: str = "",
    scope_id: str = "",
    environment_ref: str = "",
    test_data_strategy: str = "",
    require_scenario_ready: bool = False,
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
        "scenario_readiness": _scenario_readiness_preflight(
            resolved_root,
            project=project,
            scan_base_url=scan_base_url,
            scope_id=scope_id,
            environment_ref=environment_ref,
            test_data_strategy=test_data_strategy,
            require_ready=require_scenario_ready,
        ),
    }
    acceptance = _classify_acceptance(checks, doctor_payload)
    payload = {
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
    payload["customer_acceptance_summary"] = _customer_acceptance_summary(payload)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run QualiBug private-pilot customer acceptance smoke checks.")
    parser.add_argument("--root", default=None, help="Private pilot root/workspace directory.")
    parser.add_argument("--server-url", default="", help="Optional running server base URL, for example http://localhost:8088.")
    parser.add_argument("--project", default="", help="Project id used for source-registry preflight.")
    parser.add_argument("--scan-base-url", default="", help="Target API/UI base URL used for scenario-readiness preflight.")
    parser.add_argument("--scope-id", default="", help="Customer-approved scan scope id.")
    parser.add_argument("--environment-ref", default="", help="Customer environment reference, such as staging or qa.")
    parser.add_argument("--test-data-strategy", default="", help="Customer-approved test data strategy.")
    parser.add_argument("--require-scenario-ready", action="store_true", help="Block acceptance when scenario-readiness preflight is incomplete.")
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
        project=args.project,
        scan_base_url=args.scan_base_url,
        scope_id=args.scope_id,
        environment_ref=args.environment_ref,
        test_data_strategy=args.test_data_strategy,
        require_scenario_ready=args.require_scenario_ready,
    )
    if args.output is not None:
        write_acceptance_report(payload, output=args.output, root=args.root)
    print(json.dumps(payload, ensure_ascii=False, indent=None if args.compact else 2, sort_keys=True))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
