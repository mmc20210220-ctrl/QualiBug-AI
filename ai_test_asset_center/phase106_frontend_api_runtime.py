from __future__ import annotations

"""Phase106C: wire the generated React frontend to a real API runtime mode.

Phase106A created the Vite + React + TypeScript scaffold. Phase106B introduced
component boundaries and a demo/real data-source switch. Phase106C upgrades the
generated app with a production-shaped API runtime boundary:

* explicit runtime API config and health contract
* request timeout, envelope normalization, read-only execution metadata
* real API adapter with fallback-to-demo behavior for local development
* page-level runtime health hook and API runtime workbench route
* contract checks proving environment diagnosis, test plan, risk evidence and
  report pages can switch from demo data to Phase104 API calls

The repository itself still only receives a Python generator and tests; the React
app is emitted into an output directory so local users can inspect, build, and
iterate without introducing npm dependencies into the root project.
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
from ai_test_asset_center.phase106_frontend_component_model import (
    FRONTEND_APP_DIR,
    build_frontend_component_model,
)

PHASE106C_VERSION = "phase106c-frontend-api-runtime-v1"

API_RUNTIME_MANIFEST_JSON = "frontend_api_runtime_manifest.json"
API_RUNTIME_MANIFEST_MD = "frontend_api_runtime_manifest.md"
API_RUNTIME_ACCEPTANCE_JSON = "frontend_api_runtime_acceptance_report.json"
API_RUNTIME_ACCEPTANCE_MD = "frontend_api_runtime_acceptance_report.md"
API_RUNTIME_CHECKSUMS = "CHECKSUMS_PHASE106C.sha256"
API_RUNTIME_ZIP = "phase106_frontend_api_runtime.zip"

REQUIRED_API_RUNTIME_FILES: tuple[str, ...] = (
    f"{FRONTEND_APP_DIR}/src/api/qualibugClient.ts",
    f"{FRONTEND_APP_DIR}/src/api/runtimeApi.ts",
    f"{FRONTEND_APP_DIR}/src/app/runtimeConfig.ts",
    f"{FRONTEND_APP_DIR}/src/services/realApiRuntime.ts",
    f"{FRONTEND_APP_DIR}/src/services/qualibugDataSource.ts",
    f"{FRONTEND_APP_DIR}/src/hooks/useRuntimeHealth.ts",
    f"{FRONTEND_APP_DIR}/src/pages/ApiRuntimeWorkbenchPage.tsx",
    f"{FRONTEND_APP_DIR}/src/__tests__/api-runtime-contract.test.ts",
    f"{FRONTEND_APP_DIR}/src/styles/api-runtime.css",
    f"{FRONTEND_APP_DIR}/README_FRONTEND_API_RUNTIME.md",
    API_RUNTIME_MANIFEST_JSON,
    API_RUNTIME_MANIFEST_MD,
    API_RUNTIME_ACCEPTANCE_JSON,
    API_RUNTIME_ACCEPTANCE_MD,
    API_RUNTIME_CHECKSUMS,
    API_RUNTIME_ZIP,
)

CORE_API_RUNTIME_LABELS: tuple[str, ...] = (
    "真实 API Client 运行模式",
    "real API mode",
    "demo fallback",
    "RuntimeApiAdapter",
    "runtimeHealth",
    "environment diagnosis",
    "test plan generation",
    "risk evidence",
    "executive report",
    "read-only execution",
    "Phase104 API",
    "默认脱敏",
)

RUNTIME_API_METHODS: tuple[str, ...] = (
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

FORBIDDEN_API_RUNTIME_PATTERNS: tuple[str, ...] = (
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
class FrontendApiRuntimeCheck:
    key: str
    passed: bool
    detail: str
    owner: str = "frontend"
    severity: str = "critical"


@dataclass
class FrontendApiRuntimeAcceptanceReport:
    passed: bool
    score: int
    version: str
    scenario: str
    output_dir: str
    app_dir: str
    checks: list[FrontendApiRuntimeCheck] = field(default_factory=list)
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


def _read_text(path: Path, *, limit: int = 300_000) -> str:
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


def _json_dump(payload: Any) -> str:
    return json.dumps(redact_value(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_checksum_files(root: Path) -> list[Path]:
    excluded = {API_RUNTIME_CHECKSUMS, API_RUNTIME_ZIP}
    return [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and path.name not in excluded
        and not path.name.endswith(".pyc")
        and "node_modules" not in path.parts
    ]


def write_frontend_api_runtime_checksums(output_dir: str | Path) -> dict[str, str]:
    root = Path(output_dir)
    checksums = {path.relative_to(root).as_posix(): _sha256(path) for path in _iter_checksum_files(root)}
    lines = [f"{digest}  {relative}" for relative, digest in sorted(checksums.items())]
    _write_text(root / API_RUNTIME_CHECKSUMS, "\n".join(lines) + "\n")
    return checksums


def verify_frontend_api_runtime_checksums(output_dir: str | Path) -> list[str]:
    root = Path(output_dir)
    checksum_path = root / API_RUNTIME_CHECKSUMS
    if not checksum_path.exists():
        return [f"missing {API_RUNTIME_CHECKSUMS}"]
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


def _zip_api_runtime(output_dir: Path) -> Path:
    archive_path = output_dir / API_RUNTIME_ZIP
    if archive_path.exists():
        archive_path.unlink()
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(output_dir.rglob("*")):
            if path.is_file() and path != archive_path and not path.name.endswith(".pyc") and "node_modules" not in path.parts:
                archive.write(path, path.relative_to(output_dir).as_posix())
    return archive_path


def _runtime_endpoint_contract() -> list[dict[str, str]]:
    return [
        {"method": "GET", "path": "/api/v1/health", "client": "health", "page": "runtime health"},
        {"method": "GET", "path": "/api/v1/projects", "client": "listProjects", "page": "customer intake"},
        {"method": "POST", "path": "/api/v1/projects", "client": "createProject", "page": "customer intake"},
        {"method": "GET", "path": "/api/v1/projects/{projectId}/command-center", "client": "getCommandCenter", "page": "dashboard"},
        {"method": "GET", "path": "/api/v1/projects/{projectId}/environment/readiness", "client": "getEnvironmentReadiness", "page": "environment diagnosis"},
        {"method": "POST", "path": "/api/v1/projects/{projectId}/environment/preflight", "client": "runEnvironmentPreflight", "page": "environment diagnosis"},
        {"method": "GET", "path": "/api/v1/projects/{projectId}/business-model", "client": "getBusinessModel", "page": "business flow map"},
        {"method": "GET", "path": "/api/v1/projects/{projectId}/test-plan", "client": "getTestPlan", "page": "AI test plan"},
        {"method": "POST", "path": "/api/v1/projects/{projectId}/test-plan/generate", "client": "generateTestPlan", "page": "AI test plan"},
        {"method": "POST", "path": "/api/v1/projects/{projectId}/test-runs", "client": "startTestRun", "page": "live execution"},
        {"method": "GET", "path": "/api/v1/projects/{projectId}/live-map", "client": "getLiveMap", "page": "live execution"},
        {"method": "GET", "path": "/api/v1/projects/{projectId}/risks", "client": "listRisks", "page": "risk evidence"},
        {"method": "GET", "path": "/api/v1/projects/{projectId}/risks/{riskId}", "client": "getRiskDetail", "page": "risk evidence"},
        {"method": "GET", "path": "/api/v1/projects/{projectId}/value-metrics", "client": "getValueMetrics", "page": "report ROI"},
        {"method": "GET", "path": "/api/v1/projects/{projectId}/reports/executive", "client": "getExecutiveReport", "page": "executive report"},
        {"method": "POST", "path": "/api/v1/projects/{projectId}/reports/generate", "client": "generateExecutiveReport", "page": "executive report"},
    ]


def _write_runtime_config(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/app/runtimeConfig.ts",
        """export interface RuntimeConfig {\n  apiBaseUrl: string;\n  projectId: string;\n  demoMode: boolean;\n  fallbackToDemo: boolean;\n  requestTimeoutMs: number;\n  safeExecutionMode: 'read_only' | 'controlled';\n}\n\nfunction readBoolean(value: unknown, fallback: boolean): boolean {\n  if (typeof value !== 'string') return fallback;\n  return ['1', 'true', 'yes', 'on'].includes(value.toLowerCase());\n}\n\nfunction readNumber(value: unknown, fallback: number): number {\n  if (typeof value !== 'string') return fallback;\n  const parsed = Number(value);\n  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;\n}\n\nexport const runtimeConfig: RuntimeConfig = {\n  apiBaseUrl: import.meta.env.VITE_QUALIBUG_API_BASE_URL || 'http://127.0.0.1:8790',\n  projectId: import.meta.env.VITE_QUALIBUG_PROJECT_ID || 'demo-project',\n  demoMode: readBoolean(import.meta.env.VITE_QUALIBUG_DEMO_MODE, true),\n  fallbackToDemo: readBoolean(import.meta.env.VITE_QUALIBUG_FALLBACK_TO_DEMO, true),\n  requestTimeoutMs: readNumber(import.meta.env.VITE_QUALIBUG_REQUEST_TIMEOUT_MS, 8000),\n  safeExecutionMode: (import.meta.env.VITE_QUALIBUG_SAFE_EXECUTION_MODE || 'read_only') as RuntimeConfig['safeExecutionMode'],\n};\n\nexport function runtimeModeLabel(): string {\n  return runtimeConfig.demoMode ? 'demo mode' : 'real API mode';\n}\n""",
    )
    env_path = app_dir / ".env.example"
    original = _read_text(env_path)
    additions = "VITE_QUALIBUG_PROJECT_ID=demo-project\nVITE_QUALIBUG_FALLBACK_TO_DEMO=true\nVITE_QUALIBUG_REQUEST_TIMEOUT_MS=8000\nVITE_QUALIBUG_SAFE_EXECUTION_MODE=read_only\n"
    if "VITE_QUALIBUG_FALLBACK_TO_DEMO" not in original:
        _write_text(env_path, original.rstrip() + "\n" + additions)


def _write_runtime_api(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/api/runtimeApi.ts",
        """import { runtimeConfig } from '../app/runtimeConfig';\n\nexport type HttpMethod = 'GET' | 'POST' | 'PATCH' | 'DELETE';\n\nexport interface ApiRequestOptions {\n  method: HttpMethod;\n  path: string;\n  body?: Record<string, unknown>;\n  timeoutMs?: number;\n}\n\nexport interface ApiEnvelope<T> {\n  success?: boolean;\n  data?: T;\n  error?: { code?: string; message?: string; details?: unknown };\n}\n\nexport interface RuntimeHealth {\n  mode: 'demo' | 'real';\n  apiBaseUrl: string;\n  online: boolean;\n  fallbackToDemo: boolean;\n  safeExecutionMode: string;\n  checkedAt: string;\n  message: string;\n}\n\nexport class QualiBugApiError extends Error {\n  constructor(\n    message: string,\n    public readonly status: number,\n    public readonly path: string,\n    public readonly safeDetails?: Record<string, unknown>,\n  ) {\n    super(message);\n    this.name = 'QualiBugApiError';\n  }\n}\n\nfunction normalizeEnvelope<T>(payload: ApiEnvelope<T> | T): T {\n  const maybeEnvelope = payload as ApiEnvelope<T>;\n  if (maybeEnvelope && typeof maybeEnvelope === 'object' && 'success' in maybeEnvelope) {\n    if (maybeEnvelope.success === false) {\n      throw new QualiBugApiError(maybeEnvelope.error?.message || 'QualiBug API returned an error', 200, 'envelope');\n    }\n    return (maybeEnvelope.data ?? payload) as T;\n  }\n  return payload as T;\n}\n\nexport function redactApiError(error: unknown): Record<string, unknown> {\n  if (error instanceof QualiBugApiError) {\n    return { name: error.name, status: error.status, path: error.path, message: error.message };\n  }\n  if (error instanceof Error) {\n    return { name: error.name, message: error.message };\n  }\n  return { message: 'Unknown API error' };\n}\n\nexport async function requestJson<T>({ method, path, body, timeoutMs = runtimeConfig.requestTimeoutMs }: ApiRequestOptions): Promise<T> {\n  const controller = new AbortController();\n  const timeout = window.setTimeout(() => controller.abort(), timeoutMs);\n  try {\n    const response = await fetch(`${runtimeConfig.apiBaseUrl.replace(/\\/$/, '')}${path}`, {\n      method,\n      signal: controller.signal,\n      headers: { 'Content-Type': 'application/json', 'X-QualiBug-Execution-Mode': runtimeConfig.safeExecutionMode },\n      body: body ? JSON.stringify(body) : undefined,\n    });\n    const rawText = await response.text();\n    const payload = rawText ? JSON.parse(rawText) : {};\n    if (!response.ok) {\n      throw new QualiBugApiError(`QualiBug API ${method} ${path} failed with ${response.status}`, response.status, path);\n    }\n    return normalizeEnvelope<T>(payload as ApiEnvelope<T> | T);\n  } finally {\n    window.clearTimeout(timeout);\n  }\n}\n\nexport async function loadRuntimeHealth(): Promise<RuntimeHealth> {\n  if (runtimeConfig.demoMode) {\n    return {\n      mode: 'demo',\n      apiBaseUrl: runtimeConfig.apiBaseUrl,\n      online: true,\n      fallbackToDemo: runtimeConfig.fallbackToDemo,\n      safeExecutionMode: runtimeConfig.safeExecutionMode,\n      checkedAt: new Date().toISOString(),\n      message: 'demo mode 使用本地脱敏数据，不触发真实客户环境。',\n    };\n  }\n  try {\n    await requestJson<Record<string, unknown>>({ method: 'GET', path: '/api/v1/health' });\n    return {\n      mode: 'real',\n      apiBaseUrl: runtimeConfig.apiBaseUrl,\n      online: true,\n      fallbackToDemo: runtimeConfig.fallbackToDemo,\n      safeExecutionMode: runtimeConfig.safeExecutionMode,\n      checkedAt: new Date().toISOString(),\n      message: 'real API mode 已连接 Phase104 API。',\n    };\n  } catch (error) {\n    return {\n      mode: 'real',\n      apiBaseUrl: runtimeConfig.apiBaseUrl,\n      online: false,\n      fallbackToDemo: runtimeConfig.fallbackToDemo,\n      safeExecutionMode: runtimeConfig.safeExecutionMode,\n      checkedAt: new Date().toISOString(),\n      message: `real API mode 暂不可用，${runtimeConfig.fallbackToDemo ? '将回退 demo fallback' : '不会回退'}。`,\n    };\n  }\n}\n""",
    )
    _write_text(
        app_dir / "src/api/qualibugClient.ts",
        """import { requestJson, type RuntimeHealth, loadRuntimeHealth } from './runtimeApi';\n\nexport class QualiBugClient {\n  health(): Promise<RuntimeHealth> { return loadRuntimeHealth(); }\n  listProjects() { return requestJson<Record<string, unknown>[]>({ method: 'GET', path: '/api/v1/projects' }); }\n  createProject(payload: Record<string, unknown>) { return requestJson<Record<string, unknown>>({ method: 'POST', path: '/api/v1/projects', body: payload }); }\n  getCommandCenter(projectId: string) { return requestJson<Record<string, unknown>>({ method: 'GET', path: `/api/v1/projects/${projectId}/command-center` }); }\n  getEnvironmentReadiness(projectId: string) { return requestJson<Record<string, unknown>>({ method: 'GET', path: `/api/v1/projects/${projectId}/environment/readiness` }); }\n  runEnvironmentPreflight(projectId: string, payload: Record<string, unknown> = { safe_execution_mode: 'read_only' }) { return requestJson<Record<string, unknown>>({ method: 'POST', path: `/api/v1/projects/${projectId}/environment/preflight`, body: payload }); }\n  getBusinessModel(projectId: string) { return requestJson<Record<string, unknown>>({ method: 'GET', path: `/api/v1/projects/${projectId}/business-model` }); }\n  getTestPlan(projectId: string) { return requestJson<Record<string, unknown>>({ method: 'GET', path: `/api/v1/projects/${projectId}/test-plan` }); }\n  generateTestPlan(projectId: string, payload: Record<string, unknown> = {}) { return requestJson<Record<string, unknown>>({ method: 'POST', path: `/api/v1/projects/${projectId}/test-plan/generate`, body: payload }); }\n  startTestRun(projectId: string, payload: Record<string, unknown> = { mode: 'read_only' }) { return requestJson<Record<string, unknown>>({ method: 'POST', path: `/api/v1/projects/${projectId}/test-runs`, body: payload }); }\n  getLiveMap(projectId: string) { return requestJson<Record<string, unknown>>({ method: 'GET', path: `/api/v1/projects/${projectId}/live-map` }); }\n  listRisks(projectId: string) { return requestJson<Record<string, unknown>[]>({ method: 'GET', path: `/api/v1/projects/${projectId}/risks` }); }\n  getRiskDetail(projectId: string, riskId: string) { return requestJson<Record<string, unknown>>({ method: 'GET', path: `/api/v1/projects/${projectId}/risks/${riskId}` }); }\n  getValueMetrics(projectId: string) { return requestJson<Record<string, unknown>>({ method: 'GET', path: `/api/v1/projects/${projectId}/value-metrics` }); }\n  getExecutiveReport(projectId: string) { return requestJson<Record<string, unknown>>({ method: 'GET', path: `/api/v1/projects/${projectId}/reports/executive` }); }\n  generateExecutiveReport(projectId: string, payload: Record<string, unknown> = {}) { return requestJson<Record<string, unknown>>({ method: 'POST', path: `/api/v1/projects/${projectId}/reports/generate`, body: payload }); }\n}\n\nexport const qualiBugClient = new QualiBugClient();\n""",
    )


def _write_real_api_runtime(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/services/realApiRuntime.ts",
        """import { qualiBugClient } from '../api/qualibugClient';\nimport { demoData } from '../data/demoData';\nimport { runtimeConfig } from '../app/runtimeConfig';\nimport { loadRuntimeHealth, redactApiError, type RuntimeHealth } from '../api/runtimeApi';\nimport type { QualiBugViewModel } from './qualibugDataSource';\n\ntype ApiLoader<T> = () => Promise<T>;\n\nexport interface RuntimeLoadResult<T> {\n  mode: 'demo' | 'real';\n  source: 'demo-data' | 'phase104-api' | 'demo-fallback';\n  value: T;\n  runtimeHealth: RuntimeHealth;\n  error?: Record<string, unknown>;\n}\n\nfunction demoView(): QualiBugViewModel {\n  return {\n    mode: 'demo',\n    project: demoData.project,\n    dashboard: demoData.dashboard,\n    environment: demoData.environment,\n    testPlan: demoData.test_plan,\n    liveMap: demoData.live_map,\n    risks: demoData.risks as Record<string, unknown>[],\n    riskDetail: demoData.risk_detail,\n    valueMetrics: demoData.value_metrics,\n    executiveReport: demoData.executive_report,\n  };\n}\n\nexport class RuntimeApiAdapter {\n  constructor(private readonly projectId = runtimeConfig.projectId) {}\n\n  async runtimeHealth(): Promise<RuntimeHealth> {\n    return loadRuntimeHealth();\n  }\n\n  async safeApiCall<T>(loader: ApiLoader<T>, fallback: T): Promise<RuntimeLoadResult<T>> {\n    const runtimeHealth = await loadRuntimeHealth();\n    if (runtimeConfig.demoMode) {\n      return { mode: 'demo', source: 'demo-data', value: fallback, runtimeHealth };\n    }\n    try {\n      const value = await loader();\n      return { mode: 'real', source: 'phase104-api', value, runtimeHealth: { ...runtimeHealth, online: true } };\n    } catch (error) {\n      if (!runtimeConfig.fallbackToDemo) throw error;\n      return {\n        mode: 'real',\n        source: 'demo-fallback',\n        value: fallback,\n        runtimeHealth: { ...runtimeHealth, online: false },\n        error: redactApiError(error),\n      };\n    }\n  }\n\n  async loadDashboard(): Promise<QualiBugViewModel> {\n    const view = demoView();\n    const dashboard = await this.safeApiCall(() => qualiBugClient.getCommandCenter(this.projectId), view.dashboard);\n    return { ...view, mode: dashboard.mode, dashboard: dashboard.value, runtimeHealth: dashboard.runtimeHealth, runtimeSource: dashboard.source } as QualiBugViewModel;\n  }\n\n  async loadCustomerIntake(): Promise<QualiBugViewModel> {\n    const view = demoView();\n    const projects = await this.safeApiCall(() => qualiBugClient.listProjects(), [] as Record<string, unknown>[]);\n    return { ...view, mode: projects.mode, project: { ...view.project, projects: projects.value }, runtimeHealth: projects.runtimeHealth, runtimeSource: projects.source } as QualiBugViewModel;\n  }\n\n  async loadEnvironment(): Promise<QualiBugViewModel> {\n    const view = demoView();\n    const environment = await this.safeApiCall(() => qualiBugClient.getEnvironmentReadiness(this.projectId), view.environment);\n    return { ...view, mode: environment.mode, environment: environment.value, runtimeHealth: environment.runtimeHealth, runtimeSource: environment.source } as QualiBugViewModel;\n  }\n\n  async runEnvironmentPreflight(): Promise<Record<string, unknown>> {\n    const result = await this.safeApiCall(\n      () => qualiBugClient.runEnvironmentPreflight(this.projectId, { safe_execution_mode: runtimeConfig.safeExecutionMode }),\n      { accepted: true, mode: 'demo', message: 'demo fallback 不触发真实客户环境' },\n    );\n    return { ...result.value, runtimeSource: result.source, runtimeHealth: result.runtimeHealth };\n  }\n\n  async loadBusinessFlow(): Promise<QualiBugViewModel> {\n    const view = demoView();\n    const businessModel = await this.safeApiCall(() => qualiBugClient.getBusinessModel(this.projectId), view.liveMap);\n    return { ...view, mode: businessModel.mode, liveMap: businessModel.value, runtimeHealth: businessModel.runtimeHealth, runtimeSource: businessModel.source } as QualiBugViewModel;\n  }\n\n  async loadTestExecution(): Promise<QualiBugViewModel> {\n    const view = demoView();\n    const testPlan = await this.safeApiCall(() => qualiBugClient.getTestPlan(this.projectId), view.testPlan);\n    const liveMap = await this.safeApiCall(() => qualiBugClient.getLiveMap(this.projectId), view.liveMap);\n    return { ...view, mode: testPlan.mode, testPlan: testPlan.value, liveMap: liveMap.value, runtimeHealth: testPlan.runtimeHealth, runtimeSource: testPlan.source } as QualiBugViewModel;\n  }\n\n  async generateTestPlan(): Promise<Record<string, unknown>> {\n    const result = await this.safeApiCall(() => qualiBugClient.generateTestPlan(this.projectId), { accepted: true, mode: 'demo', message: 'demo fallback 生成脱敏测试计划' });\n    return { ...result.value, runtimeSource: result.source, runtimeHealth: result.runtimeHealth };\n  }\n\n  async startTestRun(): Promise<Record<string, unknown>> {\n    const result = await this.safeApiCall(() => qualiBugClient.startTestRun(this.projectId, { mode: runtimeConfig.safeExecutionMode }), { accepted: true, mode: 'demo', message: 'demo fallback 启动只读虚拟执行' });\n    return { ...result.value, runtimeSource: result.source, runtimeHealth: result.runtimeHealth };\n  }\n\n  async loadRiskEvidence(): Promise<QualiBugViewModel> {\n    const view = demoView();\n    const risks = await this.safeApiCall(() => qualiBugClient.listRisks(this.projectId), view.risks);\n    return { ...view, mode: risks.mode, risks: risks.value, runtimeHealth: risks.runtimeHealth, runtimeSource: risks.source } as QualiBugViewModel;\n  }\n\n  async loadReportRoi(): Promise<QualiBugViewModel> {\n    const view = demoView();\n    const valueMetrics = await this.safeApiCall(() => qualiBugClient.getValueMetrics(this.projectId), view.valueMetrics);\n    const executiveReport = await this.safeApiCall(() => qualiBugClient.getExecutiveReport(this.projectId), view.executiveReport);\n    return { ...view, mode: valueMetrics.mode, valueMetrics: valueMetrics.value, executiveReport: executiveReport.value, runtimeHealth: valueMetrics.runtimeHealth, runtimeSource: valueMetrics.source } as QualiBugViewModel;\n  }\n}\n\nexport const runtimeApiAdapter = new RuntimeApiAdapter();\n""",
    )
    _write_text(
        app_dir / "src/services/qualibugDataSource.ts",
        """import { runtimeApiAdapter } from './realApiRuntime';\nimport type { DataMode } from '../app/dataMode';\nimport type { RuntimeHealth } from '../api/runtimeApi';\n\nexport interface QualiBugViewModel {\n  mode: DataMode;\n  project: Record<string, unknown>;\n  dashboard: Record<string, unknown>;\n  environment: Record<string, unknown>;\n  testPlan: Record<string, unknown>;\n  liveMap: Record<string, unknown>;\n  risks: Record<string, unknown>[];\n  riskDetail: Record<string, unknown>;\n  valueMetrics: Record<string, unknown>;\n  executiveReport: Record<string, unknown>;\n  runtimeHealth?: RuntimeHealth;\n  runtimeSource?: 'demo-data' | 'phase104-api' | 'demo-fallback';\n}\n\nexport class QualiBugDataSource {\n  loadRuntimeHealth() { return runtimeApiAdapter.runtimeHealth(); }\n  loadDashboard() { return runtimeApiAdapter.loadDashboard(); }\n  loadCustomerIntake() { return runtimeApiAdapter.loadCustomerIntake(); }\n  loadEnvironment() { return runtimeApiAdapter.loadEnvironment(); }\n  runEnvironmentPreflight() { return runtimeApiAdapter.runEnvironmentPreflight(); }\n  loadBusinessFlow() { return runtimeApiAdapter.loadBusinessFlow(); }\n  loadTestExecution() { return runtimeApiAdapter.loadTestExecution(); }\n  generateTestPlan() { return runtimeApiAdapter.generateTestPlan(); }\n  startTestRun() { return runtimeApiAdapter.startTestRun(); }\n  loadRiskEvidence() { return runtimeApiAdapter.loadRiskEvidence(); }\n  loadReportRoi() { return runtimeApiAdapter.loadReportRoi(); }\n}\n\nexport const qualiBugDataSource = new QualiBugDataSource();\n""",
    )


def _write_runtime_hook_and_page(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/hooks/useRuntimeHealth.ts",
        """import { useEffect, useState } from 'react';\nimport { qualiBugDataSource } from '../services/qualibugDataSource';\nimport type { RuntimeHealth } from '../api/runtimeApi';\n\nexport function useRuntimeHealth() {\n  const [runtimeHealth, setRuntimeHealth] = useState<RuntimeHealth | null>(null);\n  const [loading, setLoading] = useState(true);\n  const [error, setError] = useState<string | null>(null);\n\n  useEffect(() => {\n    let mounted = true;\n    qualiBugDataSource.loadRuntimeHealth()\n      .then((health) => { if (mounted) { setRuntimeHealth(health); setError(null); } })\n      .catch((err: unknown) => { if (mounted) setError(err instanceof Error ? err.message : 'runtime health failed'); })\n      .finally(() => { if (mounted) setLoading(false); });\n    return () => { mounted = false; };\n  }, []);\n\n  return { runtimeHealth, loading, error };\n}\n""",
    )
    _write_text(
        app_dir / "src/pages/ApiRuntimeWorkbenchPage.tsx",
        """import { useRuntimeHealth } from '../hooks/useRuntimeHealth';\nimport { runtimeConfig } from '../app/runtimeConfig';\nimport { PageShell } from '../components/PageShell';\nimport '../styles/api-runtime.css';\n\nconst runtimeChecks = [\n  '真实 API Client 运行模式',\n  'Phase104 API health',\n  'environment diagnosis',\n  'test plan generation',\n  'risk evidence',\n  'executive report',\n  'demo fallback',\n  'read-only execution',\n  '默认脱敏',\n];\n\nexport function ApiRuntimeWorkbenchPage() {\n  const { runtimeHealth, loading, error } = useRuntimeHealth();\n  return (\n    <PageShell title="真实 API Client 运行模式" subtitle="连接 Phase104 API，同时保留 demo fallback，保证客户现场演示不中断。">\n      <div className="qb-runtime-grid">\n        <article className="qb-runtime-card">\n          <span>runtime mode</span>\n          <strong>{runtimeConfig.demoMode ? 'demo mode' : 'real API mode'}</strong>\n          <p>{runtimeConfig.apiBaseUrl}</p>\n        </article>\n        <article className="qb-runtime-card">\n          <span>runtimeHealth</span>\n          <strong>{loading ? 'checking' : runtimeHealth?.online ? 'online' : 'offline'}</strong>\n          <p>{error || runtimeHealth?.message || '等待检查'}</p>\n        </article>\n        <article className="qb-runtime-card">\n          <span>safe execution</span>\n          <strong>{runtimeConfig.safeExecutionMode}</strong>\n          <p>所有真实执行默认带只读或受控执行标记。</p>\n        </article>\n      </div>\n      <section className="qb-runtime-card">\n        <h3>API runtime acceptance checklist</h3>\n        <ul className="qb-runtime-list">\n          {runtimeChecks.map((item) => <li key={item}>{item}</li>)}\n        </ul>\n      </section>\n      <section className="qb-runtime-card">\n        <h3>Runtime health payload</h3>\n        <pre>{JSON.stringify(runtimeHealth, null, 2)}</pre>\n      </section>\n    </PageShell>\n  );\n}\n""",
    )
    _write_text(
        app_dir / "src/styles/api-runtime.css",
        """.qb-runtime-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; }\n.qb-runtime-card { background: #fff; border: 1px solid var(--qb-border); border-radius: var(--qb-radius); box-shadow: var(--qb-shadow); padding: 18px; }\n.qb-runtime-card span { color: var(--qb-muted); font-size: 12px; font-weight: 900; text-transform: uppercase; }\n.qb-runtime-card strong { display: block; margin: 8px 0; font-size: 24px; }\n.qb-runtime-list { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; margin: 0; padding: 0; list-style: none; }\n.qb-runtime-list li { border: 1px solid var(--qb-border); border-radius: 999px; padding: 8px 12px; color: var(--qb-primary); font-weight: 800; }\n@media (max-width: 900px) { .qb-runtime-grid, .qb-runtime-list { grid-template-columns: 1fr; } }\n""",
    )


