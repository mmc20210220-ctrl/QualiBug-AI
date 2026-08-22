from __future__ import annotations

"""Phase104C: API contract acceptance gate for frontend integration.

Phase104A exposes a mutation-capable local HTTP API and Phase104B exports the
OpenAPI/frontend integration kit.  This module closes the loop with an offline
acceptance gate that validates the exported contract and probes the embedded
HTTP app without opening a socket.

The gate answers the questions a frontend team needs before wiring pages:

* are the OpenAPI, markdown, TypeScript client, and manifest present;
* do route definitions match the exported OpenAPI paths and methods;
* does the TypeScript client expose the critical V1 workflow methods;
* can the local HTTP app execute the PRD flow through customer-safe envelopes;
* do all contract/runtime artifacts stay redacted.
"""

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from ai_test_asset_center.phase103_enterprise_command_center import redact_value
from ai_test_asset_center.phase104_api_contract_exporter import (
    PHASE104B_VERSION,
    assert_contract_safe,
    export_api_contract,
    route_contracts,
)
from ai_test_asset_center.phase104_command_center_http_api import PHASE104A_VERSION, Phase104CommandCenterHttpApp

PHASE104C_VERSION = "phase104c-api-contract-acceptance-v1"

REQUIRED_CONTRACT_FILES: tuple[str, ...] = (
    "openapi.json",
    "API_CONTRACT.md",
    "frontend_api_client.ts",
    "contract_manifest.json",
)

CRITICAL_CLIENT_METHODS: tuple[str, ...] = (
    "createProject",
    "applyBusinessTemplate",
    "patchEnvironmentConfig",
    "runEnvironmentPreflight",
    "generateTestPlan",
    "startTestRun",
    "getCommandCenter",
    "getLiveMap",
    "listRisks",
    "getRiskDetail",
    "getValueMetrics",
    "getExecutiveReport",
)

RUNTIME_SECRET_PATTERNS: tuple[str, ...] = (
    "raw-token",
    "raw-cookie",
    "raw-session",
    "raw-password",
    "client_secret=",
    "password=",
    "SESSION=raw",
    "Bearer raw",
    "DemoPasswordShouldBeRedacted",
    "access_token=",
    "refresh_token=",
)


@dataclass(frozen=True)
class ContractAcceptanceCheck:
    """One contract acceptance result."""

    key: str
    title: str
    passed: bool
    severity: str = "critical"
    detail: str = ""
    suggested_action: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "passed": self.passed,
            "severity": self.severity,
            "detail": self.detail,
            "suggested_action": self.suggested_action,
        }


@dataclass
class ContractAcceptanceReport:
    """Acceptance report for one exported Phase104 API contract."""

    contract_dir: str
    scenario: str
    version: str = PHASE104C_VERSION
    checks: list[ContractAcceptanceCheck] = field(default_factory=list)
    artifacts: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return all(check.passed or check.severity != "critical" for check in self.checks)

    @property
    def score(self) -> int:
        if not self.checks:
            return 0
        passed_count = sum(1 for check in self.checks if check.passed)
        return int(round((passed_count / len(self.checks)) * 100))

    @property
    def failed_checks(self) -> list[ContractAcceptanceCheck]:
        return [check for check in self.checks if not check.passed]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "contract_dir": self.contract_dir,
            "scenario": self.scenario,
            "passed": self.passed,
            "score": self.score,
            "checks": [check.to_dict() for check in self.checks],
            "failed_checks": [check.to_dict() for check in self.failed_checks],
            "artifacts": redact_value(self.artifacts),
        }

    def to_markdown(self) -> str:
        lines = [
            "# Phase104C API 合同验收报告",
            "",
            f"- 版本：`{self.version}`",
            f"- Contract：`{self.contract_dir}`",
            f"- Runtime：`{PHASE104A_VERSION}`",
            f"- Contract Exporter：`{PHASE104B_VERSION}`",
            f"- 场景：`{self.scenario}`",
            f"- 结论：{'通过' if self.passed else '未通过'}",
            f"- 验收分：{self.score}/100",
            "",
            "## 验收项",
            "",
            "| 项 | 结果 | 严重性 | 说明 |",
            "| --- | --- | --- | --- |",
        ]
        for check in self.checks:
            result = "通过" if check.passed else "未通过"
            detail = (check.detail or "-").replace("\n", " ")
            lines.append(f"| {check.title} | {result} | {check.severity} | {detail} |")
        if self.failed_checks:
            lines.extend(["", "## 待处理项", ""])
            for check in self.failed_checks:
                lines.append(f"- **{check.title}**：{check.suggested_action or check.detail or '请检查 API 合同与本地服务。'}")
        lines.extend(
            [
                "",
                "## 安全说明",
                "",
                "本验收报告只记录脱敏后的合同与运行时响应检查，不包含 token、cookie、password、session 或 client_secret 原值。",
                "",
            ]
        )
        return "\n".join(lines)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_text_payload(paths: Sequence[Path]) -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in paths if path.exists())


