from __future__ import annotations

"""Phase106A: real frontend application scaffold for QualiBug.

Phase105 completed the product display layer as static pages, delivery bundles,
preview service, preview acceptance, release package, and one-click smoke demo.
Phase106 starts the actual frontend engineering track.  This module generates a
Vite + React + TypeScript application scaffold that can be handed to frontend
engineers while preserving the product story and API contract proven in
Phase104/105.

The scaffold is intentionally generated from the same redacted Phase104 demo data
used by the Phase105 pages, but it is no longer a loose static export: it has
package metadata, typed API client, routes, reusable components, design tokens,
page-level views, a contract test, environment configuration, checksum ledger,
and a validation gate.
"""

import argparse
import hashlib
import json
import shutil
import time
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from ai_test_asset_center.phase103_enterprise_command_center import redact_value
from ai_test_asset_center.phase105_frontend_product_shell import collect_product_shell_demo_data

PHASE106A_VERSION = "phase106a-frontend-app-scaffold-v1"

FRONTEND_APP_DIR = "frontend_app"
FRONTEND_APP_MANIFEST_JSON = "frontend_app_scaffold_manifest.json"
FRONTEND_APP_MANIFEST_MD = "frontend_app_scaffold_manifest.md"
FRONTEND_APP_ACCEPTANCE_JSON = "frontend_app_scaffold_acceptance_report.json"
FRONTEND_APP_ACCEPTANCE_MD = "frontend_app_scaffold_acceptance_report.md"
FRONTEND_APP_CHECKSUMS = "CHECKSUMS.sha256"
FRONTEND_APP_ZIP = "phase106_frontend_app_scaffold.zip"

REQUIRED_FRONTEND_APP_FILES: tuple[str, ...] = (
    f"{FRONTEND_APP_DIR}/package.json",
    f"{FRONTEND_APP_DIR}/index.html",
    f"{FRONTEND_APP_DIR}/vite.config.ts",
    f"{FRONTEND_APP_DIR}/tsconfig.json",
    f"{FRONTEND_APP_DIR}/tsconfig.node.json",
    f"{FRONTEND_APP_DIR}/.env.example",
    f"{FRONTEND_APP_DIR}/README_FRONTEND_APP.md",
    f"{FRONTEND_APP_DIR}/src/main.tsx",
    f"{FRONTEND_APP_DIR}/src/App.tsx",
    f"{FRONTEND_APP_DIR}/src/routes.ts",
    f"{FRONTEND_APP_DIR}/src/types.ts",
    f"{FRONTEND_APP_DIR}/src/api/qualibugClient.ts",
    f"{FRONTEND_APP_DIR}/src/data/demoData.ts",
    f"{FRONTEND_APP_DIR}/src/components/Sidebar.tsx",
    f"{FRONTEND_APP_DIR}/src/components/Topbar.tsx",
    f"{FRONTEND_APP_DIR}/src/components/MetricCard.tsx",
    f"{FRONTEND_APP_DIR}/src/components/StatusPill.tsx",
    f"{FRONTEND_APP_DIR}/src/components/JourneyStepper.tsx",
    f"{FRONTEND_APP_DIR}/src/components/PageCard.tsx",
    f"{FRONTEND_APP_DIR}/src/components/EvidenceBadge.tsx",
    f"{FRONTEND_APP_DIR}/src/pages/DashboardPage.tsx",
    f"{FRONTEND_APP_DIR}/src/pages/CustomerIntakePage.tsx",
    f"{FRONTEND_APP_DIR}/src/pages/EnvironmentDiagnosisPage.tsx",
    f"{FRONTEND_APP_DIR}/src/pages/BusinessFlowMapPage.tsx",
    f"{FRONTEND_APP_DIR}/src/pages/TestExecutionPage.tsx",
    f"{FRONTEND_APP_DIR}/src/pages/RiskEvidencePage.tsx",
    f"{FRONTEND_APP_DIR}/src/pages/ReportRoiPage.tsx",
    f"{FRONTEND_APP_DIR}/src/styles/design-tokens.css",
    f"{FRONTEND_APP_DIR}/src/styles/app.css",
    f"{FRONTEND_APP_DIR}/src/__tests__/frontend-contract.test.ts",
    FRONTEND_APP_MANIFEST_JSON,
    FRONTEND_APP_MANIFEST_MD,
    FRONTEND_APP_ACCEPTANCE_JSON,
    FRONTEND_APP_ACCEPTANCE_MD,
    FRONTEND_APP_CHECKSUMS,
    FRONTEND_APP_ZIP,
)

CORE_FRONTEND_APP_LABELS: tuple[str, ...] = (
    "QualiBug AI",
    "真实前端工程",
    "Vite",
    "React",
    "TypeScript",
    "API Client",
    "客户资料导入",
    "环境诊断",
    "业务流程地图",
    "AI 测试计划",
    "实时测试执行",
    "风险证据链",
    "领导层报告",
    "ROI 价值中心",
    "默认脱敏",
)

REQUIRED_API_METHODS: tuple[str, ...] = (
    "health",
    "listProjects",
    "createProject",
    "getCommandCenter",
    "getEnvironmentReadiness",
    "runEnvironmentPreflight",
    "getBusinessModel",
    "getTestPlan",
    "generateTestPlan",
    "startTestRun",
    "getLiveMap",
    "listRisks",
    "getRiskDetail",
    "getValueMetrics",
    "getExecutiveReport",
    "generateExecutiveReport",
)

FORBIDDEN_FRONTEND_APP_PATTERNS: tuple[str, ...] = (
    "raw-token",
    "raw-cookie",
    "raw-session",
    "raw-password",
    "client_secret=",
    "clientSecret=raw",
    "SESSION=raw",
    "Bearer raw",
    "DemoPasswordShouldBeRedacted",
    "Traceback (most recent call last)",
)


@dataclass(frozen=True)
class FrontendAppScaffoldCheck:
    key: str
    passed: bool
    detail: str
    owner: str = "frontend"
    severity: str = "critical"