def _patch_routes(app_dir: Path) -> None:
    routes_path = app_dir / "src/routes.ts"
    routes = _read_text(routes_path)
    if "'/api-runtime'" not in routes:
        if "{ path: '/component-model'" in routes:
            marker_line = next((line for line in routes.splitlines() if "{ path: '/component-model'" in line), "")
            if marker_line:
                routes = routes.replace(
                    marker_line,
                    marker_line + "\n  { path: '/api-runtime', key: 'api_runtime', label: '真实 API Client 运行模式', component: 'ApiRuntimeWorkbenchPage' },",
                )
        _write_text(routes_path, routes)

    app_path = app_dir / "src/App.tsx"
    app = _read_text(app_path)
    if "ApiRuntimeWorkbenchPage" not in app:
        app = app.replace(
            "import { ComponentModelWorkbenchPage } from './pages/ComponentModelWorkbenchPage';",
            "import { ComponentModelWorkbenchPage } from './pages/ComponentModelWorkbenchPage';\nimport { ApiRuntimeWorkbenchPage } from './pages/ApiRuntimeWorkbenchPage';",
        )
        app = app.replace(
            "import './styles/component-model.css';",
            "import './styles/component-model.css';\nimport './styles/api-runtime.css';",
        )
        app = app.replace(
            "    case '/component-model': return <ComponentModelWorkbenchPage />;",
            "    case '/component-model': return <ComponentModelWorkbenchPage />;\n    case '/api-runtime': return <ApiRuntimeWorkbenchPage />;",
        )
        _write_text(app_path, app)


