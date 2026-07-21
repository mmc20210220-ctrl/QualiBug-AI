from __future__ import annotations

"""Phase104D: frontend integration workspace generator.

Phase104A exposed a mutation-capable local HTTP API and Phase104B/C produced
and validated its API contract.  This module turns those contract artifacts into
a small frontend handoff workspace so a web frontend can start integration
without reading backend internals.

The generated workspace is deliberately framework-neutral.  It contains:

* exported OpenAPI/API contract artifacts under ``contract/``;
* a TypeScript fetch client copied from the contract exporter;
* page-data adapter helpers that map API envelopes to V1 UI view models;
* an integration workflow smoke example for the PRD onboarding path;
* environment examples and a human-readable checklist;
* a manifest plus a validator for CI/local handoff.

Security posture:
* no raw token/cookie/password/session/client_secret example values are written;
* generated content is scanned before being declared acceptable;
* the generated sample uses placeholders and safe local URLs only.
"""
import os

import argparse
import hashlib
import json
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from ai_test_asset_center.phase104_api_contract_exporter import (
    PHASE104B_VERSION,
    export_api_contract,
    render_frontend_api_client,
)

PHASE104D_VERSION = "phase104d-frontend-integration-workspace-v1"

SENSITIVE_PATTERNS = (
    "raw-token",
    "raw-cookie",
    "raw-session",
    "raw-password",
    "client_secret=",
    "clientSecret=raw",
    "password=",
    "SESSION=raw",
    "Bearer raw",
    "DemoPasswordShouldBeRedacted",
    "Authorization: Bearer ey",
)

REQUIRED_WORKSPACE_FILES = (
    "workspace_manifest.json",
    "README_FRONTEND_INTEGRATION.md",
    "INTEGRATION_CHECKLIST.md",
    ".env.example",
    "package.json",
    "contract/openapi.json",
    "contract/API_CONTRACT.md",
    "contract/frontend_api_client.ts",
    "src/api/qualibugClient.ts",
    "src/api/qualibugWorkflowSmoke.ts",
    "src/api/pageDataAdapters.ts",
    "src/types/qualibug.ts",
)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _read_all_text_files(base: Path) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() in {".md", ".json", ".ts", ".tsx", ".js", ".env", ".example", ""}:
            try:
                items.append((path.relative_to(base).as_posix(), path.read_text(encoding="utf-8")))
            except UnicodeDecodeError:
                continue
    return items


def scan_workspace_for_secret_leaks(base: Path) -> list[str]:
    """Return leak findings for forbidden example credential patterns."""
    findings: list[str] = []
    for rel, text in _read_all_text_files(base):
        lowered = text.lower()
        for pattern in SENSITIVE_PATTERNS:
            if pattern.lower() in lowered:
                findings.append(f"{rel}: contains forbidden pattern {pattern}")
    return findings


def render_env_example(api_base_url: str) -> str:
    return f"""# QualiBug Phase104D frontend integration environment
# Copy this file to .env.local in your frontend workspace.
# Do not place real customer credentials in source control.

VITE_QUALIBUG_API_BASE_URL={api_base_url.rstrip('/')}
VITE_QUALIBUG_API_TIMEOUT_MS=15000
VITE_QUALIBUG_DEFAULT_SCENARIO=manufacturing
VITE_QUALIBUG_REDACTION_MODE=safe
"""


