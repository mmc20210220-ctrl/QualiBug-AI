from __future__ import annotations

"""Phase104E: frontend-to-backend runtime integration smoke harness.

Phase104D generated a framework-neutral frontend handoff workspace, but teams
still need a deterministic way to prove that the generated client contract and
page adapters match the live local backend behavior.  This module provides a
stdlib-only smoke runner that exercises the same workflow a frontend would use
against the Phase104A in-process HTTP application.

The smoke harness intentionally avoids opening a socket and avoids running a
Node/TypeScript toolchain.  It validates the frontend workspace artifacts, then
uses the HTTP route adapter exactly as a browser client would:

* seed project read paths for dashboard/map/risk/report pages;
* mutation workflow for create project -> template -> environment -> plan -> run
  -> report;
* method safety and customer-safe error envelopes;
* secret redaction across generated artifacts and runtime responses.
"""

import argparse
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from ai_test_asset_center.phase103_enterprise_command_center import redact_value
from ai_test_asset_center.phase104_command_center_http_api import Phase104CommandCenterHttpApp
from ai_test_asset_center.phase104_frontend_integration_workspace import (
    build_frontend_integration_workspace,
    scan_workspace_for_secret_leaks,
    validate_frontend_integration_workspace,
)

PHASE104E_VERSION = "phase104e-frontend-runtime-smoke-v1"

FORBIDDEN_RUNTIME_PATTERNS = (
    "raw-token",
    "raw-cookie",
    "raw-session",
    "raw-password",
    "client_secret=",
    "clientSecret=raw",
    "password=",
    "SESSION=raw",
    "Bearer raw",
    "Traceback (most recent call last)",
)


@dataclass(frozen=True)
class RuntimeSmokeStep:
    key: str
    method: str
    path: str
    status: int
    passed: bool
    detail: str
    data_summary: dict[str, Any]


@dataclass(frozen=True)
class RuntimeSmokeReport:
    passed: bool
    score: int
    version: str
    generated_at: str
    scenario: str
    workspace_dir: str | None
    output_dir: str | None
    seed_project_id: str | None
    created_project_id: str | None
    step_count: int
    passed_step_count: int
    failed_step_count: int
    workspace_validation_passed: bool | None
    redaction_status: str
    secret_leak_findings: list[str]
    steps: list[RuntimeSmokeStep]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["steps"] = [asdict(step) for step in self.steps]
        return redact_value(data)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _safe_json_text(value: Any) -> str:
    return json.dumps(redact_value(value), ensure_ascii=False, indent=2, sort_keys=True)


def _contains_forbidden_runtime_pattern(value: Any) -> list[str]:
    text = _safe_json_text(value).lower()
    return [pattern for pattern in FORBIDDEN_RUNTIME_PATTERNS if pattern.lower() in text]


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _data_summary(data: Any) -> dict[str, Any]:
    """Return a compact, safe summary suitable for reports and CI logs."""
    if isinstance(data, list):
        first = _as_mapping(data[0]) if data else {}
        return redact_value(
            {
                "type": "list",
                "count": len(data),
                "first_keys": sorted(str(key) for key in first.keys())[:10],
            }
        )
    if isinstance(data, Mapping):
        summary: dict[str, Any] = {"type": "object", "keys": sorted(str(key) for key in data.keys())[:12]}
        for key in [
            "project_id",
            "status",
            "score",
            "quality_health_score",
            "launch_recommendation",
            "risk_id",
            "run_id",
            "plan_id",
            "redaction_status",
        ]:
            if key in data:
                summary[key] = data.get(key)
        nested_test_run = _as_mapping(data.get("test_run"))
        if nested_test_run:
            summary["run_id"] = nested_test_run.get("run_id")
            summary["risk_found"] = nested_test_run.get("risk_found")
        return redact_value(summary)
    return {"type": type(data).__name__, "value": redact_value(data)}