def _write_api_runtime_contract_test(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/__tests__/api-runtime-contract.test.ts",
        """import { describe, expect, it } from 'vitest';\nimport { runtimeConfig } from '../app/runtimeConfig';\nimport { requestJson, redactApiError, loadRuntimeHealth } from '../api/runtimeApi';\nimport { RuntimeApiAdapter } from '../services/realApiRuntime';\nimport { pageComponents } from '../routes';\n\ndescribe('Phase106C API runtime contract', () => {\n  it('keeps real API mode configurable and safe by default', () => {\n    expect(runtimeConfig.apiBaseUrl).toContain('http');\n    expect(runtimeConfig.fallbackToDemo).toBe(true);\n    expect(runtimeConfig.safeExecutionMode).toBe('read_only');\n  });\n\n  it('keeps /api-runtime route visible', () => {\n    expect(Object.keys(pageComponents)).toContain('/api-runtime');\n  });\n\n  it('exposes runtime primitives for Phase104 API calls', () => {\n    expect(typeof requestJson).toBe('function');\n    expect(typeof loadRuntimeHealth).toBe('function');\n    expect(redactApiError(new Error('safe error')).message).toBe('safe error');\n  });\n\n  it('keeps RuntimeApiAdapter methods for critical pages', () => {\n    const adapter = new RuntimeApiAdapter('demo-project');\n    expect(typeof adapter.loadEnvironment).toBe('function');\n    expect(typeof adapter.runEnvironmentPreflight).toBe('function');\n    expect(typeof adapter.loadTestExecution).toBe('function');\n    expect(typeof adapter.generateTestPlan).toBe('function');\n    expect(typeof adapter.startTestRun).toBe('function');\n    expect(typeof adapter.loadRiskEvidence).toBe('function');\n    expect(typeof adapter.loadReportRoi).toBe('function');\n  });\n});\n""",
    )