def render_package_json() -> str:
    data = {
        "name": "qualibug-phase104-frontend-integration-workspace",
        "private": True,
        "version": "0.1.0",
        "type": "module",
        "description": "Framework-neutral frontend handoff workspace for QualiBug Enterprise Command Center V1 APIs.",
        "scripts": {
            "typecheck": "tsc --noEmit",
            "contract:readme": "echo See contract/API_CONTRACT.md",
            "api:dev": "python -m ai_test_asset_center.phase104_command_center_http_api --seed-scenario manufacturing --port 8088",
        },
        "devDependencies": {
            "typescript": "^5.0.0"
        },
    }
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def render_typescript_types() -> str:
    return """// Phase104D shared frontend types for QualiBug V1 pages.
// These are intentionally lightweight view-model types. The full route contract
// lives in ../contract/openapi.json and contract/API_CONTRACT.md.

export type ApiEnvelope<T = unknown> = {
  success: boolean;
  data: T | null;
  error: ApiError | null;
  meta: Record<string, unknown>;
};

export type ApiError = {
  code: string;
  message: string;
  status?: number;
  details?: Record<string, unknown>;
};

export type ProjectSummary = {
  project_id: string;
  project_name?: string;
  customer_name?: string;
  system_name?: string;
  industry?: string;
  status?: string;
};

export type DashboardViewModel = {
  qualityHealthScore: number | null;
  launchRecommendation: string;
  launchSummary: string;
  coreCoverageRate: number | null;
  launchBlockingRiskCount: number;
  estimatedHoursSaved: number | null;
  environmentStatus: string;
  topRisks: RiskCardViewModel[];
  executiveSummary: string;
};

export type RiskCardViewModel = {
  riskId: string;
  title: string;
  severity: string;
  businessImpact: string;
  affectedFlowName: string;
  launchBlocking: boolean;
  evidenceScore: number | null;
  reproducibilityScore: number | null;
  status: string;
};

export type EnvironmentViewModel = {
  status: string;
  score: number | null;
  allowFormalTest: boolean;
  safeExecutionMode: string;
  blockers: string[];
  suggestedActions: string[];
  requiredInputs: Array<Record<string, unknown>>;
};

export type LiveMapViewModel = {
  nodeCount: number;
  edgeCount: number;
  riskOverlayCount: number;
  highestRisk: string;
  events: Array<Record<string, unknown>>;
};

export type ExecutiveReportViewModel = {
  title: string;
  launchRecommendation: string;
  executiveSummary: string;
  topRiskCount: number;
  nextActionCount: number;
};
"""


def render_page_data_adapters() -> str:
    return """import type {
  DashboardViewModel,
  EnvironmentViewModel,
  ExecutiveReportViewModel,
  LiveMapViewModel,
  RiskCardViewModel,
} from '../types/qualibug';

function asRecord(value: unknown): Record<string, any> {
  return value && typeof value === 'object' ? value as Record<string, any> : {};
}

function asArray(value: unknown): any[] {
  return Array.isArray(value) ? value : [];
}

function asNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function asString(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback;
}

export function toRiskCardViewModel(risk: unknown): RiskCardViewModel {
  const item = asRecord(risk);
  const flow = asRecord(item.affected_business_flow);
  return {
    riskId: asString(item.risk_id),
    title: asString(item.title, '未命名风险'),
    severity: asString(item.severity, 'unknown'),
    businessImpact: asString(item.business_impact, '暂无业务影响说明'),
    affectedFlowName: asString(flow.name, '未映射业务链路'),
    launchBlocking: item.launch_blocking === true,
    evidenceScore: asNumber(item.evidence_score),
    reproducibilityScore: asNumber(item.reproducibility_score),
    status: asString(item.status, 'detected'),
  };
}

export function toDashboardViewModel(data: unknown): DashboardViewModel {
  const snapshot = asRecord(data);
  const launch = asRecord(snapshot.launch_decision);
  const flowSummary = asRecord(snapshot.business_flow_summary);
  const riskSummary = asRecord(snapshot.risk_summary);
  const valueMetrics = asRecord(snapshot.value_metrics);
  const environment = asRecord(snapshot.environment_readiness);
  return {
    qualityHealthScore: asNumber(snapshot.quality_health_score),
    launchRecommendation: asString(launch.recommendation, 'UNKNOWN'),
    launchSummary: asString(launch.summary, ''),
    coreCoverageRate: asNumber(flowSummary.coverage_rate),
    launchBlockingRiskCount: Number(riskSummary.launch_blocking || 0),
    estimatedHoursSaved: asNumber(valueMetrics.estimated_hours_saved),
    environmentStatus: asString(environment.status, 'unknown'),
    topRisks: asArray(snapshot.top_risks).map(toRiskCardViewModel),
    executiveSummary: asString(snapshot.executive_summary, ''),
  };
}

export function toEnvironmentViewModel(data: unknown): EnvironmentViewModel {
  const env = asRecord(data);
  return {
    status: asString(env.status, 'unknown'),
    score: asNumber(env.score),
    allowFormalTest: env.allow_formal_test === true,
    safeExecutionMode: asString(env.safe_execution_mode, 'read_only'),
    blockers: asArray(env.current_blockers).map(String),
    suggestedActions: asArray(env.suggested_actions).map(String),
    requiredInputs: asArray(env.required_customer_inputs).map(asRecord),
  };
}

export function toLiveMapViewModel(data: unknown): LiveMapViewModel {
  const map = asRecord(data);
  const overlays = asArray(map.risk_overlays);
  const severities = overlays.map((overlay) => asString(asRecord(overlay).severity, 'unknown'));
  const highestRisk = severities.includes('critical') ? 'critical' : severities[0] || 'none';
  return {
    nodeCount: asArray(map.nodes).length,
    edgeCount: asArray(map.edges).length,
    riskOverlayCount: overlays.length,
    highestRisk,
    events: asArray(map.events).map(asRecord),
  };
}

export function toExecutiveReportViewModel(data: unknown): ExecutiveReportViewModel {
  const report = asRecord(data);
  return {
    title: asString(report.title, '质量风险评估报告'),
    launchRecommendation: asString(report.launch_recommendation, 'UNKNOWN'),
    executiveSummary: asString(report.executive_summary, ''),
    topRiskCount: asArray(report.top_risks).length,
    nextActionCount: asArray(report.next_actions).length,
  };
}
"""