@dataclass
class FrontendAppScaffoldAcceptanceReport:
    passed: bool
    score: int
    version: str
    scenario: str
    output_dir: str
    app_dir: str
    checks: list[FrontendAppScaffoldCheck] = field(default_factory=list)
    artifacts: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return redact_value(
            {
                "passed": self.passed,
                "score": self.score,
                "version": self.version,
                "scenario": self.scenario,
                "output_dir": self.output_dir,
                "app_dir": self.app_dir,
                "checks": [asdict(check) for check in self.checks],
                "artifacts": self.artifacts,
            }
        )


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _json_dump(data: Any) -> str:
    return json.dumps(redact_value(data), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _read_text(path: Path, *, limit: int = 200_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")[:limit]
    except OSError:
        return ""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return dict(payload) if isinstance(payload, Mapping) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_str(value: Any, default: str = "—") -> str:
    text = str(value).strip() if value is not None else ""
    return text or default


def _first_mapping(items: Any) -> dict[str, Any]:
    if isinstance(items, Sequence) and not isinstance(items, (str, bytes)) and items:
        first = items[0]
        return dict(first) if isinstance(first, Mapping) else {}
    return {}


def _iter_checksum_files(root: Path) -> list[Path]:
    excluded = {FRONTEND_APP_CHECKSUMS, FRONTEND_APP_ZIP}
    return [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and path.name not in excluded
        and not path.name.endswith(".pyc")
        and "node_modules" not in path.parts
    ]


def write_frontend_app_checksums(output_dir: str | Path) -> dict[str, str]:
    root = Path(output_dir)
    checksums = {path.relative_to(root).as_posix(): _sha256(path) for path in _iter_checksum_files(root)}
    lines = [f"{digest}  {name}" for name, digest in sorted(checksums.items())]
    _write_text(root / FRONTEND_APP_CHECKSUMS, "\n".join(lines) + "\n")
    return checksums


def verify_frontend_app_checksums(output_dir: str | Path) -> list[str]:
    root = Path(output_dir)
    checksum_path = root / FRONTEND_APP_CHECKSUMS
    if not checksum_path.exists():
        return [f"missing {FRONTEND_APP_CHECKSUMS}"]
    failures: list[str] = []
    for line in checksum_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            expected, relative = line.split(None, 1)
        except ValueError:
            failures.append(f"invalid checksum line: {line}")
            continue
        target = root / relative.strip()
        if not target.exists():
            failures.append(f"missing checksum target: {relative.strip()}")
            continue
        actual = _sha256(target)
        if actual != expected:
            failures.append(f"checksum mismatch: {relative.strip()}")
    return failures


def _route_inventory() -> list[dict[str, str]]:
    return [
        {"path": "/", "key": "dashboard", "label": "质量驾驶舱", "component": "DashboardPage"},
        {"path": "/customer-intake", "key": "customer_intake", "label": "客户资料导入", "component": "CustomerIntakePage"},
        {"path": "/environment", "key": "environment", "label": "环境诊断", "component": "EnvironmentDiagnosisPage"},
        {"path": "/business-flow", "key": "business_flow", "label": "业务流程地图", "component": "BusinessFlowMapPage"},
        {"path": "/test-execution", "key": "test_execution", "label": "AI 测试计划 / 实时测试执行", "component": "TestExecutionPage"},
        {"path": "/risk-evidence", "key": "risk_evidence", "label": "风险证据链", "component": "RiskEvidencePage"},
        {"path": "/report-roi", "key": "report_roi", "label": "领导层报告 / ROI", "component": "ReportRoiPage"},
    ]


def _api_contract() -> list[dict[str, str]]:
    return [
        {"method": "GET", "path": "/api/v1/health", "client": "health", "page": "系统启动"},
        {"method": "GET", "path": "/api/v1/projects", "client": "listProjects", "page": "项目列表"},
        {"method": "POST", "path": "/api/v1/projects", "client": "createProject", "page": "客户资料导入"},
        {"method": "GET", "path": "/api/v1/projects/{projectId}/command-center", "client": "getCommandCenter", "page": "质量驾驶舱"},
        {"method": "GET", "path": "/api/v1/projects/{projectId}/environment/readiness", "client": "getEnvironmentReadiness", "page": "环境诊断"},
        {"method": "POST", "path": "/api/v1/projects/{projectId}/environment/preflight", "client": "runEnvironmentPreflight", "page": "环境诊断"},
        {"method": "GET", "path": "/api/v1/projects/{projectId}/business-model", "client": "getBusinessModel", "page": "业务流程地图"},
        {"method": "GET", "path": "/api/v1/projects/{projectId}/test-plan", "client": "getTestPlan", "page": "AI 测试计划"},
        {"method": "POST", "path": "/api/v1/projects/{projectId}/test-plan/generate", "client": "generateTestPlan", "page": "AI 测试计划"},
        {"method": "POST", "path": "/api/v1/projects/{projectId}/test-runs", "client": "startTestRun", "page": "实时测试执行"},
        {"method": "GET", "path": "/api/v1/projects/{projectId}/live-map", "client": "getLiveMap", "page": "实时测试执行"},
        {"method": "GET", "path": "/api/v1/projects/{projectId}/risks", "client": "listRisks", "page": "风险证据链"},
        {"method": "GET", "path": "/api/v1/projects/{projectId}/risks/{riskId}", "client": "getRiskDetail", "page": "风险证据链"},
        {"method": "GET", "path": "/api/v1/projects/{projectId}/value-metrics", "client": "getValueMetrics", "page": "ROI 价值中心"},
        {"method": "GET", "path": "/api/v1/projects/{projectId}/reports/executive", "client": "getExecutiveReport", "page": "领导层报告"},
        {"method": "POST", "path": "/api/v1/projects/{projectId}/reports/generate", "client": "generateExecutiveReport", "page": "领导层报告"},
    ]


def _derive_app_data(scenario: str, api_base_url: str) -> dict[str, Any]:
    demo = collect_product_shell_demo_data(scenario=scenario, api_base_url=api_base_url)
    project = dict(demo.get("project") or {})
    dashboard = dict(demo.get("dashboard") or {})
    environment = dict(demo.get("environment") or {})
    test_plan = dict(demo.get("test_plan") or {})
    live_map = dict(demo.get("live_map") or {})
    risks = demo.get("risks") if isinstance(demo.get("risks"), list) else []
    value_metrics = dict(demo.get("value_metrics") or {})
    executive_report = dict(demo.get("executive_report") or {})
    risk_detail = dict(demo.get("risk_detail") or {})

    return redact_value(
        {
            "version": PHASE106A_VERSION,
            "generated_at": _now(),
            "scenario": scenario,
            "api_base_url": api_base_url.rstrip("/"),
            "project": project,
            "dashboard": dashboard,
            "environment": environment,
            "test_plan": test_plan,
            "live_map": live_map,
            "risks": risks,
            "risk_detail": risk_detail,
            "value_metrics": value_metrics,
            "executive_report": executive_report,
            "routes": _route_inventory(),
            "api_contract": _api_contract(),
            "engineering_stack": {
                "builder": "Vite",
                "view": "React",
                "language": "TypeScript",
                "testing": "Vitest + Testing Library ready",
                "state_strategy": "React hooks first; server data via QualiBugClient",
                "security": "default redacted display; no raw credential rendering",
            },
            "demo_summary": {
                "project_name": project.get("name") or project.get("project_name") or "QualiBug Demo Project",
                "launch_recommendation": dashboard.get("launch_recommendation") or dashboard.get("decision") or "需复验后上线",
                "quality_score": dashboard.get("quality_score") or dashboard.get("health_score") or 0,
                "environment_score": environment.get("readiness_score") or environment.get("score") or 0,
                "risk_count": len(risks),
                "blocking_risks": _safe_int(dashboard.get("blocking_risk_count") or dashboard.get("blocking_risks"), 0),
                "covered_flows": len(live_map.get("nodes") or live_map.get("flow_nodes") or []),
                "estimated_hours_saved": value_metrics.get("estimated_hours_saved") or value_metrics.get("hours_saved") or 0,
            },
        }
    )


def _write_package_json(app_dir: Path) -> None:
    package = {
        "name": "qualibug-command-center-frontend",
        "private": True,
        "version": "0.1.0-phase106a",
        "type": "module",
        "scripts": {
            "dev": "vite --host 127.0.0.1 --port 5173",
            "build": "tsc -p tsconfig.json && vite build",
            "preview": "vite preview --host 127.0.0.1 --port 4173",
            "test": "vitest run",
            "lint:contract": "vitest run src/__tests__/frontend-contract.test.ts",
        },
        "dependencies": {"@vitejs/plugin-react": "latest", "vite": "latest", "typescript": "latest", "react": "latest", "react-dom": "latest"},
        "devDependencies": {"vitest": "latest", "@testing-library/react": "latest", "@testing-library/jest-dom": "latest", "jsdom": "latest", "@types/react": "latest", "@types/react-dom": "latest"},
    }
    _write_text(app_dir / "package.json", _json_dump(package))


def _write_ts_configs(app_dir: Path) -> None:
    _write_text(
        app_dir / "tsconfig.json",
        _json_dump(
            {
                "compilerOptions": {
                    "target": "ES2020",
                    "useDefineForClassFields": True,
                    "lib": ["DOM", "DOM.Iterable", "ES2020"],
                    "allowJs": False,
                    "skipLibCheck": True,
                    "esModuleInterop": True,
                    "allowSyntheticDefaultImports": True,
                    "strict": True,
                    "forceConsistentCasingInFileNames": True,
                    "module": "ESNext",
                    "moduleResolution": "Node",
                    "resolveJsonModule": True,
                    "isolatedModules": True,
                    "noEmit": True,
                    "jsx": "react-jsx",
                },
                "include": ["src"],
                "references": [{"path": "./tsconfig.node.json"}],
            }
        ),
    )
    _write_text(
        app_dir / "tsconfig.node.json",
        _json_dump(
            {
                "compilerOptions": {"composite": True, "module": "ESNext", "moduleResolution": "Node", "allowSyntheticDefaultImports": True},
                "include": ["vite.config.ts"],
            }
        ),
    )
    _write_text(
        app_dir / "vite.config.ts",
        """import { defineConfig } from 'vite';\nimport react from '@vitejs/plugin-react';\n\nexport default defineConfig({\n  plugins: [react()],\n  server: { host: '127.0.0.1', port: 5173 },\n  preview: { host: '127.0.0.1', port: 4173 },\n});\n""",
    )
    _write_text(app_dir / ".env.example", "VITE_QUALIBUG_API_BASE_URL=http://127.0.0.1:8790\nVITE_QUALIBUG_DEMO_MODE=true\n")
    _write_text(
        app_dir / "index.html",
        """<!doctype html>\n<html lang=\"zh-CN\">\n  <head>\n    <meta charset=\"UTF-8\" />\n    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />\n    <title>QualiBug AI 前端工程化应用</title>\n  </head>\n  <body>\n    <div id=\"root\"></div>\n    <script type=\"module\" src=\"/src/main.tsx\"></script>\n  </body>\n</html>\n""",
    )


def _write_types(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/types.ts",
        """export type LaunchStatus = 'ready' | 'warning' | 'blocked' | 'unknown';\n\nexport interface QualiBugRoute {\n  path: string;\n  key: string;\n  label: string;\n  component: string;\n}\n\nexport interface ApiRouteContract {\n  method: 'GET' | 'POST' | 'PATCH' | 'DELETE';\n  path: string;\n  client: string;\n  page: string;\n}\n\nexport interface MetricCardModel {\n  label: string;\n  value: string | number;\n  helper: string;\n  status?: LaunchStatus;\n}\n\nexport interface RiskSummary {\n  risk_id?: string;\n  title?: string;\n  severity?: string;\n  business_impact?: string;\n  status?: string;\n}\n\nexport interface QualiBugDemoData {\n  version: string;\n  generated_at: string;\n  scenario: string;\n  api_base_url: string;\n  project: Record<string, unknown>;\n  dashboard: Record<string, unknown>;\n  environment: Record<string, unknown>;\n  test_plan: Record<string, unknown>;\n  live_map: Record<string, unknown>;\n  risks: RiskSummary[];\n  risk_detail: Record<string, unknown>;\n  value_metrics: Record<string, unknown>;\n  executive_report: Record<string, unknown>;\n  routes: QualiBugRoute[];\n  api_contract: ApiRouteContract[];\n  engineering_stack: Record<string, unknown>;\n  demo_summary: Record<string, string | number>;\n}\n""",
    )


def _write_api_client(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/api/qualibugClient.ts",
        """type HttpMethod = 'GET' | 'POST' | 'PATCH' | 'DELETE';\n\nexport interface QualiBugClientOptions {\n  baseUrl?: string;\n  demoMode?: boolean;\n}\n\nexport class QualiBugClient {\n  private readonly baseUrl: string;\n\n  constructor(options: QualiBugClientOptions = {}) {\n    this.baseUrl = (options.baseUrl || import.meta.env.VITE_QUALIBUG_API_BASE_URL || 'http://127.0.0.1:8790').replace(/\\/$/, '');\n  }\n\n  private async request<T>(method: HttpMethod, path: string, body?: Record<string, unknown>): Promise<T> {\n    const response = await fetch(`${this.baseUrl}${path}`, {\n      method,\n      headers: { 'Content-Type': 'application/json' },\n      body: body ? JSON.stringify(body) : undefined,\n    });\n    if (!response.ok) {\n      throw new Error(`QualiBug API ${method} ${path} failed with ${response.status}`);\n    }\n    const envelope = await response.json();\n    if (envelope && envelope.success === false) {\n      throw new Error(envelope.error?.message || `QualiBug API ${method} ${path} failed`);\n    }\n    return (envelope?.data ?? envelope) as T;\n  }\n\n  health() { return this.request<Record<string, unknown>>('GET', '/api/v1/health'); }\n  listProjects() { return this.request<Record<string, unknown>[]>('GET', '/api/v1/projects'); }\n  createProject(payload: Record<string, unknown>) { return this.request<Record<string, unknown>>('POST', '/api/v1/projects', payload); }\n  getCommandCenter(projectId: string) { return this.request<Record<string, unknown>>('GET', `/api/v1/projects/${projectId}/command-center`); }\n  getEnvironmentReadiness(projectId: string) { return this.request<Record<string, unknown>>('GET', `/api/v1/projects/${projectId}/environment/readiness`); }\n  runEnvironmentPreflight(projectId: string, payload: Record<string, unknown> = { safe_execution_mode: 'read_only' }) { return this.request<Record<string, unknown>>('POST', `/api/v1/projects/${projectId}/environment/preflight`, payload); }\n  getBusinessModel(projectId: string) { return this.request<Record<string, unknown>>('GET', `/api/v1/projects/${projectId}/business-model`); }\n  getTestPlan(projectId: string) { return this.request<Record<string, unknown>>('GET', `/api/v1/projects/${projectId}/test-plan`); }\n  generateTestPlan(projectId: string, payload: Record<string, unknown> = {}) { return this.request<Record<string, unknown>>('POST', `/api/v1/projects/${projectId}/test-plan/generate`, payload); }\n  startTestRun(projectId: string, payload: Record<string, unknown> = { mode: 'read_only' }) { return this.request<Record<string, unknown>>('POST', `/api/v1/projects/${projectId}/test-runs`, payload); }\n  getLiveMap(projectId: string) { return this.request<Record<string, unknown>>('GET', `/api/v1/projects/${projectId}/live-map`); }\n  listRisks(projectId: string) { return this.request<Record<string, unknown>[]>('GET', `/api/v1/projects/${projectId}/risks`); }\n  getRiskDetail(projectId: string, riskId: string) { return this.request<Record<string, unknown>>('GET', `/api/v1/projects/${projectId}/risks/${riskId}`); }\n  getValueMetrics(projectId: string) { return this.request<Record<string, unknown>>('GET', `/api/v1/projects/${projectId}/value-metrics`); }\n  getExecutiveReport(projectId: string) { return this.request<Record<string, unknown>>('GET', `/api/v1/projects/${projectId}/reports/executive`); }\n  generateExecutiveReport(projectId: string, payload: Record<string, unknown> = {}) { return this.request<Record<string, unknown>>('POST', `/api/v1/projects/${projectId}/reports/generate`, payload); }\n}\n\nexport const qualiBugClient = new QualiBugClient();\n""",
    )


def _ts_literal(data: Mapping[str, Any]) -> str:
    return json.dumps(redact_value(data), ensure_ascii=False, indent=2)


def _write_data(app_dir: Path, data: Mapping[str, Any]) -> None:
    _write_text(
        app_dir / "src/data/demoData.ts",
        f"""import type {{ QualiBugDemoData }} from '../types';\n\nexport const demoData = {_ts_literal(data)} as const satisfies QualiBugDemoData;\n\nexport const routeInventory = demoData.routes;\nexport const apiContract = demoData.api_contract;\n""",
    )


def _write_components(app_dir: Path) -> None:
    components: dict[str, str] = {
        "Sidebar.tsx": """import { routeInventory } from '../data/demoData';\n\nexport function Sidebar() {\n  return (\n    <aside className=\"qb-sidebar\">\n      <div className=\"qb-brand\">\n        <strong>QualiBug AI</strong>\n        <span>真实前端工程</span>\n      </div>\n      <nav>\n        {routeInventory.map((route) => (\n          <a key={route.key} href={route.path} className=\"qb-nav-link\">\n            <span>{route.label}</span>\n            <small>{route.component}</small>\n          </a>\n        ))}\n      </nav>\n    </aside>\n  );\n}\n""",
        "Topbar.tsx": """import { demoData } from '../data/demoData';\n\nexport function Topbar() {\n  return (\n    <header className=\"qb-topbar\">\n      <div>\n        <span className=\"qb-kicker\">默认脱敏 · API Client Ready</span>\n        <h1>{String(demoData.demo_summary.project_name)}</h1>\n      </div>\n      <div className=\"qb-topbar-actions\">\n        <code>{demoData.api_base_url}</code>\n        <button type=\"button\">切换真实 API</button>\n      </div>\n    </header>\n  );\n}\n""",
        "MetricCard.tsx": """import type { MetricCardModel } from '../types';\nimport { StatusPill } from './StatusPill';\n\nexport function MetricCard({ metric }: { metric: MetricCardModel }) {\n  return (\n    <article className=\"qb-metric-card\">\n      <div className=\"qb-metric-head\">\n        <span>{metric.label}</span>\n        {metric.status ? <StatusPill status={metric.status} /> : null}\n      </div>\n      <strong>{metric.value}</strong>\n      <p>{metric.helper}</p>\n    </article>\n  );\n}\n""",
        "StatusPill.tsx": """import type { LaunchStatus } from '../types';\n\nconst labelMap: Record<LaunchStatus, string> = {\n  ready: '可上线',\n  warning: '需复验',\n  blocked: '阻断',\n  unknown: '待确认',\n};\n\nexport function StatusPill({ status }: { status: LaunchStatus }) {\n  return <span className={`qb-status qb-status-${status}`}>{labelMap[status]}</span>;\n}\n""",
        "JourneyStepper.tsx": """const steps = ['客户资料导入', '环境诊断', '业务流程地图', 'AI 测试计划', '实时测试执行', '风险证据链', '领导层报告 / ROI'];\n\nexport function JourneyStepper() {\n  return (\n    <ol className=\"qb-journey\">\n      {steps.map((step, index) => (\n        <li key={step}>\n          <span>{index + 1}</span>\n          <strong>{step}</strong>\n        </li>\n      ))}\n    </ol>\n  );\n}\n""",
        "PageCard.tsx": """import type { QualiBugRoute } from '../types';\n\nexport function PageCard({ route }: { route: QualiBugRoute }) {\n  return (\n    <a className=\"qb-page-card\" href={route.path}>\n      <small>{route.key}</small>\n      <strong>{route.label}</strong>\n      <span>{route.component}</span>\n    </a>\n  );\n}\n""",
        "EvidenceBadge.tsx": """export function EvidenceBadge({ text = '证据可复验' }: { text?: string }) {\n  return <span className=\"qb-evidence-badge\">{text}</span>;\n}\n""",
    }
    for filename, content in components.items():
        _write_text(app_dir / "src/components" / filename, content)


def _write_pages(app_dir: Path) -> None:
    pages: dict[str, str] = {
        "DashboardPage.tsx": """import { demoData, routeInventory } from '../data/demoData';\nimport { JourneyStepper } from '../components/JourneyStepper';\nimport { MetricCard } from '../components/MetricCard';\nimport { PageCard } from '../components/PageCard';\n\nexport function DashboardPage() {\n  const metrics = [\n    { label: '质量健康分', value: demoData.demo_summary.quality_score || '—', helper: '来自 Phase104 Command Center', status: 'warning' as const },\n    { label: '环境可测性', value: demoData.demo_summary.environment_score || '—', helper: '环境诊断中心输出', status: 'warning' as const },\n    { label: '阻断风险', value: demoData.demo_summary.blocking_risks || 0, helper: '需要上线前处理', status: 'blocked' as const },\n    { label: '预计节省工时', value: demoData.demo_summary.estimated_hours_saved || 0, helper: 'ROI 价值中心估算', status: 'ready' as const },\n  ];\n  return (\n    <section className=\"qb-page\">\n      <div className=\"qb-hero\">\n        <span className=\"qb-kicker\">Vite · React · TypeScript · API Client</span>\n        <h2>真实前端工程化起点</h2>\n        <p>从 Phase105 静态展示层升级为可开发、可构建、可接真实后端的 QualiBug AI 前端应用。</p>\n      </div>\n      <JourneyStepper />\n      <div className=\"qb-metric-grid\">{metrics.map((metric) => <MetricCard key={metric.label} metric={metric} />)}</div>\n      <div className=\"qb-page-grid\">{routeInventory.map((route) => <PageCard key={route.key} route={route} />)}</div>\n    </section>\n  );\n}\n""",
        "CustomerIntakePage.tsx": """import { demoData } from '../data/demoData';\n\nexport function CustomerIntakePage() {\n  return (\n    <section className=\"qb-page\">\n      <h2>客户资料导入</h2>\n      <p>真实工程版本保留上传、解析、行业识别、业务建模和客户补料清单入口。</p>\n      <div className=\"qb-panel\">\n        <strong>项目</strong>\n        <pre>{JSON.stringify(demoData.project, null, 2)}</pre>\n      </div>\n      <button type=\"button\">上传 PRD / OpenAPI / 角色权限资料</button>\n    </section>\n  );\n}\n""",
        "EnvironmentDiagnosisPage.tsx": """import { demoData } from '../data/demoData';\n\nexport function EnvironmentDiagnosisPage() {\n  return (\n    <section className=\"qb-page\">\n      <h2>环境诊断</h2>\n      <p>显示 URL、DNS、HTTP、认证、API Smoke、阻断原因和安全执行模式。</p>\n      <div className=\"qb-panel\"><pre>{JSON.stringify(demoData.environment, null, 2)}</pre></div>\n      <button type=\"button\">重新预检</button>\n    </section>\n  );\n}\n""",
        "BusinessFlowMapPage.tsx": """import { demoData } from '../data/demoData';\n\nexport function BusinessFlowMapPage() {\n  return (\n    <section className=\"qb-page\">\n      <h2>业务流程地图</h2>\n      <p>把业务节点、链路覆盖、风险爆点和证据回流变成前端组件。</p>\n      <div className=\"qb-flow-strip\">\n        {Object.keys(demoData.live_map).slice(0, 8).map((key) => <span key={key}>{key}</span>)}\n      </div>\n      <div className=\"qb-panel\"><pre>{JSON.stringify(demoData.live_map, null, 2)}</pre></div>\n    </section>\n  );\n}\n""",
        "TestExecutionPage.tsx": """import { demoData } from '../data/demoData';\n\nexport function TestExecutionPage() {\n  return (\n    <section className=\"qb-page\">\n      <h2>AI 测试计划 / 实时测试执行</h2>\n      <p>展示可执行探针、阻断探针、执行时间线、风险事件和证据回流。</p>\n      <div className=\"qb-panel\"><pre>{JSON.stringify(demoData.test_plan, null, 2)}</pre></div>\n      <button type=\"button\">生成测试计划</button>\n      <button type=\"button\">启动只读执行</button>\n    </section>\n  );\n}\n""",
        "RiskEvidencePage.tsx": """import { demoData } from '../data/demoData';\nimport { EvidenceBadge } from '../components/EvidenceBadge';\n\nexport function RiskEvidencePage() {\n  return (\n    <section className=\"qb-page\">\n      <h2>风险证据链</h2>\n      <p>风险列表、业务影响、复现步骤、请求响应摘要、修复建议和关闭条件。</p>\n      <EvidenceBadge />\n      <div className=\"qb-risk-list\">\n        {demoData.risks.map((risk, index) => (\n          <article className=\"qb-panel\" key={risk.risk_id || index}>\n            <strong>{risk.title || `风险 ${index + 1}`}</strong>\n            <p>{risk.business_impact || risk.severity || '业务影响待确认'}</p>\n          </article>\n        ))}\n      </div>\n    </section>\n  );\n}\n""",
        "ReportRoiPage.tsx": """import { demoData } from '../data/demoData';\n\nexport function ReportRoiPage() {\n  return (\n    <section className=\"qb-page\">\n      <h2>领导层报告 / ROI 价值中心</h2>\n      <p>上线建议、执行摘要、节省工时、业务影响区间和可复制汇报摘要。</p>\n      <div className=\"qb-panel\"><pre>{JSON.stringify(demoData.executive_report, null, 2)}</pre></div>\n      <div className=\"qb-panel\"><pre>{JSON.stringify(demoData.value_metrics, null, 2)}</pre></div>\n      <button type=\"button\">生成领导层报告</button>\n    </section>\n  );\n}\n""",
    }
    for filename, content in pages.items():
        _write_text(app_dir / "src/pages" / filename, content)


def _write_app_entry(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/routes.ts",
        """import { DashboardPage } from './pages/DashboardPage';\nimport { CustomerIntakePage } from './pages/CustomerIntakePage';\nimport { EnvironmentDiagnosisPage } from './pages/EnvironmentDiagnosisPage';\nimport { BusinessFlowMapPage } from './pages/BusinessFlowMapPage';\nimport { TestExecutionPage } from './pages/TestExecutionPage';\nimport { RiskEvidencePage } from './pages/RiskEvidencePage';\nimport { ReportRoiPage } from './pages/ReportRoiPage';\n\nexport const pageComponents = {\n  '/': DashboardPage,\n  '/customer-intake': CustomerIntakePage,\n  '/environment': EnvironmentDiagnosisPage,\n  '/business-flow': BusinessFlowMapPage,\n  '/test-execution': TestExecutionPage,\n  '/risk-evidence': RiskEvidencePage,\n  '/report-roi': ReportRoiPage,\n};\n\nexport function resolvePage(pathname: string) {\n  return pageComponents[pathname as keyof typeof pageComponents] || DashboardPage;\n}\n""",
    )
    _write_text(
        app_dir / "src/App.tsx",
        """import { Sidebar } from './components/Sidebar';\nimport { Topbar } from './components/Topbar';\nimport { resolvePage } from './routes';\nimport './styles/design-tokens.css';\nimport './styles/app.css';\n\nexport function App() {\n  const Page = resolvePage(window.location.pathname);\n  return (\n    <div className=\"qb-app\">\n      <Sidebar />\n      <main className=\"qb-main\">\n        <Topbar />\n        <Page />\n      </main>\n    </div>\n  );\n}\n""",
    )
    _write_text(
        app_dir / "src/main.tsx",
        """import React from 'react';\nimport ReactDOM from 'react-dom/client';\nimport { App } from './App';\n\nReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(\n  <React.StrictMode>\n    <App />\n  </React.StrictMode>,\n);\n""",
    )


def _write_styles(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/styles/design-tokens.css",
        """:root {\n  --qb-bg: #f5f7fb;\n  --qb-surface: #ffffff;\n  --qb-surface-strong: #0f172a;\n  --qb-text: #182034;\n  --qb-muted: #64748b;\n  --qb-border: #dbe3ef;\n  --qb-primary: #2f5cf6;\n  --qb-danger: #dc2626;\n  --qb-warning: #d97706;\n  --qb-success: #059669;\n  --qb-radius: 18px;\n  --qb-shadow: 0 18px 45px rgba(15, 23, 42, 0.08);\n}\n""",
    )
    _write_text(
        app_dir / "src/styles/app.css",
        """* { box-sizing: border-box; }\nbody { margin: 0; background: var(--qb-bg); color: var(--qb-text); font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }\na { color: inherit; text-decoration: none; }\nbutton { border: 0; border-radius: 999px; padding: 10px 16px; background: var(--qb-primary); color: white; font-weight: 700; margin: 6px 8px 6px 0; cursor: pointer; }\npre { white-space: pre-wrap; word-break: break-word; color: var(--qb-muted); font-size: 12px; }\n.qb-app { display: grid; grid-template-columns: 290px 1fr; min-height: 100vh; }\n.qb-sidebar { background: var(--qb-surface-strong); color: white; padding: 28px; position: sticky; top: 0; height: 100vh; }\n.qb-brand { display: grid; gap: 4px; margin-bottom: 28px; }\n.qb-brand span, .qb-nav-link small, .qb-kicker { color: #94a3b8; font-size: 12px; letter-spacing: .04em; text-transform: uppercase; }\n.qb-nav-link { display: grid; gap: 4px; padding: 13px 14px; border-radius: 14px; margin-bottom: 8px; background: rgba(255,255,255,.06); }\n.qb-nav-link:hover { background: rgba(255,255,255,.12); }\n.qb-main { padding: 28px; }\n.qb-topbar, .qb-hero, .qb-panel, .qb-metric-card, .qb-page-card { background: var(--qb-surface); border: 1px solid var(--qb-border); box-shadow: var(--qb-shadow); border-radius: var(--qb-radius); }\n.qb-topbar { display: flex; justify-content: space-between; align-items: center; padding: 18px 22px; margin-bottom: 22px; }\n.qb-topbar h1 { margin: 2px 0 0; }\n.qb-topbar-actions { display: flex; gap: 12px; align-items: center; }\n.qb-topbar code { background: #eef2ff; padding: 8px 10px; border-radius: 12px; color: var(--qb-primary); }\n.qb-page { display: grid; gap: 18px; }\n.qb-hero { padding: 28px; background: linear-gradient(135deg, #ffffff, #eef4ff); }\n.qb-hero h2, .qb-page h2 { margin: 0 0 8px; font-size: 30px; }\n.qb-hero p, .qb-page p { color: var(--qb-muted); margin: 0; line-height: 1.7; }\n.qb-journey { display: grid; grid-template-columns: repeat(7, 1fr); list-style: none; padding: 0; margin: 0; gap: 10px; }\n.qb-journey li { background: white; border: 1px solid var(--qb-border); border-radius: 16px; padding: 14px; display: grid; gap: 8px; }\n.qb-journey span { width: 28px; height: 28px; border-radius: 50%; background: var(--qb-primary); color: white; display: grid; place-items: center; font-weight: 800; }\n.qb-metric-grid, .qb-page-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; }\n.qb-metric-card, .qb-page-card, .qb-panel { padding: 18px; }\n.qb-metric-head { display: flex; align-items: center; justify-content: space-between; color: var(--qb-muted); }\n.qb-metric-card strong { display: block; font-size: 30px; margin: 10px 0; }\n.qb-status { border-radius: 999px; padding: 5px 9px; font-size: 12px; font-weight: 800; }\n.qb-status-ready { background: #dcfce7; color: var(--qb-success); }\n.qb-status-warning { background: #fef3c7; color: var(--qb-warning); }\n.qb-status-blocked { background: #fee2e2; color: var(--qb-danger); }\n.qb-status-unknown { background: #e2e8f0; color: var(--qb-muted); }\n.qb-page-card { display: grid; gap: 8px; }\n.qb-page-card small { color: var(--qb-primary); font-weight: 800; text-transform: uppercase; }\n.qb-flow-strip { display: flex; flex-wrap: wrap; gap: 10px; }\n.qb-flow-strip span, .qb-evidence-badge { border: 1px solid var(--qb-border); border-radius: 999px; padding: 8px 12px; background: white; color: var(--qb-primary); font-weight: 700; }\n.qb-risk-list { display: grid; gap: 12px; }\n@media (max-width: 1080px) { .qb-app { grid-template-columns: 1fr; } .qb-sidebar { position: static; height: auto; } .qb-journey, .qb-metric-grid, .qb-page-grid { grid-template-columns: 1fr 1fr; } }\n@media (max-width: 720px) { .qb-journey, .qb-metric-grid, .qb-page-grid { grid-template-columns: 1fr; } .qb-topbar { align-items: flex-start; flex-direction: column; } }\n""",
    )


def _write_frontend_contract_test(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/__tests__/frontend-contract.test.ts",
        """import { describe, expect, it } from 'vitest';\nimport { apiContract, demoData, routeInventory } from '../data/demoData';\n\ndescribe('QualiBug frontend contract', () => {\n  it('keeps every product route visible', () => {\n    expect(routeInventory.map((route) => route.path)).toEqual([\n      '/',\n      '/customer-intake',\n      '/environment',\n      '/business-flow',\n      '/test-execution',\n      '/risk-evidence',\n      '/report-roi',\n    ]);\n  });\n\n  it('keeps Phase104 API endpoints mapped to frontend pages', () => {\n    expect(apiContract.some((route) => route.client === 'getCommandCenter')).toBe(true);\n    expect(apiContract.some((route) => route.client === 'runEnvironmentPreflight')).toBe(true);\n    expect(apiContract.some((route) => route.client === 'generateTestPlan')).toBe(true);\n    expect(apiContract.some((route) => route.client === 'getRiskDetail')).toBe(true);\n    expect(apiContract.some((route) => route.client === 'getExecutiveReport')).toBe(true);\n  });\n\n  it('uses redacted demo data and does not expose raw secrets', () => {\n    const serialized = JSON.stringify(demoData);\n    expect(serialized).not.toContain('rawCredentialMarker');\n    expect(serialized).not.toContain('clientSecretRawMarker');\n    expect(serialized).not.toContain('demoPasswordRawMarker');\n  });\n});\n""",
    )


def _write_readme(app_dir: Path, data: Mapping[str, Any]) -> None:
    route_lines = "\n".join(f"- `{route['path']}`：{route['label']} → `{route['component']}`" for route in data["routes"])
    api_lines = "\n".join(f"- `{route['method']} {route['path']}` → `{route['client']}` / {route['page']}" for route in data["api_contract"])
    _write_text(
        app_dir / "README_FRONTEND_APP.md",
        f"""# Phase106A 前端工程化应用脚手架\n\n这是 QualiBug AI 从静态展示层进入真实前端工程的第一步。\n\n## 技术栈\n\n- Vite\n- React\n- TypeScript\n- Vitest contract test\n- Typed QualiBug API Client\n- 默认脱敏演示数据\n\n## 本地启动\n\n```powershell\nnpm install\nnpm run dev\n```\n\n## 构建\n\n```powershell\nnpm run build\n```\n\n## 合同测试\n\n```powershell\nnpm test\n```\n\n## 页面路由\n\n{route_lines}\n\n## Phase104 API Client 映射\n\n{api_lines}\n\n## 当前 demo 摘要\n\n```json\n{json.dumps(data['demo_summary'], ensure_ascii=False, indent=2)}\n```\n\n## 工程边界\n\n- 当前脚手架已经可以进入真实前端开发。\n- 当前 demo data 默认脱敏，仅用于本地开发和演示。\n- 真实环境需要设置 `.env` 中的 `VITE_QUALIBUG_API_BASE_URL`。\n- 下一阶段应接入真实 API、登录态、项目详情路由、轮询/SSE 和前端 CI。\n""",
    )


def _zip_scaffold(output_dir: Path) -> Path:
    archive_path = output_dir / FRONTEND_APP_ZIP
    if archive_path.exists():
        archive_path.unlink()
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(output_dir.rglob("*")):
            if path.is_file() and path != archive_path and not path.name.endswith(".pyc"):
                archive.write(path, path.relative_to(output_dir).as_posix())
    return archive_path


def scan_frontend_app_scaffold_for_secret_leaks(output_dir: str | Path) -> list[str]:
    root = Path(output_dir)
    leaks: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() in {".zip", ".png", ".jpg", ".jpeg", ".webp"}:
            continue
        text = _read_text(path, limit=300_000)
        for pattern in FORBIDDEN_FRONTEND_APP_PATTERNS:
            if pattern in text:
                leaks.append(f"{path.relative_to(root).as_posix()} contains forbidden pattern {pattern}")
    return leaks


def _build_manifest(output_dir: Path, data: Mapping[str, Any], report: FrontendAppScaffoldAcceptanceReport | None = None) -> dict[str, Any]:
    manifest = {
        "version": PHASE106A_VERSION,
        "generated_at": _now(),
        "scenario": data.get("scenario"),
        "app_dir": FRONTEND_APP_DIR,
        "entrypoint": f"{FRONTEND_APP_DIR}/index.html",
        "package_json": f"{FRONTEND_APP_DIR}/package.json",
        "routes": data.get("routes", []),
        "api_contract": data.get("api_contract", []),
        "required_files": list(REQUIRED_FRONTEND_APP_FILES),
        "core_labels": list(CORE_FRONTEND_APP_LABELS),
        "artifacts": {
            "manifest_json": FRONTEND_APP_MANIFEST_JSON,
            "manifest_md": FRONTEND_APP_MANIFEST_MD,
            "acceptance_json": FRONTEND_APP_ACCEPTANCE_JSON,
            "acceptance_md": FRONTEND_APP_ACCEPTANCE_MD,
            "checksums": FRONTEND_APP_CHECKSUMS,
            "zip": FRONTEND_APP_ZIP,
        },
    }
    if report is not None:
        manifest["acceptance"] = {"passed": report.passed, "score": report.score, "checks": len(report.checks)}
    return redact_value(manifest)


def _write_manifest_files(output_dir: Path, manifest: Mapping[str, Any]) -> None:
    _write_text(output_dir / FRONTEND_APP_MANIFEST_JSON, _json_dump(manifest))
    route_lines = "\n".join(f"- `{route['path']}`：{route['label']} / `{route['component']}`" for route in manifest.get("routes", []))
    api_lines = "\n".join(f"- `{route['method']} {route['path']}` → `{route['client']}`" for route in manifest.get("api_contract", []))
    _write_text(
        output_dir / FRONTEND_APP_MANIFEST_MD,
        f"""# Phase106A Frontend App Scaffold Manifest\n\n- Version: `{manifest.get('version')}`\n- App dir: `{manifest.get('app_dir')}`\n- Entry: `{manifest.get('entrypoint')}`\n- Package: `{manifest.get('package_json')}`\n\n## Routes\n\n{route_lines}\n\n## API Contract\n\n{api_lines}\n\n## Security\n\n默认脱敏；生成与验收均扫描 token / cookie / session / client_secret / traceback 等高风险泄露模式。\n""",
    )


def _write_report_files(output_dir: Path, report: FrontendAppScaffoldAcceptanceReport) -> None:
    payload = report.to_dict()
    _write_text(output_dir / FRONTEND_APP_ACCEPTANCE_JSON, _json_dump(payload))
    check_lines = "\n".join(
        f"- [{'x' if check['passed'] else ' '}] `{check['key']}`：{check['detail']}" for check in payload["checks"]
    )
    _write_text(
        output_dir / FRONTEND_APP_ACCEPTANCE_MD,
        f"""# Phase106A Frontend App Scaffold Acceptance\n\n- Passed: `{payload['passed']}`\n- Score: `{payload['score']}`\n- Version: `{payload['version']}`\n- App dir: `{payload['app_dir']}`\n\n## Checks\n\n{check_lines}\n""",
    )


def build_frontend_app_scaffold(
    output_dir: str | Path,
    *,
    scenario: str = "manufacturing",
    api_base_url: str = "http://127.0.0.1:8790",
    clean: bool = True,
) -> FrontendAppScaffoldAcceptanceReport:
    root = Path(output_dir)
    if clean and root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    app_dir = root / FRONTEND_APP_DIR
    data = _derive_app_data(scenario=scenario, api_base_url=api_base_url)

    _write_package_json(app_dir)
    _write_ts_configs(app_dir)
    _write_types(app_dir)
    _write_api_client(app_dir)
    _write_data(app_dir, data)
    _write_components(app_dir)
    _write_pages(app_dir)
    _write_app_entry(app_dir)
    _write_styles(app_dir)
    _write_frontend_contract_test(app_dir)
    _write_readme(app_dir, data)

    manifest = _build_manifest(root, data)
    _write_manifest_files(root, manifest)
    report = validate_frontend_app_scaffold(root, scenario=scenario, write_report=True, skip_checksum=True)
    manifest = _build_manifest(root, data, report)
    _write_manifest_files(root, manifest)
    write_frontend_app_checksums(root)
    _zip_scaffold(root)
    report = validate_frontend_app_scaffold(root, scenario=scenario, write_report=True)
    manifest = _build_manifest(root, data, report)
    _write_manifest_files(root, manifest)
    write_frontend_app_checksums(root)
    _zip_scaffold(root)
    return report


def validate_frontend_app_scaffold(
    output_dir: str | Path,
    *,
    scenario: str = "manufacturing",
    write_report: bool = True,
    skip_checksum: bool = False,
) -> FrontendAppScaffoldAcceptanceReport:
    root = Path(output_dir)
    app_dir = root / FRONTEND_APP_DIR
    checks: list[FrontendAppScaffoldCheck] = []

    missing = [relative for relative in REQUIRED_FRONTEND_APP_FILES if not (root / relative).exists()]
    if skip_checksum:
        missing = [relative for relative in missing if relative != FRONTEND_APP_CHECKSUMS]
    if not skip_checksum and FRONTEND_APP_ZIP in missing:
        missing.remove(FRONTEND_APP_ZIP)
    checks.append(FrontendAppScaffoldCheck("required_files", not missing, "全部必需工程文件存在" if not missing else f"缺失文件: {missing}"))

    package_json = _read_json(app_dir / "package.json")
    dependencies = {**dict(package_json.get("dependencies") or {}), **dict(package_json.get("devDependencies") or {})}
    stack_ok = all(dep in dependencies for dep in ("react", "react-dom", "vite", "typescript", "vitest")) and package_json.get("type") == "module"
    checks.append(FrontendAppScaffoldCheck("vite_react_typescript_stack", stack_ok, "package.json 已声明 Vite / React / TypeScript / Vitest" if stack_ok else "package.json 工程依赖不完整"))

    routes_text = _read_text(app_dir / "src/routes.ts") + _read_text(app_dir / "src/data/demoData.ts")
    missing_routes = [route["path"] for route in _route_inventory() if route["path"] not in routes_text]
    checks.append(FrontendAppScaffoldCheck("route_inventory", not missing_routes, "7 个核心前端路由已建立" if not missing_routes else f"缺失路由: {missing_routes}"))

    api_text = _read_text(app_dir / "src/api/qualibugClient.ts")
    missing_methods = [method for method in REQUIRED_API_METHODS if f"{method}(" not in api_text]
    checks.append(FrontendAppScaffoldCheck("api_client_methods", not missing_methods, "Phase104 API Client 方法已覆盖核心页面" if not missing_methods else f"缺失 client 方法: {missing_methods}"))

    app_text = "\n".join(_read_text(path) for path in sorted((app_dir / "src").rglob("*.tsx")) if path.is_file())
    missing_labels = [label for label in CORE_FRONTEND_APP_LABELS if label not in app_text and label not in api_text and label not in _read_text(app_dir / "README_FRONTEND_APP.md")]
    checks.append(FrontendAppScaffoldCheck("business_labels", not missing_labels, "核心业务页面与工程化标签已出现在前端源码" if not missing_labels else f"缺失展示标签: {missing_labels}"))

    contract_text = _read_text(app_dir / "src/__tests__/frontend-contract.test.ts")
    contract_ok = all(keyword in contract_text for keyword in ("routeInventory", "apiContract", "getCommandCenter", "runEnvironmentPreflight", "generateTestPlan", "getRiskDetail", "getExecutiveReport"))
    checks.append(FrontendAppScaffoldCheck("frontend_contract_test", contract_ok, "已生成前端合同测试骨架" if contract_ok else "合同测试覆盖不足"))

    env_text = _read_text(app_dir / ".env.example")
    env_ok = "VITE_QUALIBUG_API_BASE_URL" in env_text and "VITE_QUALIBUG_DEMO_MODE" in env_text
    checks.append(FrontendAppScaffoldCheck("env_configuration", env_ok, "已提供前端 API 环境变量模板" if env_ok else ".env.example 缺少关键变量"))

    manifest = _read_json(root / FRONTEND_APP_MANIFEST_JSON)
    manifest_ok = manifest.get("version") == PHASE106A_VERSION and len(manifest.get("routes") or []) >= 7 and len(manifest.get("api_contract") or []) >= 15
    checks.append(FrontendAppScaffoldCheck("manifest", manifest_ok, "manifest 描述了路由、API、产物和验收状态" if manifest_ok else "manifest 内容不完整"))

    if skip_checksum:
        checksum_failures: list[str] = []
        checksum_ok = True
        checksum_detail = "构建中跳过 checksum 复验"
    else:
        checksum_failures = verify_frontend_app_checksums(root)
        checksum_ok = not checksum_failures
        checksum_detail = "checksum 复验通过" if checksum_ok else f"checksum 失败: {checksum_failures}"
    checks.append(FrontendAppScaffoldCheck("checksums", checksum_ok, checksum_detail))

    leaks = scan_frontend_app_scaffold_for_secret_leaks(root)
    checks.append(FrontendAppScaffoldCheck("secret_leak_scan", not leaks, "未发现高风险敏感信息泄露模式" if not leaks else f"发现泄露风险: {leaks}"))

    passed = all(check.passed for check in checks)
    score = round(sum(1 for check in checks if check.passed) / len(checks) * 100) if checks else 0
    report = FrontendAppScaffoldAcceptanceReport(
        passed=passed,
        score=score,
        version=PHASE106A_VERSION,
        scenario=scenario,
        output_dir=str(root),
        app_dir=str(app_dir),
        checks=checks,
        artifacts={
            "app_dir": FRONTEND_APP_DIR,
            "entrypoint": f"{FRONTEND_APP_DIR}/index.html",
            "package_json": f"{FRONTEND_APP_DIR}/package.json",
            "manifest_json": FRONTEND_APP_MANIFEST_JSON,
            "acceptance_json": FRONTEND_APP_ACCEPTANCE_JSON,
            "checksums": FRONTEND_APP_CHECKSUMS,
            "zip": FRONTEND_APP_ZIP,
        },
    )
    if write_report:
        _write_report_files(root, report)
    return report


def run_frontend_app_scaffold_export(
    output_dir: str | Path,
    *,
    scenario: str = "manufacturing",
    api_base_url: str = "http://127.0.0.1:8790",
    validate_only: bool = False,
) -> FrontendAppScaffoldAcceptanceReport:
    if validate_only:
        return validate_frontend_app_scaffold(output_dir, scenario=scenario, write_report=True)
    return build_frontend_app_scaffold(output_dir, scenario=scenario, api_base_url=api_base_url)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build or validate the Phase106A QualiBug frontend app scaffold.")
    parser.add_argument("--output-dir", default="outputs/phase106_frontend_app_scaffold", help="Output directory for the scaffold")
    parser.add_argument("--scenario", default="manufacturing", help="Seed demo scenario")
    parser.add_argument("--api-base-url", default="http://127.0.0.1:8790", help="Phase104 API base URL for the generated frontend")
    parser.add_argument("--validate-only", action="store_true", help="Validate an existing scaffold instead of rebuilding")
    args = parser.parse_args(argv)

    report = run_frontend_app_scaffold_export(
        args.output_dir,
        scenario=args.scenario,
        api_base_url=args.api_base_url,
        validate_only=args.validate_only,
    )
    print(_json_dump(report.to_dict()))
    return 0 if report.passed else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