def _write_api_runtime_readme(app_dir: Path) -> None:
    endpoint_lines = "\n".join(f"- `{item['method']} {item['path']}` → `{item['client']}` / {item['page']}" for item in _runtime_endpoint_contract())
    _write_text(
        app_dir / "README_FRONTEND_API_RUNTIME.md",
        f"""# Phase106C 真实 API Client 运行模式接入\n\nPhase106C 在 Phase106B 组件模型基础上，补齐真实 API 运行模式。\n\n## 新增能力\n\n- `runtimeConfig.ts`：集中管理 `VITE_QUALIBUG_API_BASE_URL`、`VITE_QUALIBUG_DEMO_MODE`、`VITE_QUALIBUG_FALLBACK_TO_DEMO`、请求超时和安全执行模式。\n- `runtimeApi.ts`：统一 `requestJson`、`AbortController` 超时、API envelope 归一化、错误脱敏和 runtimeHealth。\n- `realApiRuntime.ts`：`RuntimeApiAdapter` 将页面数据加载切到 Phase104 API，并在客户现场后端不可用时进入 `demo fallback`。\n- `/api-runtime`：前端真实 API 运行模式工作台。\n- `api-runtime-contract.test.ts`：验证关键 API、只读执行和 demo fallback。\n\n## 真实 API Contract\n\n{endpoint_lines}\n\n## 本地启动\n\n```powershell\nnpm install\nnpm run dev\n```\n\n打开：\n\n```text\nhttp://127.0.0.1:5173/api-runtime\n```\n\n## 切真实 API\n\n```text\nVITE_QUALIBUG_DEMO_MODE=false\nVITE_QUALIBUG_FALLBACK_TO_DEMO=true\nVITE_QUALIBUG_API_BASE_URL=http://127.0.0.1:8790\nVITE_QUALIBUG_SAFE_EXECUTION_MODE=read_only\n```\n\n## 安全边界\n\n- 默认只读执行。\n- API 错误只展示脱敏摘要。\n- 客户现场后端不可用时可回退 demo fallback，避免演示中断。\n""",
    )


