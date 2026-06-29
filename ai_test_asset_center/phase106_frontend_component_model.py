from __future__ import annotations

"""Phase106B: componentize the Phase106 frontend app and add data-mode wiring.

Phase106A generated a Vite + React + TypeScript scaffold.  Phase106B keeps that
scaffold as the base, then upgrades the generated frontend into a clearer
component model that can be developed like a real product:

* explicit demo mode / real API mode resolution
* a typed data source boundary between UI and API client
* page-shell and reusable domain components
* a component model workbench route
* contract checks proving routes, components, API methods and safe display rules

This module does not mutate repository frontend source.  It generates an output
frontend app that extends the Phase106A scaffold and validates the generated
artifact.
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
from ai_test_asset_center.phase106_frontend_app_scaffold import (
    FRONTEND_APP_DIR,
    build_frontend_app_scaffold,
)

PHASE106B_VERSION = "phase106b-frontend-component-model-v1"

COMPONENT_MODEL_MANIFEST_JSON = "frontend_component_model_manifest.json"
COMPONENT_MODEL_MANIFEST_MD = "frontend_component_model_manifest.md"
COMPONENT_MODEL_ACCEPTANCE_JSON = "frontend_component_model_acceptance_report.json"
COMPONENT_MODEL_ACCEPTANCE_MD = "frontend_component_model_acceptance_report.md"
COMPONENT_MODEL_CHECKSUMS = "CHECKSUMS_PHASE106B.sha256"
COMPONENT_MODEL_ZIP = "phase106_frontend_component_model.zip"

REQUIRED_COMPONENT_MODEL_FILES: tuple[str, ...] = (
    f"{FRONTEND_APP_DIR}/src/app/appConfig.ts",
    f"{FRONTEND_APP_DIR}/src/app/dataMode.ts",
    f"{FRONTEND_APP_DIR}/src/services/qualibugDataSource.ts",
    f"{FRONTEND_APP_DIR}/src/hooks/useQualiBugData.ts",
    f"{FRONTEND_APP_DIR}/src/components/PageShell.tsx",
    f"{FRONTEND_APP_DIR}/src/components/DataModeBadge.tsx",
    f"{FRONTEND_APP_DIR}/src/components/KpiRail.tsx",
    f"{FRONTEND_APP_DIR}/src/components/FlowNodeCard.tsx",
    f"{FRONTEND_APP_DIR}/src/components/ProbeTable.tsx",
    f"{FRONTEND_APP_DIR}/src/components/RiskList.tsx",
    f"{FRONTEND_APP_DIR}/src/components/ActionQueue.tsx",
    f"{FRONTEND_APP_DIR}/src/pages/ComponentModelWorkbenchPage.tsx",
    f"{FRONTEND_APP_DIR}/src/__tests__/component-model-contract.test.ts",
    f"{FRONTEND_APP_DIR}/src/styles/component-model.css",
    f"{FRONTEND_APP_DIR}/README_FRONTEND_COMPONENT_MODEL.md",
    COMPONENT_MODEL_MANIFEST_JSON,
    COMPONENT_MODEL_MANIFEST_MD,
    COMPONENT_MODEL_ACCEPTANCE_JSON,
    COMPONENT_MODEL_ACCEPTANCE_MD,
    COMPONENT_MODEL_CHECKSUMS,
    COMPONENT_MODEL_ZIP,
)

CORE_COMPONENT_MODEL_LABELS: tuple[str, ...] = (
    "组件模型真实化",
    "demo mode",
    "real API mode",
    "QualiBugDataSource",
    "useQualiBugData",
    "PageShell",
    "KpiRail",
    "ProbeTable",
    "RiskList",
    "ActionQueue",
    "客户资料导入",
    "环境诊断",
    "AI 测试计划",
    "实时测试执行",
    "风险证据链",
    "领导层报告",
    "ROI 价值中心",
    "默认脱敏",
)

REQUIRED_COMPONENTS: tuple[str, ...] = (
    "PageShell",
    "DataModeBadge",
    "KpiRail",
    "FlowNodeCard",
    "ProbeTable",
    "RiskList",
    "ActionQueue",
)

REQUIRED_DATA_SOURCE_METHODS: tuple[str, ...] = (
    "loadDashboard",
    "loadCustomerIntake",
    "loadEnvironment",
    "runEnvironmentPreflight",
    "loadBusinessFlow",
    "loadTestExecution",
    "generateTestPlan",
    "startTestRun",
    "loadRiskEvidence",
    "loadReportRoi",
)

FORBIDDEN_COMPONENT_MODEL_PATTERNS: tuple[str, ...] = (
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
class FrontendComponentModelCheck:
    key: str
    passed: bool
    detail: str
    owner: str = "frontend"
    severity: str = "critical"


@dataclass
class FrontendComponentModelAcceptanceReport:
    passed: bool
    score: int
    version: str
    scenario: str
    output_dir: str
    app_dir: str
    checks: list[FrontendComponentModelCheck] = field(default_factory=list)
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


def _read_text(path: Path, *, limit: int = 250_000) -> str:
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
    excluded = {COMPONENT_MODEL_CHECKSUMS, COMPONENT_MODEL_ZIP}
    return [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and path.name not in excluded
        and not path.name.endswith(".pyc")
        and "node_modules" not in path.parts
    ]


def write_frontend_component_model_checksums(output_dir: str | Path) -> dict[str, str]:
    root = Path(output_dir)
    checksums = {path.relative_to(root).as_posix(): _sha256(path) for path in _iter_checksum_files(root)}
    lines = [f"{digest}  {relative}" for relative, digest in sorted(checksums.items())]
    _write_text(root / COMPONENT_MODEL_CHECKSUMS, "\n".join(lines) + "\n")
    return checksums


def verify_frontend_component_model_checksums(output_dir: str | Path) -> list[str]:
    root = Path(output_dir)
    checksum_path = root / COMPONENT_MODEL_CHECKSUMS
    if not checksum_path.exists():
        return [f"missing {COMPONENT_MODEL_CHECKSUMS}"]
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


def _zip_component_model(output_dir: Path) -> Path:
    archive_path = output_dir / COMPONENT_MODEL_ZIP
    if archive_path.exists():
        archive_path.unlink()
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(output_dir.rglob("*")):
            if path.is_file() and path != archive_path and not path.name.endswith(".pyc") and "node_modules" not in path.parts:
                archive.write(path, path.relative_to(output_dir).as_posix())
    return archive_path


def _component_inventory() -> list[dict[str, str]]:
    return [
        {"name": "PageShell", "role": "统一页面标题、动作区、说明和默认脱敏提示", "file": "src/components/PageShell.tsx"},
        {"name": "DataModeBadge", "role": "显示 demo mode / real API mode", "file": "src/components/DataModeBadge.tsx"},
        {"name": "KpiRail", "role": "统一 KPI 轨道", "file": "src/components/KpiRail.tsx"},
        {"name": "FlowNodeCard", "role": "业务流程节点卡", "file": "src/components/FlowNodeCard.tsx"},
        {"name": "ProbeTable", "role": "可执行探针与阻断探针表格", "file": "src/components/ProbeTable.tsx"},
        {"name": "RiskList", "role": "风险证据链列表", "file": "src/components/RiskList.tsx"},
        {"name": "ActionQueue", "role": "客户下一步动作队列", "file": "src/components/ActionQueue.tsx"},
    ]


def _mode_contract() -> dict[str, Any]:
    return {
        "demo_mode": {
            "source": "src/data/demoData.ts",
            "behavior": "不依赖后端，使用默认脱敏演示数据，适合售前演示和本地开发。",
            "env": "VITE_QUALIBUG_DEMO_MODE=true",
        },
        "real_api_mode": {
            "source": "src/api/qualibugClient.ts",
            "behavior": "通过 QualiBugDataSource 调用 Phase104 API Client，适合接真实后端。",
            "env": "VITE_QUALIBUG_DEMO_MODE=false",
        },
        "boundary": "页面只依赖 useQualiBugData / QualiBugDataSource，不直接散落 fetch 调用。",
    }


def _write_app_config(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/app/appConfig.ts",
        """export interface QualiBugAppConfig {\n  apiBaseUrl: string;\n  demoMode: boolean;\n  projectId: string;\n}\n\nfunction readBoolean(value: unknown, fallback: boolean): boolean {\n  if (typeof value !== 'string') return fallback;\n  return ['1', 'true', 'yes', 'on'].includes(value.toLowerCase());\n}\n\nexport const appConfig: QualiBugAppConfig = {\n  apiBaseUrl: import.meta.env.VITE_QUALIBUG_API_BASE_URL || 'http://127.0.0.1:8790',\n  demoMode: readBoolean(import.meta.env.VITE_QUALIBUG_DEMO_MODE, true),\n  projectId: import.meta.env.VITE_QUALIBUG_PROJECT_ID || 'demo-project',\n};\n""",
    )
    _write_text(
        app_dir / "src/app/dataMode.ts",
        """import { appConfig } from './appConfig';\n\nexport type DataMode = 'demo' | 'real';\n\nexport function resolveDataMode(): DataMode {\n  return appConfig.demoMode ? 'demo' : 'real';\n}\n\nexport function isDemoMode(): boolean {\n  return resolveDataMode() === 'demo';\n}\n\nexport function dataModeLabel(mode: DataMode = resolveDataMode()): string {\n  return mode === 'demo' ? 'demo mode' : 'real API mode';\n}\n""",
    )


def _write_data_source(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/services/qualibugDataSource.ts",
        """import { qualiBugClient } from '../api/qualibugClient';\nimport { demoData } from '../data/demoData';\nimport { appConfig } from '../app/appConfig';\nimport { resolveDataMode, type DataMode } from '../app/dataMode';\n\nexport interface QualiBugViewModel {\n  mode: DataMode;\n  project: Record<string, unknown>;\n  dashboard: Record<string, unknown>;\n  environment: Record<string, unknown>;\n  testPlan: Record<string, unknown>;\n  liveMap: Record<string, unknown>;\n  risks: Record<string, unknown>[];\n  riskDetail: Record<string, unknown>;\n  valueMetrics: Record<string, unknown>;\n  executiveReport: Record<string, unknown>;\n}\n\nexport class QualiBugDataSource {\n  constructor(private readonly projectId = appConfig.projectId) {}\n\n  private demoView(): QualiBugViewModel {\n    return {\n      mode: 'demo',\n      project: demoData.project,\n      dashboard: demoData.dashboard,\n      environment: demoData.environment,\n      testPlan: demoData.test_plan,\n      liveMap: demoData.live_map,\n      risks: demoData.risks as Record<string, unknown>[],\n      riskDetail: demoData.risk_detail,\n      valueMetrics: demoData.value_metrics,\n      executiveReport: demoData.executive_report,\n    };\n  }\n\n  async loadDashboard(): Promise<QualiBugViewModel> {\n    if (resolveDataMode() === 'demo') return this.demoView();\n    const dashboard = await qualiBugClient.getCommandCenter(this.projectId);\n    return { ...this.demoView(), mode: 'real', dashboard };\n  }\n\n  async loadCustomerIntake(): Promise<QualiBugViewModel> {\n    if (resolveDataMode() === 'demo') return this.demoView();\n    const projects = await qualiBugClient.listProjects();\n    return { ...this.demoView(), mode: 'real', project: { projects } };\n  }\n\n  async loadEnvironment(): Promise<QualiBugViewModel> {\n    if (resolveDataMode() === 'demo') return this.demoView();\n    const environment = await qualiBugClient.getEnvironmentReadiness(this.projectId);\n    return { ...this.demoView(), mode: 'real', environment };\n  }\n\n  async runEnvironmentPreflight(): Promise<Record<string, unknown>> {\n    if (resolveDataMode() === 'demo') return { mode: 'demo', accepted: true, message: '演示模式不会触发真实环境写入' };\n    return qualiBugClient.runEnvironmentPreflight(this.projectId);\n  }\n\n  async loadBusinessFlow(): Promise<QualiBugViewModel> {\n    if (resolveDataMode() === 'demo') return this.demoView();\n    const businessModel = await qualiBugClient.getBusinessModel(this.projectId);\n    return { ...this.demoView(), mode: 'real', liveMap: businessModel };\n  }\n\n  async loadTestExecution(): Promise<QualiBugViewModel> {\n    if (resolveDataMode() === 'demo') return this.demoView();\n    const [testPlan, liveMap] = await Promise.all([qualiBugClient.getTestPlan(this.projectId), qualiBugClient.getLiveMap(this.projectId)]);\n    return { ...this.demoView(), mode: 'real', testPlan, liveMap };\n  }\n\n  async generateTestPlan(): Promise<Record<string, unknown>> {\n    if (resolveDataMode() === 'demo') return { mode: 'demo', accepted: true, message: '演示模式生成脱敏测试计划' };\n    return qualiBugClient.generateTestPlan(this.projectId);\n  }\n\n  async startTestRun(): Promise<Record<string, unknown>> {\n    if (resolveDataMode() === 'demo') return { mode: 'demo', accepted: true, message: '演示模式启动虚拟只读测试执行' };\n    return qualiBugClient.startTestRun(this.projectId);\n  }\n\n  async loadRiskEvidence(): Promise<QualiBugViewModel> {\n    if (resolveDataMode() === 'demo') return this.demoView();\n    const risks = await qualiBugClient.listRisks(this.projectId);\n    return { ...this.demoView(), mode: 'real', risks: risks as Record<string, unknown>[] };\n  }\n\n  async loadReportRoi(): Promise<QualiBugViewModel> {\n    if (resolveDataMode() === 'demo') return this.demoView();\n    const [valueMetrics, executiveReport] = await Promise.all([\n      qualiBugClient.getValueMetrics(this.projectId),\n      qualiBugClient.getExecutiveReport(this.projectId),\n    ]);\n    return { ...this.demoView(), mode: 'real', valueMetrics, executiveReport };\n  }\n}\n\nexport const qualiBugDataSource = new QualiBugDataSource();\n""",
    )
    _write_text(
        app_dir / "src/hooks/useQualiBugData.ts",
        """import { useEffect, useState } from 'react';\nimport { qualiBugDataSource, type QualiBugViewModel } from '../services/qualibugDataSource';\n\ntype Loader = () => Promise<QualiBugViewModel>;\n\nexport function useQualiBugData(loader: Loader = () => qualiBugDataSource.loadDashboard()) {\n  const [data, setData] = useState<QualiBugViewModel | null>(null);\n  const [loading, setLoading] = useState(true);\n  const [error, setError] = useState<string | null>(null);\n\n  useEffect(() => {\n    let mounted = true;\n    setLoading(true);\n    loader()\n      .then((payload) => { if (mounted) { setData(payload); setError(null); } })\n      .catch((err: unknown) => { if (mounted) setError(err instanceof Error ? err.message : '加载失败'); })\n      .finally(() => { if (mounted) setLoading(false); });\n    return () => { mounted = false; };\n  }, [loader]);\n\n  return { data, loading, error };\n}\n""",
    )


def _write_components(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/components/DataModeBadge.tsx",
        """import { dataModeLabel, resolveDataMode, type DataMode } from '../app/dataMode';\n\nexport function DataModeBadge({ mode, source }: { mode?: DataMode; source?: string }) {\n  const resolvedMode = mode || resolveDataMode();\n  const label = dataModeLabel(resolvedMode);\n  return <span className={`qb-mode-badge qb-mode-${resolvedMode}`}>{source ? `${label} · ${source}` : label}</span>;\n}\n""",
    )
    _write_text(
        app_dir / "src/components/PageShell.tsx",
        """import type { ReactNode } from 'react';\nimport { DataModeBadge } from './DataModeBadge';\n\nexport function PageShell({ eyebrow, title, subtitle, description, actions, children }: { eyebrow?: string; title: string; subtitle?: string; description?: string; actions?: ReactNode; children: ReactNode }) {\n  const helperText = description || subtitle || '';\n  return (\n    <section className=\"qb-page qb-page-shell\">\n      <div className=\"qb-shell-head\">\n        <div>\n          <span className=\"qb-kicker\">{eyebrow || '组件模型真实化 · 默认脱敏'}</span>\n          <h2>{title}</h2>\n          {helperText ? <p>{helperText}</p> : null}\n        </div>\n        <div className=\"qb-shell-actions\">\n          <DataModeBadge />\n          {actions}\n        </div>\n      </div>\n      {children}\n    </section>\n  );\n}\n""",
    )
    _write_text(
        app_dir / "src/components/KpiRail.tsx",
        """import { MetricCard } from './MetricCard';\nimport type { MetricCardModel } from '../types';\n\nexport function KpiRail({ metrics }: { metrics: MetricCardModel[] }) {\n  return <div className=\"qb-metric-grid qb-kpi-rail\">{metrics.map((metric) => <MetricCard key={metric.label} metric={metric} />)}</div>;\n}\n""",
    )
    _write_text(
        app_dir / "src/components/FlowNodeCard.tsx",
        """export function FlowNodeCard({ node }: { node: Record<string, unknown> }) {\n  const name = String(node.name || node.title || node.label || '业务节点');\n  const status = String(node.status || node.coverage_status || '待确认');\n  const risk = String(node.risk_level || node.risk || 'normal');\n  return (\n    <article className=\"qb-domain-card\">\n      <small>业务流程地图</small>\n      <strong>{name}</strong>\n      <p>覆盖状态：{status}</p>\n      <p>风险级别：{risk}</p>\n    </article>\n  );\n}\n""",
    )
    _write_text(
        app_dir / "src/components/ProbeTable.tsx",
        """export function ProbeTable({ probes = [] }: { probes?: Record<string, unknown>[] }) {\n  return (\n    <div className=\"qb-panel qb-table-panel\">\n      <h3>AI 测试计划 / 实时测试执行</h3>\n      <table className=\"qb-table\">\n        <thead><tr><th>探针</th><th>状态</th><th>链路</th><th>说明</th></tr></thead>\n        <tbody>\n          {probes.slice(0, 8).map((probe, index) => (\n            <tr key={String(probe.id || probe.name || index)}>\n              <td>{String(probe.name || probe.title || `探针 ${index + 1}`)}</td>\n              <td>{String(probe.status || probe.execution_status || 'ready')}</td>\n              <td>{String(probe.flow || probe.business_flow || probe.node || '核心链路')}</td>\n              <td>{String(probe.reason || probe.description || probe.action || '默认只读执行')}</td>\n            </tr>\n          ))}\n        </tbody>\n      </table>\n    </div>\n  );\n}\n""",
    )
    _write_text(
        app_dir / "src/components/RiskList.tsx",
        """export function RiskList({ risks = [] }: { risks?: Record<string, unknown>[] }) {\n  return (\n    <div className=\"qb-risk-list\">\n      {risks.slice(0, 6).map((risk, index) => (\n        <article className=\"qb-panel qb-risk-item\" key={String(risk.risk_id || risk.id || index)}>\n          <small>风险证据链</small>\n          <strong>{String(risk.title || risk.name || `风险 ${index + 1}`)}</strong>\n          <p>{String(risk.business_impact || risk.impact || '影响核心业务链路，需要复验。')}</p>\n        </article>\n      ))}\n    </div>\n  );\n}\n""",
    )
    _write_text(
        app_dir / "src/components/ActionQueue.tsx",
        """export function ActionQueue({ actions = [] }: { actions?: string[] }) {\n  const safeActions = actions.length ? actions : ['确认客户资料', '完成环境诊断', '生成 AI 测试计划', '启动只读执行', '复验风险证据', '生成领导层报告'];\n  return (\n    <div className=\"qb-panel\">\n      <h3>客户下一步动作</h3>\n      <ol className=\"qb-action-queue\">{safeActions.map((action) => <li key={action}>{action}</li>)}</ol>\n    </div>\n  );\n}\n""",
    )


def _write_workbench_page(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/pages/ComponentModelWorkbenchPage.tsx",
        """import { PageShell } from '../components/PageShell';\nimport { KpiRail } from '../components/KpiRail';\nimport { FlowNodeCard } from '../components/FlowNodeCard';\nimport { ProbeTable } from '../components/ProbeTable';\nimport { RiskList } from '../components/RiskList';\nimport { ActionQueue } from '../components/ActionQueue';\nimport { qualiBugDataSource } from '../services/qualibugDataSource';\nimport { useQualiBugData } from '../hooks/useQualiBugData';\n\nexport function ComponentModelWorkbenchPage() {\n  const { data, loading, error } = useQualiBugData(() => qualiBugDataSource.loadTestExecution());\n  const nodes = ((data?.liveMap.nodes || data?.liveMap.flow_nodes || []) as Record<string, unknown>[]);\n  const probes = ((data?.testPlan.executable_probes || data?.testPlan.probes || data?.testPlan.blocked_probes || []) as Record<string, unknown>[]);\n  const risks = (data?.risks || []) as Record<string, unknown>[];\n  const metrics = [\n    { label: '数据模式', value: data?.mode || 'demo', helper: '支持 demo mode / real API mode', status: 'ready' as const },\n    { label: '组件数量', value: 7, helper: 'PageShell / KpiRail / ProbeTable / RiskList 等', status: 'ready' as const },\n    { label: '探针样本', value: probes.length, helper: 'AI 测试计划组件输入', status: 'warning' as const },\n    { label: '风险样本', value: risks.length, helper: '风险证据链组件输入', status: 'blocked' as const },\n  ];\n\n  return (\n    <PageShell title=\"前端组件模型真实化\" subtitle=\"页面不再直接读散落数据，而是通过 QualiBugDataSource + useQualiBugData 进入组件模型。\">\n      {loading ? <div className=\"qb-panel\">加载组件模型...</div> : null}\n      {error ? <div className=\"qb-panel\">{error}</div> : null}\n      <KpiRail metrics={metrics} />\n      <div className=\"qb-page-grid\">{nodes.slice(0, 4).map((node, index) => <FlowNodeCard key={String(node.id || node.name || index)} node={node} />)}</div>\n      <ProbeTable probes={probes} />\n      <RiskList risks={risks} />\n      <ActionQueue />\n    </PageShell>\n  );\n}\n""",
    )


def _patch_routes_and_app(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/routes.ts",
        """import type { QualiBugRoute } from './types';\n\nexport const routes: QualiBugRoute[] = [\n  { path: '/', key: 'dashboard', label: '质量驾驶舱', component: 'DashboardPage' },\n  { path: '/customer-intake', key: 'customer_intake', label: '客户资料导入', component: 'CustomerIntakePage' },\n  { path: '/environment', key: 'environment', label: '环境诊断', component: 'EnvironmentDiagnosisPage' },\n  { path: '/business-flow', key: 'business_flow', label: '业务流程地图', component: 'BusinessFlowMapPage' },\n  { path: '/test-execution', key: 'test_execution', label: 'AI 测试计划 / 实时测试执行', component: 'TestExecutionPage' },\n  { path: '/risk-evidence', key: 'risk_evidence', label: '风险证据链', component: 'RiskEvidencePage' },\n  { path: '/report-roi', key: 'report_roi', label: '领导层报告 / ROI', component: 'ReportRoiPage' },\n  { path: '/component-model', key: 'component_model', label: '前端组件模型', component: 'ComponentModelWorkbenchPage' },\n];\n""",
    )
    _write_text(
        app_dir / "src/App.tsx",
        """import { Sidebar } from './components/Sidebar';\nimport { Topbar } from './components/Topbar';\nimport { DashboardPage } from './pages/DashboardPage';\nimport { CustomerIntakePage } from './pages/CustomerIntakePage';\nimport { EnvironmentDiagnosisPage } from './pages/EnvironmentDiagnosisPage';\nimport { BusinessFlowMapPage } from './pages/BusinessFlowMapPage';\nimport { TestExecutionPage } from './pages/TestExecutionPage';\nimport { RiskEvidencePage } from './pages/RiskEvidencePage';\nimport { ReportRoiPage } from './pages/ReportRoiPage';\nimport { ComponentModelWorkbenchPage } from './pages/ComponentModelWorkbenchPage';\nimport './styles/design-tokens.css';\nimport './styles/app.css';\nimport './styles/component-model.css';\n\nfunction resolvePage(pathname: string) {\n  switch (pathname) {\n    case '/customer-intake': return <CustomerIntakePage />;\n    case '/environment': return <EnvironmentDiagnosisPage />;\n    case '/business-flow': return <BusinessFlowMapPage />;\n    case '/test-execution': return <TestExecutionPage />;\n    case '/risk-evidence': return <RiskEvidencePage />;\n    case '/report-roi': return <ReportRoiPage />;\n    case '/component-model': return <ComponentModelWorkbenchPage />;\n    default: return <DashboardPage />;\n  }\n}\n\nexport function App() {\n  return (\n    <div className=\"qb-app\">\n      <Sidebar />\n      <main className=\"qb-main\">\n        <Topbar />\n        {resolvePage(window.location.pathname)}\n      </main>\n    </div>\n  );\n}\n\nexport default App;\n""",
    )


def _write_component_contract_test(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/__tests__/component-model-contract.test.ts",
        """import { describe, expect, it } from 'vitest';\nimport { routes } from '../routes';\nimport { resolveDataMode, dataModeLabel } from '../app/dataMode';\nimport { QualiBugDataSource } from '../services/qualibugDataSource';\n\ndescribe('Phase106B frontend component model contract', () => {\n  it('adds a component model workbench route without removing product pages', () => {\n    expect(routes.some((route) => route.path === '/component-model')).toBe(true);\n    expect(routes.map((route) => route.label)).toContain('客户资料导入');\n    expect(routes.map((route) => route.label)).toContain('AI 测试计划 / 实时测试执行');\n    expect(routes.map((route) => route.label)).toContain('领导层报告 / ROI');\n  });\n\n  it('keeps data mode explicit for demo mode and real API mode', () => {\n    const mode = resolveDataMode();\n    expect(['demo', 'real']).toContain(mode);\n    expect(['demo mode', 'real API mode']).toContain(dataModeLabel(mode));\n  });\n\n  it('keeps page data behind QualiBugDataSource instead of scattered fetch calls', () => {\n    const dataSource = new QualiBugDataSource('contract-project');\n    expect(dataSource.loadDashboard).toBeTypeOf('function');\n    expect(dataSource.loadEnvironment).toBeTypeOf('function');\n    expect(dataSource.loadTestExecution).toBeTypeOf('function');\n    expect(dataSource.loadRiskEvidence).toBeTypeOf('function');\n    expect(dataSource.loadReportRoi).toBeTypeOf('function');\n  });\n});\n""",
    )


def _write_component_styles(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/styles/component-model.css",
        """.qb-page-shell { gap: 20px; }\n.qb-shell-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; padding: 24px; background: var(--qb-surface); border: 1px solid var(--qb-border); border-radius: var(--qb-radius); box-shadow: var(--qb-shadow); }\n.qb-shell-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }\n.qb-mode-badge { border-radius: 999px; padding: 8px 12px; font-weight: 900; font-size: 12px; letter-spacing: .03em; }\n.qb-mode-demo { background: #e0f2fe; color: #075985; }\n.qb-mode-real { background: #dcfce7; color: #166534; }\n.qb-domain-card { padding: 18px; background: white; border: 1px solid var(--qb-border); border-radius: var(--qb-radius); box-shadow: var(--qb-shadow); display: grid; gap: 8px; }\n.qb-domain-card small, .qb-risk-item small { color: var(--qb-primary); font-weight: 900; text-transform: uppercase; }\n.qb-table-panel { overflow-x: auto; }\n.qb-table { width: 100%; border-collapse: collapse; }\n.qb-table th, .qb-table td { text-align: left; border-bottom: 1px solid var(--qb-border); padding: 10px 8px; vertical-align: top; }\n.qb-table th { color: var(--qb-muted); font-size: 13px; }\n.qb-action-queue { margin: 0; padding-left: 20px; display: grid; gap: 8px; }\n@media (max-width: 720px) { .qb-shell-head { flex-direction: column; } }\n""",
    )


def _write_component_readme(app_dir: Path) -> None:
    component_lines = "\n".join(f"- `{item['name']}`：{item['role']}" for item in _component_inventory())
    _write_text(
        app_dir / "README_FRONTEND_COMPONENT_MODEL.md",
        f"""# Phase106B 前端组件模型真实化\n\nPhase106B 在 Phase106A 的 Vite + React + TypeScript 脚手架基础上，补齐真实前端开发需要的组件模型和数据边界。\n\n## 新增能力\n\n- demo mode / real API mode 显式切换\n- QualiBugDataSource 数据边界\n- useQualiBugData 页面加载 Hook\n- PageShell / KPI / 探针表格 / 风险列表 / 动作队列等业务组件\n- `/component-model` 组件模型工作台\n- component-model contract test\n\n## 组件清单\n\n{component_lines}\n\n## 数据模式\n\n```text\ndemo mode: 使用 src/data/demoData.ts，适合本地演示和售前。\nreal API mode: 通过 src/api/qualibugClient.ts 调 Phase104 API。\n```\n\n## 启动\n\n```powershell\nnpm install\nnpm run dev\n```\n\n打开：\n\n```text\nhttp://127.0.0.1:5173/component-model\n```\n\n## 下一步\n\nPhase106C 应继续接真实 API Client 运行模式，把环境诊断、测试计划、风险证据和报告页逐步切到真实接口。\n""",
    )


def _write_manifest_files(output_dir: Path, report: FrontendComponentModelAcceptanceReport | None = None) -> dict[str, Any]:
    manifest = redact_value(
        {
            "version": PHASE106B_VERSION,
            "generated_at": _now(),
            "app_dir": FRONTEND_APP_DIR,
            "entrypoint": f"{FRONTEND_APP_DIR}/index.html",
            "component_route": "/component-model",
            "component_inventory": _component_inventory(),
            "mode_contract": _mode_contract(),
            "required_files": list(REQUIRED_COMPONENT_MODEL_FILES),
            "core_labels": list(CORE_COMPONENT_MODEL_LABELS),
            "artifacts": {
                "manifest_json": COMPONENT_MODEL_MANIFEST_JSON,
                "manifest_md": COMPONENT_MODEL_MANIFEST_MD,
                "acceptance_json": COMPONENT_MODEL_ACCEPTANCE_JSON,
                "acceptance_md": COMPONENT_MODEL_ACCEPTANCE_MD,
                "checksums": COMPONENT_MODEL_CHECKSUMS,
                "zip": COMPONENT_MODEL_ZIP,
            },
            "acceptance": {"passed": report.passed, "score": report.score, "checks": len(report.checks)} if report else None,
        }
    )
    _write_text(output_dir / COMPONENT_MODEL_MANIFEST_JSON, _json_dump(manifest))
    component_lines = "\n".join(f"- `{item['name']}`：{item['role']}" for item in manifest["component_inventory"])
    _write_text(
        output_dir / COMPONENT_MODEL_MANIFEST_MD,
        f"""# Phase106B Frontend Component Model Manifest\n\n- Version: `{manifest['version']}`\n- App dir: `{manifest['app_dir']}`\n- Entrypoint: `{manifest['entrypoint']}`\n- Component route: `{manifest['component_route']}`\n\n## Component Inventory\n\n{component_lines}\n\n## Data Modes\n\n- `demo mode`：默认脱敏演示数据。\n- `real API mode`：通过 QualiBugDataSource 调用 Phase104 API Client。\n\n## Security\n\n输出扫描 token / cookie / session / client_secret / traceback 等高风险泄露模式。\n""",
    )
    return manifest


def _write_report_files(output_dir: Path, report: FrontendComponentModelAcceptanceReport) -> None:
    payload = report.to_dict()
    _write_text(output_dir / COMPONENT_MODEL_ACCEPTANCE_JSON, _json_dump(payload))
    check_lines = "\n".join(f"- [{'x' if check['passed'] else ' '}] `{check['key']}`：{check['detail']}" for check in payload["checks"])
    _write_text(
        output_dir / COMPONENT_MODEL_ACCEPTANCE_MD,
        f"""# Phase106B Frontend Component Model Acceptance\n\n- Passed: `{payload['passed']}`\n- Score: `{payload['score']}`\n- Version: `{payload['version']}`\n- App dir: `{payload['app_dir']}`\n\n## Checks\n\n{check_lines}\n""",
    )


def scan_frontend_component_model_for_secret_leaks(output_dir: str | Path) -> list[str]:
    root = Path(output_dir)
    leaks: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() in {".zip", ".png", ".jpg", ".jpeg", ".webp"}:
            continue
        text = _read_text(path, limit=300_000)
        for pattern in FORBIDDEN_COMPONENT_MODEL_PATTERNS:
            if pattern in text:
                leaks.append(f"{path.relative_to(root).as_posix()} contains forbidden pattern {pattern}")
    return leaks


def build_frontend_component_model(
    output_dir: str | Path,
    *,
    scenario: str = "manufacturing",
    api_base_url: str = "http://127.0.0.1:8790",
    clean: bool = True,
) -> FrontendComponentModelAcceptanceReport:
    root = Path(output_dir)
    if clean and root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    build_frontend_app_scaffold(root, scenario=scenario, api_base_url=api_base_url, clean=False)
    app_dir = root / FRONTEND_APP_DIR

    _write_app_config(app_dir)
    _write_data_source(app_dir)
    _write_components(app_dir)
    _write_workbench_page(app_dir)
    _patch_routes_and_app(app_dir)
    _write_component_contract_test(app_dir)
    _write_component_styles(app_dir)
    _write_component_readme(app_dir)
    _write_manifest_files(root)

    report = validate_frontend_component_model(root, scenario=scenario, write_report=True, skip_checksum=True)
    _write_manifest_files(root, report)
    write_frontend_component_model_checksums(root)
    _zip_component_model(root)
    report = validate_frontend_component_model(root, scenario=scenario, write_report=True)
    _write_manifest_files(root, report)
    write_frontend_component_model_checksums(root)
    _zip_component_model(root)
    return report


def validate_frontend_component_model(
    output_dir: str | Path,
    *,
    scenario: str = "manufacturing",
    write_report: bool = True,
    skip_checksum: bool = False,
) -> FrontendComponentModelAcceptanceReport:
    root = Path(output_dir)
    app_dir = root / FRONTEND_APP_DIR
    checks: list[FrontendComponentModelCheck] = []

    missing = [relative for relative in REQUIRED_COMPONENT_MODEL_FILES if not (root / relative).exists()]
    if skip_checksum:
        missing = [relative for relative in missing if relative != COMPONENT_MODEL_CHECKSUMS]
    if not skip_checksum and COMPONENT_MODEL_ZIP in missing:
        missing.remove(COMPONENT_MODEL_ZIP)
    checks.append(FrontendComponentModelCheck("required_files", not missing, "组件模型必需文件完整" if not missing else f"缺失文件: {missing}"))

    routes_text = _read_text(app_dir / "src/routes.ts") + _read_text(app_dir / "src/App.tsx")
    route_ok = "path: '/component-model'" in routes_text and "ComponentModelWorkbenchPage" in routes_text and "path: '/test-execution'" in routes_text
    checks.append(FrontendComponentModelCheck("component_route", route_ok, "已接入 /component-model 组件模型工作台" if route_ok else "组件模型路由未接入"))

    component_text = "\n".join(_read_text(app_dir / "src/components" / f"{name}.tsx") for name in REQUIRED_COMPONENTS)
    missing_components = [name for name in REQUIRED_COMPONENTS if f"function {name}" not in component_text and f"export function {name}" not in component_text]
    checks.append(FrontendComponentModelCheck("domain_components", not missing_components, "业务组件模型已生成" if not missing_components else f"缺失组件: {missing_components}"))

    data_source_text = _read_text(app_dir / "src/services/qualibugDataSource.ts")
    missing_methods = [method for method in REQUIRED_DATA_SOURCE_METHODS if f"{method}(" not in data_source_text]
    mode_ok = "resolveDataMode() === 'demo'" in data_source_text and "mode: 'real'" in data_source_text
    checks.append(FrontendComponentModelCheck("data_source_boundary", not missing_methods and mode_ok, "QualiBugDataSource 已隔离 demo mode / real API mode" if not missing_methods and mode_ok else f"数据源边界不完整: {missing_methods}"))

    hook_text = _read_text(app_dir / "src/hooks/useQualiBugData.ts")
    hook_ok = "useEffect" in hook_text and "useState" in hook_text and "QualiBugViewModel" in hook_text
    checks.append(FrontendComponentModelCheck("data_hook", hook_ok, "useQualiBugData 已提供页面数据加载入口" if hook_ok else "useQualiBugData Hook 不完整"))

    workbench_text = _read_text(app_dir / "src/pages/ComponentModelWorkbenchPage.tsx")
    labels_missing = [label for label in ("组件模型真实化", "QualiBugDataSource", "useQualiBugData", "AI 测试计划", "风险证据链") if label not in workbench_text]
    checks.append(FrontendComponentModelCheck("workbench_page", not labels_missing, "组件模型工作台覆盖关键业务语义" if not labels_missing else f"工作台缺失文案: {labels_missing}"))

    contract_text = _read_text(app_dir / "src/__tests__/component-model-contract.test.ts")
    contract_ok = all(keyword in contract_text for keyword in ("/component-model", "demo mode", "real API mode", "QualiBugDataSource", "loadTestExecution", "loadReportRoi"))
    checks.append(FrontendComponentModelCheck("component_contract_test", contract_ok, "已生成组件模型合同测试" if contract_ok else "组件模型合同测试覆盖不足"))

    manifest = _read_json(root / COMPONENT_MODEL_MANIFEST_JSON)
    manifest_ok = manifest.get("version") == PHASE106B_VERSION and len(manifest.get("component_inventory") or []) >= 7 and manifest.get("component_route") == "/component-model"
    checks.append(FrontendComponentModelCheck("manifest", manifest_ok, "manifest 描述组件、数据模式和产物" if manifest_ok else "manifest 内容不完整"))

    if skip_checksum:
        checksum_ok = True
        checksum_detail = "构建中跳过 checksum 复验"
    else:
        checksum_failures = verify_frontend_component_model_checksums(root)
        checksum_ok = not checksum_failures
        checksum_detail = "checksum 复验通过" if checksum_ok else f"checksum 失败: {checksum_failures}"
    checks.append(FrontendComponentModelCheck("checksums", checksum_ok, checksum_detail))

    leaks = scan_frontend_component_model_for_secret_leaks(root)
    checks.append(FrontendComponentModelCheck("secret_leak_scan", not leaks, "未发现高风险敏感信息泄露模式" if not leaks else f"发现泄露风险: {leaks}"))

    passed = all(check.passed for check in checks)
    score = round(sum(1 for check in checks if check.passed) / len(checks) * 100) if checks else 0
    report = FrontendComponentModelAcceptanceReport(
        passed=passed,
        score=score,
        version=PHASE106B_VERSION,
        scenario=scenario,
        output_dir=str(root),
        app_dir=str(app_dir),
        checks=checks,
        artifacts={
            "app_dir": FRONTEND_APP_DIR,
            "entrypoint": f"{FRONTEND_APP_DIR}/index.html",
            "component_route": "/component-model",
            "manifest_json": COMPONENT_MODEL_MANIFEST_JSON,
            "acceptance_json": COMPONENT_MODEL_ACCEPTANCE_JSON,
            "checksums": COMPONENT_MODEL_CHECKSUMS,
            "zip": COMPONENT_MODEL_ZIP,
        },
    )
    if write_report:
        _write_report_files(root, report)
    return report


def run_frontend_component_model_export(
    output_dir: str | Path,
    *,
    scenario: str = "manufacturing",
    api_base_url: str = "http://127.0.0.1:8790",
    validate_only: bool = False,
) -> FrontendComponentModelAcceptanceReport:
    if validate_only:
        return validate_frontend_component_model(output_dir, scenario=scenario, write_report=True)
    return build_frontend_component_model(output_dir, scenario=scenario, api_base_url=api_base_url)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build or validate the Phase106B QualiBug frontend component model.")
    parser.add_argument("--output-dir", default="outputs/phase106_frontend_component_model", help="Output directory")
    parser.add_argument("--scenario", default="manufacturing", help="Seed demo scenario")
    parser.add_argument("--api-base-url", default="http://127.0.0.1:8790", help="Phase104 API base URL")
    parser.add_argument("--validate-only", action="store_true", help="Validate existing component model output")
    args = parser.parse_args(argv)

    report = run_frontend_component_model_export(
        args.output_dir,
        scenario=args.scenario,
        api_base_url=args.api_base_url,
        validate_only=args.validate_only,
    )
    print(_json_dump(report.to_dict()))
    return 0 if report.passed else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