def render_workflow_smoke(api_base_url: str) -> str:
    return f"""import {{ QualiBugCommandCenterClient }} from './qualibugClient';
import {{ toDashboardViewModel, toEnvironmentViewModel, toLiveMapViewModel }} from './pageDataAdapters';

// Phase104D framework-neutral frontend smoke workflow.
// Start backend first:
// python -m ai_test_asset_center.phase104_command_center_http_api --seed-scenario manufacturing --port 8088

const client = new QualiBugCommandCenterClient('{api_base_url.rstrip('/')}');

function unwrap<T>(envelope: {{ success: boolean; data: T | null; error: any }}): T {{
  if (!envelope.success || envelope.data == null) {{
    throw new Error(envelope.error?.message || 'QualiBug API request failed');
  }}
  return envelope.data;
}}

export async function runQualiBugFrontendWorkflowSmoke() {{
  const created = unwrap(await client.createProject({{
    customer_name: 'Frontend Demo Customer',
    project_name: 'Frontend Integration Smoke',
    system_name: 'ERP Demo System',
    industry: 'manufacturing',
    system_type: 'ERP',
    test_goal: '前端联调冒烟验证',
  }}));
  const projectId = (created as any).project_id;

  await client.applyBusinessTemplate(projectId, {{ template_id: 'industry_manufacturing' }});
  await client.patchEnvironmentConfig(projectId, {{
    base_url: 'https://demo.example.local',
    auth_type: 'username_password_csrf',
    session_health_path: '/api/me',
    api_smoke_paths: ['/api/orders', '/api/reports/summary'],
  }});
  const environment = toEnvironmentViewModel(unwrap(await client.runEnvironmentPreflight(projectId, {{}})));
  await client.generateTestPlan(projectId, {{}});
  await client.startTestRun(projectId, {{}});
  await client.generateExecutiveReport(projectId, {{}});

  const dashboard = toDashboardViewModel(unwrap(await client.getCommandCenter(projectId)));
  const liveMap = toLiveMapViewModel(unwrap(await client.getLiveMap(projectId)));

  return {{ projectId, environment, dashboard, liveMap }};
}}
"""