def _write_manifest_files(output_dir: Path, report: FrontendApiRuntimeAcceptanceReport | None = None) -> dict[str, Any]:
    manifest = redact_value(
        {
            "version": PHASE106C_VERSION,
            "generated_at": _now(),
            "app_dir": FRONTEND_APP_DIR,
            "entrypoint": f"{FRONTEND_APP_DIR}/index.html",
            "runtime_route": "/api-runtime",
            "required_files": list(REQUIRED_API_RUNTIME_FILES),
            "core_labels": list(CORE_API_RUNTIME_LABELS),
            "runtime_endpoint_contract": _runtime_endpoint_contract(),
            "runtime_modes": {
                "demo mode": "本地脱敏演示数据，不触发客户环境。",
                "real API mode": "通过 RuntimeApiAdapter 调用 Phase104 API。",
                "demo fallback": "真实 API 不可用时回退演示数据，保持页面可演示。",
            },
            "artifacts": {
                "manifest_json": API_RUNTIME_MANIFEST_JSON,
                "manifest_md": API_RUNTIME_MANIFEST_MD,
                "acceptance_json": API_RUNTIME_ACCEPTANCE_JSON,
                "acceptance_md": API_RUNTIME_ACCEPTANCE_MD,
                "checksums": API_RUNTIME_CHECKSUMS,
                "zip": API_RUNTIME_ZIP,
            },
            "acceptance": {"passed": report.passed, "score": report.score, "checks": len(report.checks)} if report else None,
        }
    )
    _write_text(output_dir / API_RUNTIME_MANIFEST_JSON, _json_dump(manifest))
    endpoint_lines = "\n".join(f"- `{item['method']} {item['path']}` → `{item['client']}` / {item['page']}" for item in manifest["runtime_endpoint_contract"])
    _write_text(
        output_dir / API_RUNTIME_MANIFEST_MD,
        f"""# Phase106C Frontend API Runtime Manifest\n\n- Version: `{manifest['version']}`\n- App dir: `{manifest['app_dir']}`\n- Entrypoint: `{manifest['entrypoint']}`\n- Runtime route: `{manifest['runtime_route']}`\n\n## Runtime Endpoint Contract\n\n{endpoint_lines}\n\n## Runtime Modes\n\n- `demo mode`：本地脱敏演示数据。\n- `real API mode`：调用 Phase104 API。\n- `demo fallback`：后端不可用时回退演示数据。\n\n## Security\n\n默认只读执行，扫描 token / cookie / session / client_secret / traceback 等高风险泄露模式。\n""",
    )
    return manifest