def _contains_any(text: str, patterns: Sequence[str]) -> list[str]:
    return [pattern for pattern in patterns if pattern and pattern in text]


def _response_json(app: Phase104CommandCenterHttpApp, method: str, path: str, body: Mapping[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    response = app.handle(method, path, body)
    try:
        payload = response.json_body()
    except Exception:
        payload = {}
    return response.status, payload


def _envelope_ok(status: int, payload: Mapping[str, Any], *, expected_status: int = 200) -> bool:
    return status == expected_status and payload.get("success") is True and "data" in payload and "meta" in payload


def validate_contract_artifacts(contract_dir: str | Path, *, scenario: str = "", live_smoke: bool = True) -> ContractAcceptanceReport:
    """Validate an exported Phase104B contract directory and local runtime flow."""
    root = Path(contract_dir)
    report = ContractAcceptanceReport(contract_dir=str(root), scenario=scenario)

    # 1) Required artifact files.
    missing = [name for name in REQUIRED_CONTRACT_FILES if not (root / name).exists()]
    report.checks.append(
        ContractAcceptanceCheck(
            key="required_files",
            title="API 合同文件完整",
            passed=not missing,
            detail="全部合同文件存在" if not missing else f"缺失：{', '.join(missing)}",
            suggested_action="重新运行 Phase104B 合同导出器，确认 openapi.json、API_CONTRACT.md、frontend_api_client.ts 与 manifest 均已生成。",
        )
    )
    if missing:
        return report

    openapi_path = root / "openapi.json"
    markdown_path = root / "API_CONTRACT.md"
    client_path = root / "frontend_api_client.ts"
    manifest_path = root / "contract_manifest.json"

    spec = _load_json(openapi_path)
    manifest = _load_json(manifest_path)
    markdown = markdown_path.read_text(encoding="utf-8")
    client = client_path.read_text(encoding="utf-8")
    report.artifacts = {
        "manifest_version": manifest.get("version"),
        "route_count": manifest.get("route_count"),
        "redaction_status": manifest.get("redaction_status"),
        "artifact_count": len(manifest.get("artifacts", [])),
    }

    # 2) OpenAPI basics.
    paths = spec.get("paths", {}) if isinstance(spec.get("paths"), Mapping) else {}
    components = spec.get("components", {}) if isinstance(spec.get("components"), Mapping) else {}
    expected_routes = route_contracts()
    missing_routes: list[str] = []
    for route in expected_routes:
        if route.path not in paths or route.method.lower() not in paths.get(route.path, {}):
            missing_routes.append(f"{route.method} {route.path}")
    operation_ids = [
        methods[method].get("operationId")
        for methods in paths.values()
        if isinstance(methods, Mapping)
        for method in methods
        if isinstance(methods.get(method), Mapping)
    ]
    duplicate_ops = sorted({item for item in operation_ids if item and operation_ids.count(item) > 1})
    report.checks.append(
        ContractAcceptanceCheck(
            key="openapi_routes",
            title="OpenAPI 路由与 operationId 完整",
            passed=not missing_routes and not duplicate_ops and spec.get("openapi") == "3.0.3",
            detail=(
                f"routes={len(expected_routes)}, schemas={len(components.get('schemas', {}))}"
                if not missing_routes and not duplicate_ops
                else f"missing={missing_routes}; duplicate_ops={duplicate_ops}"
            ),
            suggested_action="检查 Phase104B route_contracts 与 build_openapi_spec 是否同步。",
        )
    )

    # 3) Envelope schemas and error documentation.
    schemas = components.get("schemas", {}) if isinstance(components.get("schemas"), Mapping) else {}
    required_schemas = {"ApiEnvelope", "ApiError", "ApiMeta", "ProjectCreateRequest", "EnvironmentPreflightRequest", "TestRunStartRequest"}
    schema_missing = sorted(required_schemas.difference(schemas))
    docs_ok = "统一 envelope" in markdown and "错误响应" in markdown and "脱敏" in markdown
    report.checks.append(
        ContractAcceptanceCheck(
            key="envelope_and_docs",
            title="统一响应 envelope 与错误/脱敏文档完整",
            passed=not schema_missing and docs_ok,
            detail="schema 与文档完整" if not schema_missing and docs_ok else f"schema_missing={schema_missing}; docs_ok={docs_ok}",
            suggested_action="补充 ApiEnvelope/ApiError/ApiMeta schema，并在 API_CONTRACT.md 说明错误响应和脱敏规范。",
        )
    )

    # 4) Frontend client critical workflow methods.
    missing_methods = [method for method in CRITICAL_CLIENT_METHODS if f"{method}(" not in client]
    report.checks.append(
        ContractAcceptanceCheck(
            key="frontend_client_methods",
            title="前端 TypeScript client 覆盖核心联调流程",
            passed=not missing_methods and "QualiBugCommandCenterClient" in client and "ApiEnvelope" in client,
            detail="核心方法完整" if not missing_methods else f"缺失方法：{', '.join(missing_methods)}",
            suggested_action="更新 render_frontend_api_client，补齐项目初始化、环境预检、测试计划、运行、驾驶舱、风险与报告方法。",
        )
    )

    # 5) Manifest integrity basics.
    artifact_names = {item.get("path") for item in manifest.get("artifacts", []) if isinstance(item, Mapping)}
    manifest_ok = (
        manifest.get("version") == PHASE104B_VERSION
        and manifest.get("runtime_version") == PHASE104A_VERSION
        and manifest.get("route_count") == len(expected_routes)
        and manifest.get("redaction_status") == "safe"
        and set(REQUIRED_CONTRACT_FILES[:-1]).issubset(artifact_names)
    )
    report.checks.append(
        ContractAcceptanceCheck(
            key="manifest_integrity",
            title="contract_manifest 基本完整性通过",
            passed=manifest_ok,
            detail=f"route_count={manifest.get('route_count')}, redaction={manifest.get('redaction_status')}",
            suggested_action="重新导出合同，确保 manifest 中 route_count、runtime_version、redaction_status 与 artifacts 正确。",
        )
    )

    # 6) Contract redaction scan.
    combined = _safe_text_payload([openapi_path, markdown_path, client_path, manifest_path])
    leaked = _contains_any(combined, RUNTIME_SECRET_PATTERNS)
    safe_by_exporter = True
    try:
        assert_contract_safe([combined])
    except Exception:
        safe_by_exporter = False
    report.checks.append(
        ContractAcceptanceCheck(
            key="contract_redaction",
            title="合同与前端 client 未包含原始凭证示例",
            passed=not leaked and safe_by_exporter,
            detail="未发现敏感示例" if not leaked and safe_by_exporter else f"命中：{', '.join(leaked) or 'exporter guard'}",
            suggested_action="移除合同示例中的 token/cookie/password/session/client_secret 原值，仅保留脱敏占位或状态字段。",
        )
    )

    if live_smoke:
        report.checks.extend(_runtime_smoke_checks(scenario=scenario))

    return report


def _runtime_smoke_checks(*, scenario: str) -> list[ContractAcceptanceCheck]:
    """Probe the Phase104A route adapter without opening a network socket."""
    checks: list[ContractAcceptanceCheck] = []
    app = Phase104CommandCenterHttpApp(seed_scenario=scenario)
    project_id = str((app.seed_bundle or {}).get("project_id") or "")
    smoke_payloads: list[dict[str, Any]] = []

    status, health = _response_json(app, "GET", "/api/v1/health")
    smoke_payloads.append(health)
    status_projects, projects = _response_json(app, "GET", "/api/v1/projects")
    smoke_payloads.append(projects)
    checks.append(
        ContractAcceptanceCheck(
            key="runtime_health_and_seed",
            title="本地 API health 与 seed 项目可用",
            passed=_envelope_ok(status, health) and _envelope_ok(status_projects, projects) and bool(project_id),
            detail=f"health={status}, projects={status_projects}, project_id={project_id or '-'}",
            suggested_action="检查 Phase104CommandCenterHttpApp seed_scenario 初始化和 /health /projects 路由。",
        )
    )

    base = f"/api/v1/projects/{project_id}"
    read_paths = [
        ("project", base),
        ("onboarding", f"{base}/onboarding"),
        ("business_model", f"{base}/business-model"),
        ("environment", f"{base}/environment/readiness"),
        ("test_plan", f"{base}/test-plan"),
        ("command_center", f"{base}/command-center"),
        ("live_map", f"{base}/live-map"),
        ("risks", f"{base}/risks"),
        ("value_metrics", f"{base}/value-metrics"),
        ("report", f"{base}/reports/executive"),
    ]
    failed_reads: list[str] = []
    captured: dict[str, Any] = {}
    for key, path in read_paths:
        status_code, payload = _response_json(app, "GET", path)
        smoke_payloads.append(payload)
        captured[key] = payload.get("data")
        if not _envelope_ok(status_code, payload):
            failed_reads.append(f"{key}:{status_code}")
    checks.append(
        ContractAcceptanceCheck(
            key="runtime_read_routes",
            title="本地 API 只读页面路由可用",
            passed=not failed_reads,
            detail="全部核心页面路由成功" if not failed_reads else ", ".join(failed_reads),
            suggested_action="检查 Phase104A 项目级 GET 路由与 seed_demo_project 输出是否匹配。",
        )
    )

    command_center = captured.get("command_center") or {}
    live_map = captured.get("live_map") or {}
    risks = captured.get("risks") or []
    top_risks = command_center.get("top_risks") if isinstance(command_center, Mapping) else []
    checks.append(
        ContractAcceptanceCheck(
            key="runtime_business_value_payloads",
            title="运行时返回驾驶舱、地图、风险与 ROI 业务价值数据",
            passed=(
                isinstance(command_center, Mapping)
                and bool(command_center.get("quality_health_score") is not None)
                and bool(top_risks)
                and isinstance(live_map, Mapping)
                and bool(live_map.get("nodes"))
                and bool(live_map.get("risk_overlays"))
                and isinstance(risks, list)
                and bool(risks)
            ),
            detail=f"risks={len(risks) if isinstance(risks, list) else 0}, map_nodes={len(live_map.get('nodes', [])) if isinstance(live_map, Mapping) else 0}",
            suggested_action="检查 seed 数据、RiskFinding 生成、RealtimeMapSnapshot 生成和 CommandCenter 聚合逻辑。",
        )
    )

    risk_id = ""
    if isinstance(risks, list) and risks:
        first_risk = risks[0]
        if isinstance(first_risk, Mapping):
            risk_id = str(first_risk.get("risk_id") or "")
    if risk_id:
        risk_status, risk_payload = _response_json(app, "GET", f"{base}/risks/{risk_id}")
        smoke_payloads.append(risk_payload)
        risk_data = risk_payload.get("data") if isinstance(risk_payload, Mapping) else {}
        checks.append(
            ContractAcceptanceCheck(
                key="runtime_risk_detail",
                title="风险详情包含业务影响与证据链",
                passed=(
                    _envelope_ok(risk_status, risk_payload)
                    and isinstance(risk_data, Mapping)
                    and bool((risk_data.get("risk") or {}).get("business_impact"))
                    and bool(risk_data.get("evidence") or risk_data.get("evidence_bundle"))
                ),
                detail=f"risk_id={risk_id}, status={risk_status}",
                suggested_action="检查 getRiskDetail 返回结构，确保 risk + evidence 同时返回并已脱敏。",
            )
        )
    else:
        checks.append(
            ContractAcceptanceCheck(
                key="runtime_risk_detail",
                title="风险详情包含业务影响与证据链",
                passed=False,
                detail="风险列表为空，无法验证风险详情。",
                suggested_action="检查 seed 数据和 start_test_run 风险生成逻辑。",
            )
        )

    # Mutation smoke: create a project and run the same initialization flow.
    create_status, create_payload = _response_json(
        app,
        "POST",
        "/api/v1/projects",
        {
            "customer_name": "联调客户",
            "project_name": "API 合同验收项目",
            "system_name": "联调系统",
            "industry": "manufacturing",
            "system_type": "ERP",
            "test_goal": "前端联调验收",
            "owner": "qa",
        },
    )
    smoke_payloads.append(create_payload)
    created_id = ""
    if isinstance(create_payload.get("data"), Mapping):
        created_id = str(create_payload["data"].get("project_id") or "")
    mutation_failures: list[str] = []
    if not _envelope_ok(create_status, create_payload, expected_status=201) or not created_id:
        mutation_failures.append(f"create:{create_status}")
    else:
        mutation_steps = [
            ("apply_template", "POST", f"/api/v1/projects/{created_id}/business-model/apply-template", {"template_id": "industry_manufacturing", "approved_by": "qa"}, 200),
            ("patch_env", "PATCH", f"/api/v1/projects/{created_id}/environment/config", {"base_url": "https://staging.example.local", "auth_type": "username_password", "session_health_path": "/api/me", "api_smoke_paths": ["/api/orders"], "credential_status": {"password": "DemoPasswordShouldBeRedacted"}}, 200),
            ("preflight", "POST", f"/api/v1/projects/{created_id}/environment/preflight", {"safe_execution_mode": "read_only"}, 200),
            ("plan", "POST", f"/api/v1/projects/{created_id}/test-plan/generate", {"plan_name": "V1 联调计划"}, 200),
            ("run", "POST", f"/api/v1/projects/{created_id}/test-runs", {"findings": []}, 201),
            ("report", "POST", f"/api/v1/projects/{created_id}/reports/generate", {}, 200),
        ]
        for key, method, path, body, expected in mutation_steps:
            status_code, payload = _response_json(app, method, path, body)
            smoke_payloads.append(payload)
            if not _envelope_ok(status_code, payload, expected_status=expected):
                mutation_failures.append(f"{key}:{status_code}")
    checks.append(
        ContractAcceptanceCheck(
            key="runtime_mutation_flow",
            title="本地 API 可执行前端初始化写入流程",
            passed=not mutation_failures,
            detail="创建项目到生成报告流程通过" if not mutation_failures else ", ".join(mutation_failures),
            suggested_action="检查 Phase104A POST/PATCH 路由、请求体解析和 EnterpriseCommandCenterAPI 写入方法。",
        )
    )

    method_status, method_payload = _response_json(app, "DELETE", f"{base}/command-center")
    smoke_payloads.append(method_payload)
    checks.append(
        ContractAcceptanceCheck(
            key="runtime_method_guard",
            title="非白名单 HTTP 方法返回客户安全错误",
            passed=method_status in {404, 405} and method_payload.get("success") is False and "traceback" not in json.dumps(method_payload).lower(),
            detail=f"DELETE command-center -> {method_status}",
            suggested_action="检查方法白名单和 api_error_response，避免暴露 Python traceback。",
        )
    )

    runtime_text = json.dumps(redact_value(smoke_payloads), ensure_ascii=False, sort_keys=True)
    leaks = _contains_any(runtime_text, RUNTIME_SECRET_PATTERNS)
    checks.append(
        ContractAcceptanceCheck(
            key="runtime_redaction",
            title="运行时 API 响应未泄露原始凭证",
            passed=not leaks,
            detail="未发现敏感凭证模式" if not leaks else f"命中：{', '.join(leaks)}",
            suggested_action="检查 Phase104A/Phase103 API facade 的 redact_value 调用和环境配置回显字段。",
        )
    )

    return checks


def write_acceptance_report(report: ContractAcceptanceReport, output_dir: str | Path) -> dict[str, Any]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    data = report.to_dict()
    (out / "api_contract_acceptance_report.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (out / "api_contract_acceptance_report.md").write_text(report.to_markdown(), encoding="utf-8")
    return data


def run_api_contract_acceptance(
    *,
    contract_dir: str | Path,
    output_dir: str | Path,
    build_first: bool = False,
    scenario: str = "manufacturing",
    live_smoke: bool = True,
) -> dict[str, Any]:
    contract_path = Path(contract_dir)
    if build_first:
        export_api_contract(contract_path)
    report = validate_contract_artifacts(contract_path, scenario=scenario, live_smoke=live_smoke)
    return write_acceptance_report(report, output_dir)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate Phase104B API contract and Phase104A local runtime integration.")
    parser.add_argument("--contract-dir", default="outputs/phase104_api_contract", help="Directory containing openapi.json and frontend contract artifacts.")
    parser.add_argument("--output-dir", default="outputs/phase104_api_contract_acceptance", help="Directory for acceptance reports.")
    parser.add_argument("--build-first", action="store_true", help="Export the API contract before validating it.")
    parser.add_argument("--scenario", choices=["manufacturing", "ecommerce", "saas"], default="manufacturing", help="Seed scenario for embedded runtime smoke tests.")
    parser.add_argument("--skip-live-smoke", action="store_true", help="Only validate exported files; skip embedded HTTP app smoke workflow.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    data = run_api_contract_acceptance(
        contract_dir=args.contract_dir,
        output_dir=args.output_dir,
        build_first=args.build_first,
        scenario=args.scenario,
        live_smoke=not args.skip_live_smoke,
    )
    print(f"Phase104C API contract acceptance: {'PASS' if data['passed'] else 'FAIL'} ({data['score']}/100)")
    print(f"Report: {Path(args.output_dir) / 'api_contract_acceptance_report.md'}")
    return 0 if data["passed"] else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "PHASE104C_VERSION",
    "ContractAcceptanceCheck",
    "ContractAcceptanceReport",
    "run_api_contract_acceptance",
    "validate_contract_artifacts",
    "write_acceptance_report",
    "main",
]