def render_readme(api_base_url: str) -> str:
    return f"""# Phase104D 前端联调工作区

本目录是 QualiBug 企业质量指挥中心 V1 的前端联调交接包。它基于 Phase104A 可写本地 HTTP API、Phase104B API 合同和 Phase104C 合同验收门禁生成。

## 启动后端

```powershell
python -m ai_test_asset_center.phase104_command_center_http_api --seed-scenario manufacturing --port 8088
```

默认 API 地址：`{api_base_url.rstrip('/')}`

## 目录结构

```text
contract/openapi.json                 OpenAPI 3.0.3 合同
contract/API_CONTRACT.md              人类可读接口文档
contract/frontend_api_client.ts       合同导出的 TypeScript client
src/api/qualibugClient.ts             前端项目可直接复制的 client
src/api/pageDataAdapters.ts           页面 ViewModel 适配器
src/api/qualibugWorkflowSmoke.ts      前端联调冒烟流程示例
src/types/qualibug.ts                 轻量页面类型
.env.example                          本地环境变量示例
INTEGRATION_CHECKLIST.md              联调清单
workspace_manifest.json               工作区清单
```

## 推荐联调顺序

1. 调 `GET /api/v1/health` 确认后端可用。
2. 调 `GET /api/v1/projects` 获取 seed 项目。
3. 打开第一个项目的 `command-center`、`environment/readiness`、`live-map`、`risks`、`reports/executive`。
4. 再跑创建项目 → 应用模板 → 环境预检 → 生成计划 → 启动测试 → 生成报告的写入链路。
5. 前端页面统一使用 `success/data/error/meta` envelope，不要绕过错误消息。

## 安全约束

- 不要把真实 token、cookie、password、session、client_secret 写入前端仓库。
- 所有凭证输入只用于本地调试或客户授权环境，不进入演示报告。
- 页面展示以业务对象为主，不直接展示原始请求头和原始凭证。
"""


def render_checklist() -> str:
    return """# Phase104D 前端联调清单

## 环境

- [ ] 已启动 `phase104_command_center_http_api`
- [ ] `GET /api/v1/health` 返回 `success=true`
- [ ] `.env.local` 已设置 `VITE_QUALIBUG_API_BASE_URL`
- [ ] 浏览器 CORS 请求正常

## 页面数据

- [ ] 质量驾驶舱读取 `/command-center`
- [ ] 环境适配中心读取 `/environment/readiness`
- [ ] 测试计划页读取 `/test-plan`
- [ ] 实时地图读取 `/live-map`
- [ ] 风险列表读取 `/risks`
- [ ] 证据详情读取 `/risks/{risk_id}`
- [ ] ROI 页面读取 `/value-metrics`
- [ ] 成果战报读取 `/reports/executive`

## 写入链路

- [ ] 创建项目
- [ ] 应用行业模板
- [ ] 保存环境配置
- [ ] 执行环境预检
- [ ] 生成测试计划
- [ ] 启动测试运行
- [ ] 生成成果战报

## 安全

- [ ] 页面不展示 token/cookie/password/session/client_secret 原值
- [ ] 错误态显示业务化 message，不展示 Python traceback
- [ ] 证据链详情显示脱敏说明
"""


@dataclass(frozen=True)
class WorkspaceValidationCheck:
    key: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class WorkspaceValidationReport:
    passed: bool
    score: int
    output_dir: str
    checks: list[WorkspaceValidationCheck]
    artifacts: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["checks"] = [asdict(check) for check in self.checks]
        return data


def _workspace_files_manifest(output_dir: Path) -> dict[str, dict[str, Any]]:
    files: dict[str, dict[str, Any]] = {}
    for path in sorted(output_dir.rglob("*")):
        if path.is_file():
            rel = path.relative_to(output_dir).as_posix()
            files[rel] = {"size_bytes": path.stat().st_size, "sha256": _sha256_file(path)}
    return files