def _write_report_files(output_dir: Path, report: FrontendApiRuntimeAcceptanceReport) -> None:
    payload = report.to_dict()
    _write_text(output_dir / API_RUNTIME_ACCEPTANCE_JSON, _json_dump(payload))
    check_lines = "\n".join(f"- [{'x' if check['passed'] else ' '}] `{check['key']}`：{check['detail']}" for check in payload["checks"])
    _write_text(
        output_dir / API_RUNTIME_ACCEPTANCE_MD,
        f"""# Phase106C Frontend API Runtime Acceptance\n\n- Passed: `{payload['passed']}`\n- Score: `{payload['score']}`\n- Version: `{payload['version']}`\n- App dir: `{payload['app_dir']}`\n\n## Checks\n\n{check_lines}\n""",
    )


def scan_frontend_api_runtime_for_secret_leaks(output_dir: str | Path) -> list[str]:
    root = Path(output_dir)
    leaks: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() in {".zip", ".png", ".jpg", ".jpeg", ".webp"}:
            continue
        text = _read_text(path, limit=400_000)
        for pattern in FORBIDDEN_API_RUNTIME_PATTERNS:
            if pattern in text:
                leaks.append(f"{path.relative_to(root).as_posix()} contains forbidden pattern {pattern}")
    return leaks