class FrontendRuntimeClient:
    """Browser-like client wrapper over the in-process Phase104A app."""

    def __init__(self, app: Phase104CommandCenterHttpApp) -> None:
        self.app = app

    def request(self, method: str, path: str, body: Mapping[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
        response = self.app.handle(method, path, dict(body or {}))
        payload = response.json_body()
        if not isinstance(payload, dict):
            payload = {"success": False, "data": None, "error": {"code": "NON_OBJECT_RESPONSE", "message": "Response was not a JSON object."}, "meta": {}}
        return response.status, redact_value(payload)


def _record_step(
    steps: list[RuntimeSmokeStep],
    *,
    client: FrontendRuntimeClient,
    key: str,
    method: str,
    path: str,
    body: Mapping[str, Any] | None = None,
    expect_success: bool = True,
    validator: Callable[[Any, Mapping[str, Any], int], tuple[bool, str]] | None = None,
) -> Any:
    status, envelope = client.request(method, path, body)
    success = envelope.get("success") is True
    data = envelope.get("data")
    detail = "response envelope accepted"
    passed = success if expect_success else not success
    if expect_success and not success:
        detail = _as_mapping(envelope.get("error")).get("message", "request failed") or "request failed"
    elif not expect_success and success:
        detail = "request unexpectedly succeeded"
    if validator is not None:
        validator_passed, validator_detail = validator(data, envelope, status)
        passed = passed and validator_passed
        detail = validator_detail
    leak_findings = _contains_forbidden_runtime_pattern(envelope)
    if leak_findings:
        passed = False
        detail = f"unsafe runtime pattern: {', '.join(leak_findings)}"
    steps.append(
        RuntimeSmokeStep(
            key=key,
            method=method.upper(),
            path=path,
            status=status,
            passed=passed,
            detail=detail,
            data_summary=_data_summary(data if expect_success else envelope.get("error")),
        )
    )
    return data


def _must_have_keys(*keys: str) -> Callable[[Any, Mapping[str, Any], int], tuple[bool, str]]:
    def validator(data: Any, _envelope: Mapping[str, Any], _status: int) -> tuple[bool, str]:
        item = _as_mapping(data)
        missing = [key for key in keys if key not in item]
        if missing:
            return False, "missing keys: " + ", ".join(missing)
        return True, "required keys present: " + ", ".join(keys)

    return validator


def _list_nonempty(label: str) -> Callable[[Any, Mapping[str, Any], int], tuple[bool, str]]:
    def validator(data: Any, _envelope: Mapping[str, Any], _status: int) -> tuple[bool, str]:
        items = _as_list(data)
        return (bool(items), f"{label} count={len(items)}")

    return validator


def _method_rejected(data: Any, envelope: Mapping[str, Any], status: int) -> tuple[bool, str]:
    error = _as_mapping(envelope.get("error"))
    passed = status in {404, 405} and envelope.get("success") is False and bool(error.get("code"))
    return passed, f"unsafe method rejected with status={status} code={error.get('code')}"


def _risk_detail_validator(data: Any, _envelope: Mapping[str, Any], _status: int) -> tuple[bool, str]:
    item = _as_mapping(data)
    risk = _as_mapping(item.get("risk"))
    evidence = _as_mapping(item.get("evidence_bundle"))
    passed = bool(risk.get("business_impact")) and evidence.get("redaction_status") == "safe" and bool(evidence.get("reproduction_steps"))
    return passed, "risk detail contains business impact and redacted evidence bundle"


def _live_map_validator(data: Any, _envelope: Mapping[str, Any], _status: int) -> tuple[bool, str]:
    item = _as_mapping(data)
    nodes = _as_list(item.get("nodes"))
    overlays = _as_list(item.get("risk_overlays"))
    passed = bool(nodes) and bool(overlays)
    return passed, f"nodes={len(nodes)} risk_overlays={len(overlays)}"


def _dashboard_validator(data: Any, _envelope: Mapping[str, Any], _status: int) -> tuple[bool, str]:
    item = _as_mapping(data)
    passed = "quality_health_score" in item and bool(item.get("executive_summary")) and bool(item.get("top_risks"))
    return passed, "dashboard contains health score, executive summary, and top risks"


def _workspace_findings(workspace_dir: Path | None, *, build_workspace: bool, api_base_url: str) -> tuple[bool | None, list[str]]:
    if workspace_dir is None:
        return None, []
    if build_workspace or not (workspace_dir / "workspace_manifest.json").exists():
        build_frontend_integration_workspace(workspace_dir, api_base_url=api_base_url)
    validation = validate_frontend_integration_workspace(workspace_dir)
    findings: list[str] = []
    if not validation.passed:
        findings.extend(f"workspace:{check.key}:{check.detail}" for check in validation.checks if not check.passed)
    findings.extend(scan_workspace_for_secret_leaks(workspace_dir))
    return validation.passed, findings


def run_frontend_runtime_smoke(
    *,
    workspace_dir: str | Path | None = None,
    output_dir: str | Path | None = None,
    scenario: str = "manufacturing",
    api_base_url: str = "http://127.0.0.1:8790",
    build_workspace: bool = False,
) -> RuntimeSmokeReport:
    """Run a frontend-equivalent runtime smoke against the local HTTP app."""
    workspace_path = Path(workspace_dir) if workspace_dir is not None else None
    workspace_passed, workspace_findings = _workspace_findings(workspace_path, build_workspace=build_workspace, api_base_url=api_base_url)

    app = Phase104CommandCenterHttpApp(seed_scenario=scenario)
    client = FrontendRuntimeClient(app)
    steps: list[RuntimeSmokeStep] = []
    seed_project_id = _as_mapping(app.seed_bundle or {}).get("project_id")
    created_project_id: str | None = None

    _record_step(steps, client=client, key="health", method="GET", path="/api/v1/health", validator=_must_have_keys("status", "version", "redaction_status"))
    _record_step(steps, client=client, key="industry_templates", method="GET", path="/api/v1/industry-templates", validator=_list_nonempty("industry template"))
    _record_step(steps, client=client, key="seed_projects", method="GET", path="/api/v1/projects", validator=_list_nonempty("project"))

    if seed_project_id:
        _record_step(steps, client=client, key="seed_dashboard", method="GET", path=f"/api/v1/projects/{seed_project_id}/command-center", validator=_dashboard_validator)
        _record_step(steps, client=client, key="seed_live_map", method="GET", path=f"/api/v1/projects/{seed_project_id}/live-map", validator=_live_map_validator)
        risks = _record_step(steps, client=client, key="seed_risks", method="GET", path=f"/api/v1/projects/{seed_project_id}/risks", validator=_list_nonempty("risk"))
        risk_id = _as_mapping(_as_list(risks)[0]).get("risk_id") if _as_list(risks) else None
        if risk_id:
            _record_step(steps, client=client, key="seed_risk_detail", method="GET", path=f"/api/v1/projects/{seed_project_id}/risks/{risk_id}", validator=_risk_detail_validator)
        _record_step(steps, client=client, key="seed_value_metrics", method="GET", path=f"/api/v1/projects/{seed_project_id}/value-metrics", validator=_must_have_keys("estimated_hours_saved", "calculation_notes", "redaction_status"))
        _record_step(steps, client=client, key="seed_executive_report", method="GET", path=f"/api/v1/projects/{seed_project_id}/reports/executive", validator=_must_have_keys("executive_summary", "launch_recommendation", "evidence_trust_summary"))

    created = _record_step(
        steps,
        client=client,
        key="create_project",
        method="POST",
        path="/api/v1/projects",
        body={
            "customer_name": "Frontend Runtime Smoke Customer",
            "project_name": "Phase104E Runtime Smoke",
            "system_name": "Manufacturing ERP Runtime Demo",
            "industry": "manufacturing",
            "system_type": "ERP + MES",
            "test_goal": "验证前端写入链路和页面读取链路",
            "owner": "qa_lead",
        },
        validator=_must_have_keys("project_id", "status", "industry"),
    )
    created_project_id = str(_as_mapping(created).get("project_id") or "") or None
    if created_project_id:
        _record_step(steps, client=client, key="apply_template", method="POST", path=f"/api/v1/projects/{created_project_id}/business-model/apply-template", body={"template_id": "industry_manufacturing"}, validator=_must_have_keys("confirmed_business_flows", "confirmed_risk_focus"))
        _record_step(steps, client=client, key="patch_environment_config", method="PATCH", path=f"/api/v1/projects/{created_project_id}/environment/config", body={"base_url": "https://demo.example.local", "auth_type": "none", "session_health_path": "/api/me", "api_smoke_paths": ["/api/orders", "/api/reports/summary"]}, validator=_must_have_keys("project_id", "status", "config"))
        _record_step(steps, client=client, key="run_environment_preflight", method="POST", path=f"/api/v1/projects/{created_project_id}/environment/preflight", body={}, validator=_must_have_keys("score", "status", "redaction_status"))
        _record_step(steps, client=client, key="generate_test_plan", method="POST", path=f"/api/v1/projects/{created_project_id}/test-plan/generate", body={}, validator=_must_have_keys("plan_id", "probe_groups", "coverage_summary"))
        _record_step(steps, client=client, key="get_test_plan", method="GET", path=f"/api/v1/projects/{created_project_id}/test-plan", validator=_must_have_keys("plan_id", "probe_groups"))
        run_data = _record_step(steps, client=client, key="start_test_run", method="POST", path=f"/api/v1/projects/{created_project_id}/test-runs", body={}, validator=_must_have_keys("test_run", "risks", "evidence_count"))
        run_id = _as_mapping(_as_mapping(run_data).get("test_run")).get("run_id")
        if run_id:
            _record_step(steps, client=client, key="get_test_run", method="GET", path=f"/api/v1/projects/{created_project_id}/test-runs/{run_id}", validator=_must_have_keys("run_id", "status", "progress"))
        _record_step(steps, client=client, key="generate_report", method="POST", path=f"/api/v1/projects/{created_project_id}/reports/generate", body={}, validator=_must_have_keys("executive_summary", "launch_recommendation", "markdown"))
        _record_step(steps, client=client, key="created_dashboard", method="GET", path=f"/api/v1/projects/{created_project_id}/command-center", validator=_must_have_keys("quality_health_score", "launch_decision", "executive_summary"))
        _record_step(steps, client=client, key="created_report", method="GET", path=f"/api/v1/projects/{created_project_id}/reports/executive", validator=_must_have_keys("executive_summary", "launch_recommendation", "markdown"))
        _record_step(steps, client=client, key="method_safety", method="DELETE", path=f"/api/v1/projects/{created_project_id}", expect_success=False, validator=_method_rejected)

    secret_findings = list(workspace_findings)
    for step in steps:
        leaks = _contains_forbidden_runtime_pattern(step.data_summary)
        for leak in leaks:
            secret_findings.append(f"{step.key}: {leak}")
    passed_step_count = sum(1 for step in steps if step.passed)
    failed_step_count = len(steps) - passed_step_count
    redaction_status = "safe" if not secret_findings else "failed"
    passed = failed_step_count == 0 and redaction_status == "safe" and (workspace_passed is not False)
    score = int(round((passed_step_count / max(1, len(steps))) * 100))
    if workspace_passed is False:
        score = min(score, 80)
    if redaction_status != "safe":
        score = min(score, 70)

    report = RuntimeSmokeReport(
        passed=passed,
        score=score,
        version=PHASE104E_VERSION,
        generated_at=_now(),
        scenario=scenario,
        workspace_dir=str(workspace_path) if workspace_path else None,
        output_dir=str(output_dir) if output_dir else None,
        seed_project_id=str(seed_project_id) if seed_project_id else None,
        created_project_id=created_project_id,
        step_count=len(steps),
        passed_step_count=passed_step_count,
        failed_step_count=failed_step_count,
        workspace_validation_passed=workspace_passed,
        redaction_status=redaction_status,
        secret_leak_findings=secret_findings,
        steps=steps,
    )
    if output_dir is not None:
        out = Path(output_dir)
        _write_text(out / "frontend_runtime_smoke_report.json", _safe_json_text(report.to_dict()) + "\n")
        _write_text(out / "frontend_runtime_smoke_report.md", render_runtime_smoke_markdown(report))
    return report


def render_runtime_smoke_markdown(report: RuntimeSmokeReport) -> str:
    lines = [
        "# Phase104E 前端运行时联调 Smoke 报告",
        "",
        f"- 结果：{'通过' if report.passed else '未通过'}",
        f"- 得分：{report.score}",
        f"- 场景：`{report.scenario}`",
        f"- Seed 项目：`{report.seed_project_id}`",
        f"- 新建项目：`{report.created_project_id}`",
        f"- 步骤：{report.passed_step_count}/{report.step_count} 通过",
        f"- 工作区验收：{report.workspace_validation_passed}",
        f"- 脱敏状态：{report.redaction_status}",
        "",
        "## 步骤明细",
        "",
        "| 状态 | Key | Method | Path | HTTP | 说明 |",
        "|---|---|---|---|---:|---|",
    ]
    for step in report.steps:
        lines.append(f"| {'通过' if step.passed else '失败'} | `{step.key}` | `{step.method}` | `{step.path}` | {step.status} | {step.detail} |")
    if report.secret_leak_findings:
        lines.extend(["", "## 脱敏问题", ""])
        for finding in report.secret_leak_findings[:20]:
            lines.append(f"- {finding}")
    lines.append("")
    return "\n".join(lines)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Phase104E frontend runtime integration smoke.")
    parser.add_argument("--workspace-dir", default="outputs/phase104_frontend_workspace", help="Generated Phase104D frontend workspace directory.")
    parser.add_argument("--output-dir", default="outputs/phase104_frontend_runtime_smoke", help="Directory for smoke reports.")
    parser.add_argument("--scenario", choices=["manufacturing", "ecommerce", "saas"], default="manufacturing", help="Seed scenario for read-path smoke.")
    parser.add_argument("--api-base-url", default="http://127.0.0.1:8790", help="API base URL written into generated workspace examples.")
    parser.add_argument("--build-workspace", action="store_true", help="Generate/update the Phase104D workspace before smoke.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    report = run_frontend_runtime_smoke(
        workspace_dir=args.workspace_dir,
        output_dir=args.output_dir,
        scenario=args.scenario,
        api_base_url=args.api_base_url,
        build_workspace=args.build_workspace,
    )
    print(_safe_json_text(report.to_dict()))
    return 0 if report.passed else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