def build_frontend_integration_workspace(
    output_dir: str | Path,
    *,
    api_base_url: str = os.environ.get("QUALIBUG_API_BASE_URL", "http://127.0.0.1:8088"),
    overwrite: bool = True,
) -> dict[str, Any]:
    """Generate a framework-neutral frontend handoff workspace."""
    root = Path(output_dir)
    if root.exists() and any(root.iterdir()) and not overwrite:
        raise FileExistsError(f"output_dir is not empty: {root}")
    root.mkdir(parents=True, exist_ok=True)

    contract_dir = root / "contract"
    contract_manifest = export_api_contract(contract_dir)

    client_text = render_frontend_api_client()
    _write_text(root / "src" / "api" / "qualibugClient.ts", client_text)
    _write_text(root / "src" / "api" / "pageDataAdapters.ts", render_page_data_adapters())
    _write_text(root / "src" / "api" / "qualibugWorkflowSmoke.ts", render_workflow_smoke(api_base_url))
    _write_text(root / "src" / "types" / "qualibug.ts", render_typescript_types())
    _write_text(root / ".env.example", render_env_example(api_base_url))
    _write_text(root / "package.json", render_package_json())
    _write_text(root / "README_FRONTEND_INTEGRATION.md", render_readme(api_base_url))
    _write_text(root / "INTEGRATION_CHECKLIST.md", render_checklist())

    leaks = scan_workspace_for_secret_leaks(root)
    manifest = {
        "version": PHASE104D_VERSION,
        "generated_at": _now(),
        "api_base_url": api_base_url.rstrip("/"),
        "contract_version": PHASE104B_VERSION,
        "contract_manifest": contract_manifest,
        "required_files": list(REQUIRED_WORKSPACE_FILES),
        "files": _workspace_files_manifest(root),
        "redaction_status": "safe" if not leaks else "failed",
        "secret_leak_findings": leaks,
        "frontend_entrypoints": {
            "client": "src/api/qualibugClient.ts",
            "adapters": "src/api/pageDataAdapters.ts",
            "workflow_smoke": "src/api/qualibugWorkflowSmoke.ts",
            "types": "src/types/qualibug.ts",
        },
    }
    _write_text(root / "workspace_manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    # Recompute manifest including workspace_manifest itself.
    manifest["files"] = _workspace_files_manifest(root)
    _write_text(root / "workspace_manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return manifest


def validate_frontend_integration_workspace(output_dir: str | Path) -> WorkspaceValidationReport:
    root = Path(output_dir)
    checks: list[WorkspaceValidationCheck] = []

    missing = [rel for rel in REQUIRED_WORKSPACE_FILES if not (root / rel).exists()]
    checks.append(
        WorkspaceValidationCheck(
            "required_files",
            not missing,
            "required workspace files present" if not missing else "missing: " + ", ".join(missing),
        )
    )

    manifest_path = root / "workspace_manifest.json"
    manifest: dict[str, Any] = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            checks.append(WorkspaceValidationCheck("manifest_parse", False, f"manifest parse failed: {exc}"))
        else:
            checks.append(
                WorkspaceValidationCheck(
                    "manifest_parse",
                    manifest.get("version") == PHASE104D_VERSION,
                    f"version={manifest.get('version')}",
                )
            )
    else:
        checks.append(WorkspaceValidationCheck("manifest_parse", False, "workspace_manifest.json missing"))

    client_text = (root / "src" / "api" / "qualibugClient.ts").read_text(encoding="utf-8") if (root / "src" / "api" / "qualibugClient.ts").exists() else ""
    checks.append(
        WorkspaceValidationCheck(
            "client_methods",
            all(name in client_text for name in ["getCommandCenter", "runEnvironmentPreflight", "startTestRun", "generateExecutiveReport"]),
            "frontend client covers V1 integration workflow",
        )
    )

    adapters_text = (root / "src" / "api" / "pageDataAdapters.ts").read_text(encoding="utf-8") if (root / "src" / "api" / "pageDataAdapters.ts").exists() else ""
    checks.append(
        WorkspaceValidationCheck(
            "page_adapters",
            all(name in adapters_text for name in ["toDashboardViewModel", "toEnvironmentViewModel", "toLiveMapViewModel", "toRiskCardViewModel"]),
            "page adapters cover dashboard/environment/map/risk pages",
        )
    )

    workflow_text = (root / "src" / "api" / "qualibugWorkflowSmoke.ts").read_text(encoding="utf-8") if (root / "src" / "api" / "qualibugWorkflowSmoke.ts").exists() else ""
    checks.append(
        WorkspaceValidationCheck(
            "workflow_smoke",
            all(name in workflow_text for name in ["createProject", "applyBusinessTemplate", "generateTestPlan", "startTestRun"]),
            "workflow smoke covers create/template/preflight/plan/run/report",
        )
    )

    leaks = scan_workspace_for_secret_leaks(root) if root.exists() else ["workspace missing"]
    checks.append(
        WorkspaceValidationCheck(
            "redaction",
            not leaks,
            "no forbidden credential examples found" if not leaks else "; ".join(leaks[:5]),
        )
    )

    passed_count = sum(1 for check in checks if check.passed)
    score = int(round((passed_count / max(1, len(checks))) * 100))
    return WorkspaceValidationReport(
        passed=all(check.passed for check in checks),
        score=score,
        output_dir=str(root),
        checks=checks,
        artifacts={
            "file_count": len(_workspace_files_manifest(root)) if root.exists() else 0,
            "redaction_status": "safe" if not leaks else "failed",
            "api_base_url": manifest.get("api_base_url"),
        },
    )


def render_validation_markdown(report: WorkspaceValidationReport) -> str:
    lines = [
        "# Phase104D 前端联调工作区验收报告",
        "",
        f"- 结果：{'通过' if report.passed else '未通过'}",
        f"- 得分：{report.score}",
        f"- 目录：`{report.output_dir}`",
        f"- 文件数：{report.artifacts.get('file_count')}",
        f"- 脱敏状态：{report.artifacts.get('redaction_status')}",
        "",
        "## 检查项",
        "",
    ]
    for check in report.checks:
        lines.append(f"- [{'x' if check.passed else ' '}] `{check.key}`：{check.detail}")
    lines.append("")
    return "\n".join(lines)


def run_frontend_workspace_export(
    *,
    output_dir: str | Path,
    api_base_url: str = os.environ.get("QUALIBUG_API_BASE_URL", "http://127.0.0.1:8088"),
    validate: bool = True,
) -> dict[str, Any]:
    manifest = build_frontend_integration_workspace(output_dir, api_base_url=api_base_url)
    result: dict[str, Any] = {"manifest": manifest}
    if validate:
        report = validate_frontend_integration_workspace(output_dir)
        root = Path(output_dir)
        _write_text(root / "frontend_workspace_acceptance_report.json", json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        _write_text(root / "frontend_workspace_acceptance_report.md", render_validation_markdown(report))
        result["acceptance"] = report.to_dict()
    return result


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate Phase104D frontend integration workspace.")
    parser.add_argument("--output-dir", required=True, help="Directory for generated workspace artifacts.")
    parser.add_argument("--api-base-url", default=os.environ.get("QUALIBUG_API_BASE_URL", "http://127.0.0.1:8088"), help="Local API base URL for generated env/client examples.")
    parser.add_argument("--validate-only", action="store_true", help="Validate an existing workspace without regenerating it.")
    parser.add_argument("--no-validate", action="store_true", help="Skip validation report after generation.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    if args.validate_only:
        report = validate_frontend_integration_workspace(args.output_dir)
        root = Path(args.output_dir)
        _write_text(root / "frontend_workspace_acceptance_report.json", json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        _write_text(root / "frontend_workspace_acceptance_report.md", render_validation_markdown(report))
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if report.passed else 2
    result = run_frontend_workspace_export(
        output_dir=args.output_dir,
        api_base_url=args.api_base_url,
        validate=not args.no_validate,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    acceptance = result.get("acceptance")
    if acceptance and not acceptance.get("passed"):
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