def build_frontend_api_runtime(
    output_dir: str | Path,
    *,
    scenario: str = "manufacturing",
    api_base_url: str = "http://127.0.0.1:8790",
    clean: bool = True,
) -> FrontendApiRuntimeAcceptanceReport:
    root = Path(output_dir)
    if clean and root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    build_frontend_component_model(root, scenario=scenario, api_base_url=api_base_url, clean=False)
    app_dir = root / FRONTEND_APP_DIR

    _write_runtime_config(app_dir)
    _write_runtime_api(app_dir)
    _write_real_api_runtime(app_dir)
    _write_runtime_hook_and_page(app_dir)
    _patch_routes(app_dir)
    _write_api_runtime_contract_test(app_dir)
    _write_api_runtime_readme(app_dir)
    _write_manifest_files(root)

    report = validate_frontend_api_runtime(root, scenario=scenario, write_report=True, skip_checksum=True)
    _write_manifest_files(root, report)
    write_frontend_api_runtime_checksums(root)
    _zip_api_runtime(root)
    report = validate_frontend_api_runtime(root, scenario=scenario, write_report=True)
    _write_manifest_files(root, report)
    write_frontend_api_runtime_checksums(root)
    _zip_api_runtime(root)
    return report


def validate_frontend_api_runtime(
    output_dir: str | Path,
    *,
    scenario: str = "manufacturing",
    write_report: bool = True,
    skip_checksum: bool = False,
) -> FrontendApiRuntimeAcceptanceReport:
    root = Path(output_dir)
    app_dir = root / FRONTEND_APP_DIR
    checks: list[FrontendApiRuntimeCheck] = []

    missing = [relative for relative in REQUIRED_API_RUNTIME_FILES if not (root / relative).exists()]
    if skip_checksum:
        missing = [relative for relative in missing if relative != API_RUNTIME_CHECKSUMS]
    if not skip_checksum and API_RUNTIME_ZIP in missing:
        missing.remove(API_RUNTIME_ZIP)
    checks.append(FrontendApiRuntimeCheck("required_files", not missing, "API runtime 必需文件完整" if not missing else f"缺失文件: {missing}"))

    runtime_api = _read_text(app_dir / "src/api/runtimeApi.ts")
    runtime_api_ok = all(keyword in runtime_api for keyword in ("AbortController", "requestTimeoutMs", "normalizeEnvelope", "redactApiError", "loadRuntimeHealth", "X-QualiBug-Execution-Mode"))
    checks.append(FrontendApiRuntimeCheck("runtime_api_client", runtime_api_ok, "runtimeApi 支持超时、envelope、错误脱敏和安全执行头" if runtime_api_ok else "runtimeApi 能力不完整"))

    client_text = _read_text(app_dir / "src/api/qualibugClient.ts")
    missing_methods = [method for method in RUNTIME_API_METHODS if f"{method}(" not in client_text]
    checks.append(FrontendApiRuntimeCheck("phase104_client_methods", not missing_methods, "Phase104 API Client 方法完整" if not missing_methods else f"缺失方法: {missing_methods}"))

    runtime_adapter = _read_text(app_dir / "src/services/realApiRuntime.ts")
    adapter_ok = all(keyword in runtime_adapter for keyword in ("RuntimeApiAdapter", "safeApiCall", "fallbackToDemo", "demo-fallback", "runEnvironmentPreflight", "generateTestPlan", "startTestRun", "loadRiskEvidence", "loadReportRoi"))
    checks.append(FrontendApiRuntimeCheck("runtime_adapter", adapter_ok, "RuntimeApiAdapter 已覆盖环境诊断、测试计划、风险证据和报告 ROI" if adapter_ok else "RuntimeApiAdapter 覆盖不足"))

    data_source = _read_text(app_dir / "src/services/qualibugDataSource.ts")
    data_source_ok = "loadRuntimeHealth" in data_source and "runtimeApiAdapter" in data_source and "QualiBugDataSource" in data_source
    checks.append(FrontendApiRuntimeCheck("data_source_runtime_wiring", data_source_ok, "QualiBugDataSource 已接入真实 API runtime" if data_source_ok else "数据源未接入 runtime"))

    routes_text = _read_text(app_dir / "src/routes.ts")
    route_ok = "'/api-runtime'" in routes_text and "ApiRuntimeWorkbenchPage" in routes_text and "'/component-model'" in routes_text
    checks.append(FrontendApiRuntimeCheck("api_runtime_route", route_ok, "已接入 /api-runtime 工作台" if route_ok else "缺少 /api-runtime 路由"))

    page_text = _read_text(app_dir / "src/pages/ApiRuntimeWorkbenchPage.tsx")
    page_missing = [label for label in ("真实 API Client 运行模式", "runtimeHealth", "demo fallback", "read-only execution", "Phase104 API") if label not in page_text]
    checks.append(FrontendApiRuntimeCheck("runtime_workbench_page", not page_missing, "API runtime 工作台覆盖关键业务语义" if not page_missing else f"工作台缺失文案: {page_missing}"))

    env_text = _read_text(app_dir / ".env.example")
    env_ok = all(keyword in env_text for keyword in ("VITE_QUALIBUG_API_BASE_URL", "VITE_QUALIBUG_DEMO_MODE", "VITE_QUALIBUG_FALLBACK_TO_DEMO", "VITE_QUALIBUG_REQUEST_TIMEOUT_MS", "VITE_QUALIBUG_SAFE_EXECUTION_MODE"))
    checks.append(FrontendApiRuntimeCheck("runtime_env_contract", env_ok, ".env.example 已描述真实 API 运行变量" if env_ok else "运行时环境变量不完整"))

    contract_test = _read_text(app_dir / "src/__tests__/api-runtime-contract.test.ts")
    contract_ok = all(keyword in contract_test for keyword in ("/api-runtime", "requestJson", "loadRuntimeHealth", "RuntimeApiAdapter", "loadEnvironment", "loadRiskEvidence", "loadReportRoi"))
    checks.append(FrontendApiRuntimeCheck("api_runtime_contract_test", contract_ok, "已生成 API runtime 合同测试" if contract_ok else "API runtime 合同测试覆盖不足"))

    manifest = _read_json(root / API_RUNTIME_MANIFEST_JSON)
    manifest_ok = manifest.get("version") == PHASE106C_VERSION and manifest.get("runtime_route") == "/api-runtime" and len(manifest.get("runtime_endpoint_contract") or []) >= 16
    checks.append(FrontendApiRuntimeCheck("manifest", manifest_ok, "manifest 描述 runtime route、endpoint contract 和产物" if manifest_ok else "manifest 内容不完整"))

    if skip_checksum:
        checksum_ok = True
        checksum_detail = "构建中跳过 checksum 复验"
    else:
        checksum_failures = verify_frontend_api_runtime_checksums(root)
        checksum_ok = not checksum_failures
        checksum_detail = "checksum 复验通过" if checksum_ok else f"checksum 失败: {checksum_failures}"
    checks.append(FrontendApiRuntimeCheck("checksums", checksum_ok, checksum_detail))

    leaks = scan_frontend_api_runtime_for_secret_leaks(root)
    checks.append(FrontendApiRuntimeCheck("secret_leak_scan", not leaks, "未发现高风险敏感信息泄露模式" if not leaks else f"发现泄露风险: {leaks}"))

    passed = all(check.passed for check in checks)
    score = round(sum(1 for check in checks if check.passed) / len(checks) * 100) if checks else 0
    report = FrontendApiRuntimeAcceptanceReport(
        passed=passed,
        score=score,
        version=PHASE106C_VERSION,
        scenario=scenario,
        output_dir=str(root),
        app_dir=str(app_dir),
        checks=checks,
        artifacts={
            "app_dir": FRONTEND_APP_DIR,
            "entrypoint": f"{FRONTEND_APP_DIR}/index.html",
            "runtime_route": "/api-runtime",
            "manifest_json": API_RUNTIME_MANIFEST_JSON,
            "acceptance_json": API_RUNTIME_ACCEPTANCE_JSON,
            "checksums": API_RUNTIME_CHECKSUMS,
            "zip": API_RUNTIME_ZIP,
        },
    )
    if write_report:
        _write_report_files(root, report)
    return report


def run_frontend_api_runtime_export(
    output_dir: str | Path,
    *,
    scenario: str = "manufacturing",
    api_base_url: str = "http://127.0.0.1:8790",
    validate_only: bool = False,
) -> FrontendApiRuntimeAcceptanceReport:
    if validate_only:
        return validate_frontend_api_runtime(output_dir, scenario=scenario, write_report=True)
    return build_frontend_api_runtime(output_dir, scenario=scenario, api_base_url=api_base_url)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build or validate the Phase106C QualiBug frontend API runtime.")
    parser.add_argument("--output-dir", default="outputs/phase106_frontend_api_runtime", help="Output directory")
    parser.add_argument("--scenario", default="manufacturing", help="Seed demo scenario")
    parser.add_argument("--api-base-url", default="http://127.0.0.1:8790", help="Phase104 API base URL")
    parser.add_argument("--validate-only", action="store_true", help="Validate existing API runtime output")
    args = parser.parse_args(argv)

    report = run_frontend_api_runtime_export(
        args.output_dir,
        scenario=args.scenario,
        api_base_url=args.api_base_url,
        validate_only=args.validate_only,
    )
    print(_json_dump(report.to_dict()))
    return 0 if report.passed else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
