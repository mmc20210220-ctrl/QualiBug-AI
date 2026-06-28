from __future__ import annotations

"""Phase106D: add project list/detail routing to the generated React frontend.

Phase106C introduced a real API runtime boundary. Phase106D upgrades the app
from a single selected demo project into a project-aware frontend workspace:

* project list route and project detail route
* selected project persistence and current project switcher
* project-scoped API path inventory for Phase104 calls
* demo / real API project list loader with safe fallback
* route guard and contract checks for project-level navigation

The root repository still only receives a Python generator and tests. The React
application is emitted to an output directory so the product can evolve without
introducing npm dependencies at the repository root.
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
from ai_test_asset_center.phase106_frontend_api_runtime import (
    FRONTEND_APP_DIR,
    build_frontend_api_runtime,
)

PHASE106D_VERSION = "phase106d-frontend-project-routes-v1"

PROJECT_ROUTES_MANIFEST_JSON = "frontend_project_routes_manifest.json"
PROJECT_ROUTES_MANIFEST_MD = "frontend_project_routes_manifest.md"
PROJECT_ROUTES_ACCEPTANCE_JSON = "frontend_project_routes_acceptance_report.json"
PROJECT_ROUTES_ACCEPTANCE_MD = "frontend_project_routes_acceptance_report.md"
PROJECT_ROUTES_CHECKSUMS = "CHECKSUMS_PHASE106D.sha256"
PROJECT_ROUTES_ZIP = "phase106_frontend_project_routes.zip"

REQUIRED_PROJECT_ROUTE_FILES: tuple[str, ...] = (
    f"{FRONTEND_APP_DIR}/src/app/projectContext.ts",
    f"{FRONTEND_APP_DIR}/src/services/projectWorkspace.ts",
    f"{FRONTEND_APP_DIR}/src/hooks/useProjectWorkspace.ts",
    f"{FRONTEND_APP_DIR}/src/components/ProjectSwitcher.tsx",
    f"{FRONTEND_APP_DIR}/src/components/ProjectSummaryCard.tsx",
    f"{FRONTEND_APP_DIR}/src/components/ProjectRouteGuard.tsx",
    f"{FRONTEND_APP_DIR}/src/pages/ProjectListPage.tsx",
    f"{FRONTEND_APP_DIR}/src/pages/ProjectDetailPage.tsx",
    f"{FRONTEND_APP_DIR}/src/__tests__/project-routes-contract.test.ts",
    f"{FRONTEND_APP_DIR}/src/styles/project-routes.css",
    f"{FRONTEND_APP_DIR}/README_FRONTEND_PROJECT_ROUTES.md",
    PROJECT_ROUTES_MANIFEST_JSON,
    PROJECT_ROUTES_MANIFEST_MD,
    PROJECT_ROUTES_ACCEPTANCE_JSON,
    PROJECT_ROUTES_ACCEPTANCE_MD,
    PROJECT_ROUTES_CHECKSUMS,
    PROJECT_ROUTES_ZIP,
)

CORE_PROJECT_ROUTE_LABELS: tuple[str, ...] = (
    "项目列表",
    "项目详情",
    "当前项目切换",
    "项目级 API 请求",
    "项目级状态缓存",
    "ProjectWorkspace",
    "ProjectSwitcher",
    "ProjectRouteGuard",
    "demo fallback",
    "real API mode",
    "Phase104 API",
    "默认脱敏",
)

PROJECT_ROUTE_CONTRACT: tuple[dict[str, str], ...] = (
    {"path": "/projects", "component": "ProjectListPage", "purpose": "项目列表、创建项目、当前项目切换"},
    {"path": "/projects/:projectId", "component": "ProjectDetailPage", "purpose": "项目详情、项目级 API 路径、进入核心页面"},
)

PROJECT_SCOPED_API_PATHS: tuple[dict[str, str], ...] = (
    {"method": "GET", "path": "/api/v1/projects", "client": "listProjects", "screen": "项目列表"},
    {"method": "POST", "path": "/api/v1/projects", "client": "createProject", "screen": "创建项目草案"},
    {"method": "GET", "path": "/api/v1/projects/{projectId}/command-center", "client": "getCommandCenter", "screen": "质量驾驶舱"},
    {"method": "GET", "path": "/api/v1/projects/{projectId}/environment/readiness", "client": "getEnvironmentReadiness", "screen": "环境诊断"},
    {"method": "GET", "path": "/api/v1/projects/{projectId}/test-plan", "client": "getTestPlan", "screen": "AI 测试计划"},
    {"method": "GET", "path": "/api/v1/projects/{projectId}/risks", "client": "listRisks", "screen": "风险证据链"},
    {"method": "GET", "path": "/api/v1/projects/{projectId}/reports/executive", "client": "getExecutiveReport", "screen": "领导层报告"},
)

FORBIDDEN_PROJECT_ROUTE_PATTERNS: tuple[str, ...] = (
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
class FrontendProjectRoutesCheck:
    key: str
    passed: bool
    detail: str
    owner: str = "frontend"
    severity: str = "critical"


@dataclass
class FrontendProjectRoutesAcceptanceReport:
    passed: bool
    score: int
    version: str
    scenario: str
    output_dir: str
    app_dir: str
    checks: list[FrontendProjectRoutesCheck] = field(default_factory=list)
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


def _read_text(path: Path, *, limit: int = 400_000) -> str:
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
    excluded = {PROJECT_ROUTES_CHECKSUMS, PROJECT_ROUTES_ZIP}
    return [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and path.name not in excluded
        and not path.name.endswith(".pyc")
        and "node_modules" not in path.parts
    ]


def write_frontend_project_routes_checksums(output_dir: str | Path) -> dict[str, str]:
    root = Path(output_dir)
    checksums = {path.relative_to(root).as_posix(): _sha256(path) for path in _iter_checksum_files(root)}
    lines = [f"{digest}  {relative}" for relative, digest in sorted(checksums.items())]
    _write_text(root / PROJECT_ROUTES_CHECKSUMS, "\n".join(lines) + "\n")
    return checksums


def verify_frontend_project_routes_checksums(output_dir: str | Path) -> list[str]:
    root = Path(output_dir)
    checksum_path = root / PROJECT_ROUTES_CHECKSUMS
    if not checksum_path.exists():
        return [f"missing {PROJECT_ROUTES_CHECKSUMS}"]
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


def _zip_project_routes(output_dir: Path) -> Path:
    archive_path = output_dir / PROJECT_ROUTES_ZIP
    if archive_path.exists():
        archive_path.unlink()
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(output_dir.rglob("*")):
            if path.is_file() and path != archive_path and not path.name.endswith(".pyc") and "node_modules" not in path.parts:
                archive.write(path, path.relative_to(output_dir).as_posix())
    return archive_path


def _write_project_context(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/app/projectContext.ts",
        """import { runtimeConfig } from './runtimeConfig';\n\nexport interface ProjectSummary {\n  projectId: string;\n  projectName: string;\n  customerName: string;\n  industry: string;\n  status: string;\n  launchDecision: string;\n  updatedAt: string;\n  healthScore: number;\n  riskCount: number;\n}\n\nconst SELECTED_PROJECT_KEY = 'qualibug.selectedProjectId';\n\nfunction readString(value: unknown, fallback: string): string {\n  return typeof value === 'string' && value.trim() ? value : fallback;\n}\n\nfunction readNumber(value: unknown, fallback: number): number {\n  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;\n}\n\nexport function normalizeProjectSummary(raw: Record<string, unknown>, fallbackIndex = 0): ProjectSummary {\n  return {\n    projectId: readString(raw.project_id ?? raw.projectId ?? raw.id, fallbackIndex === 0 ? runtimeConfig.projectId : `demo-project-${fallbackIndex}`),\n    projectName: readString(raw.project_name ?? raw.projectName ?? raw.name, '未命名质量评估项目'),\n    customerName: readString(raw.customer_name ?? raw.customerName ?? raw.customer, '未命名客户'),\n    industry: readString(raw.industry, 'unknown'),\n    status: readString(raw.status, 'draft'),\n    launchDecision: readString(raw.launch_decision ?? raw.launchDecision ?? raw.recommendation, '待评估'),\n    updatedAt: readString(raw.updated_at ?? raw.updatedAt, new Date().toISOString()),\n    healthScore: readNumber(raw.health_score ?? raw.healthScore, 0),\n    riskCount: readNumber(raw.risk_count ?? raw.riskCount, 0),\n  };\n}\n\nexport function readSelectedProjectId(fallbackProjectId = runtimeConfig.projectId): string {\n  try {\n    return window.localStorage.getItem(SELECTED_PROJECT_KEY) || fallbackProjectId;\n  } catch {\n    return fallbackProjectId;\n  }\n}\n\nexport function persistSelectedProjectId(projectId: string): void {\n  try {\n    window.localStorage.setItem(SELECTED_PROJECT_KEY, projectId);\n  } catch {\n    // ignore storage failures in embedded customer preview.\n  }\n}\n\nexport function projectScopedApiPath(path: string, projectId: string): string {\n  return path.replace('{projectId}', encodeURIComponent(projectId));\n}\n""",
    )


def _write_project_workspace_service(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/services/projectWorkspace.ts",
        """import { qualiBugClient } from '../api/qualibugClient';\nimport { demoData } from '../data/demoData';\nimport { runtimeConfig } from '../app/runtimeConfig';\nimport { redactApiError, loadRuntimeHealth, type RuntimeHealth } from '../api/runtimeApi';\nimport { normalizeProjectSummary, projectScopedApiPath, type ProjectSummary } from '../app/projectContext';\n\nexport interface ProjectWorkspaceResult {\n  source: 'demo-data' | 'phase104-api' | 'demo-fallback';\n  mode: 'demo' | 'real';\n  projects: ProjectSummary[];\n  runtimeHealth: RuntimeHealth;\n  error?: Record<string, unknown>;\n}\n\n// demo fallback keeps project list usable when real API mode is offline.\nexport const projectScopedApiPaths = [\n  { method: 'GET', path: '/api/v1/projects/{projectId}/command-center', client: 'getCommandCenter', screen: '质量驾驶舱' },\n  { method: 'GET', path: '/api/v1/projects/{projectId}/environment/readiness', client: 'getEnvironmentReadiness', screen: '环境诊断' },\n  { method: 'GET', path: '/api/v1/projects/{projectId}/business-model', client: 'getBusinessModel', screen: '业务流程地图' },\n  { method: 'GET', path: '/api/v1/projects/{projectId}/test-plan', client: 'getTestPlan', screen: 'AI 测试计划' },\n  { method: 'GET', path: '/api/v1/projects/{projectId}/live-map', client: 'getLiveMap', screen: '实时执行' },\n  { method: 'GET', path: '/api/v1/projects/{projectId}/risks', client: 'listRisks', screen: '风险证据链' },\n  { method: 'GET', path: '/api/v1/projects/{projectId}/reports/executive', client: 'getExecutiveReport', screen: '领导层报告' },\n];\n\nexport function demoProjects(): ProjectSummary[] {\n  const primary = normalizeProjectSummary({\n    ...demoData.project,\n    health_score: demoData.demo_summary.quality_score,\n    risk_count: demoData.demo_summary.risk_count,\n    launch_decision: demoData.demo_summary.launch_recommendation,\n  });\n  return [\n    primary,\n    normalizeProjectSummary({\n      project_id: 'demo_retail_payment_v2',\n      project_name: '零售支付链路上线评估',\n      customer_name: '某零售集团',\n      industry: 'retail',\n      status: 'environment_ready',\n      launch_decision: '需完成风险复验',\n      health_score: 76,\n      risk_count: 4,\n      updated_at: demoData.generated_at,\n    }, 1),\n    normalizeProjectSummary({\n      project_id: 'demo_finance_core_banking',\n      project_name: '核心账务系统灰度质量评估',\n      customer_name: '某金融客户',\n      industry: 'finance',\n      status: 'test_running',\n      launch_decision: '暂不建议上线',\n      health_score: 68,\n      risk_count: 7,\n      updated_at: demoData.generated_at,\n    }, 2),\n  ];\n}\n\nexport function findProject(projects: ProjectSummary[], projectId: string): ProjectSummary | undefined {\n  return projects.find((project) => project.projectId === projectId) || projects[0];\n}\n\nexport function scopedPathsForProject(projectId: string) {\n  return projectScopedApiPaths.map((item) => ({ ...item, resolvedPath: projectScopedApiPath(item.path, projectId) }));\n}\n\nexport class ProjectWorkspace {\n  async listProjects(): Promise<ProjectWorkspaceResult> {\n    const runtimeHealth = await loadRuntimeHealth();\n    if (runtimeConfig.demoMode) {\n      return { mode: 'demo', source: 'demo-data', projects: demoProjects(), runtimeHealth };\n    }\n    try {\n      const rawProjects = await qualiBugClient.listProjects();\n      const projects = Array.isArray(rawProjects) ? rawProjects.map((item, index) => normalizeProjectSummary(item as Record<string, unknown>, index)) : demoProjects();\n      return { mode: 'real', source: 'phase104-api', projects, runtimeHealth: { ...runtimeHealth, online: true } };\n    } catch (error) {\n      if (!runtimeConfig.fallbackToDemo) throw error;\n      return {\n        mode: 'real',\n        source: 'demo-fallback',\n        projects: demoProjects(),\n        runtimeHealth: { ...runtimeHealth, online: false },\n        error: redactApiError(error),\n      };\n    }\n  }\n\n  async createProjectDraft(payload: Record<string, unknown>): Promise<Record<string, unknown>> {\n    const runtimeHealth = await loadRuntimeHealth();\n    if (runtimeConfig.demoMode) {\n      return { accepted: true, source: 'demo-data', runtimeHealth, project: normalizeProjectSummary(payload, 9) };\n    }\n    try {\n      const project = await qualiBugClient.createProject(payload);\n      return { accepted: true, source: 'phase104-api', runtimeHealth, project };\n    } catch (error) {\n      if (!runtimeConfig.fallbackToDemo) throw error;\n      return { accepted: true, source: 'demo-fallback', runtimeHealth, error: redactApiError(error), project: normalizeProjectSummary(payload, 9) };\n    }\n  }\n}\n\nexport const projectWorkspace = new ProjectWorkspace();\n""",
    )


def _write_project_hook(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/hooks/useProjectWorkspace.ts",
        """import { useCallback, useEffect, useMemo, useState } from 'react';\nimport { readSelectedProjectId, persistSelectedProjectId, type ProjectSummary } from '../app/projectContext';\nimport { findProject, projectWorkspace, scopedPathsForProject, type ProjectWorkspaceResult } from '../services/projectWorkspace';\n\nexport function useProjectWorkspace() {\n  const [result, setResult] = useState<ProjectWorkspaceResult | null>(null);\n  const [loading, setLoading] = useState(true);\n  const [error, setError] = useState<string | null>(null);\n  const [selectedProjectId, setSelectedProjectIdState] = useState(readSelectedProjectId());\n\n  const refresh = useCallback(() => {\n    setLoading(true);\n    projectWorkspace.listProjects()\n      .then((payload) => {\n        setResult(payload);\n        setError(null);\n        const candidate = findProject(payload.projects, selectedProjectId);\n        if (candidate && candidate.projectId !== selectedProjectId) {\n          setSelectedProjectIdState(candidate.projectId);\n          persistSelectedProjectId(candidate.projectId);\n        }\n      })\n      .catch((err: unknown) => setError(err instanceof Error ? err.message : '项目工作区加载失败'))\n      .finally(() => setLoading(false));\n  }, [selectedProjectId]);\n\n  useEffect(() => {\n    refresh();\n  }, [refresh]);\n\n  const selectProject = useCallback((projectId: string) => {\n    setSelectedProjectIdState(projectId);\n    persistSelectedProjectId(projectId);\n  }, []);\n\n  const currentProject: ProjectSummary | undefined = useMemo(() => {\n    return result ? findProject(result.projects, selectedProjectId) : undefined;\n  }, [result, selectedProjectId]);\n\n  const scopedApiPaths = useMemo(() => scopedPathsForProject(currentProject?.projectId || selectedProjectId), [currentProject, selectedProjectId]);\n\n  return {\n    loading,\n    error,\n    projects: result?.projects || [],\n    currentProject,\n    selectedProjectId,\n    selectProject,\n    refresh,\n    source: result?.source,\n    mode: result?.mode,\n    runtimeHealth: result?.runtimeHealth,\n    scopedApiPaths,\n  };\n}\n""",
    )


def _write_project_components(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/components/ProjectSummaryCard.tsx",
        """import type { ProjectSummary } from '../app/projectContext';\n\nexport function ProjectSummaryCard({ project, active, onSelect }: { project: ProjectSummary; active?: boolean; onSelect?: (projectId: string) => void }) {\n  return (\n    <article className={`qb-project-card ${active ? 'active' : ''}`}>\n      <div className=\"qb-project-card__head\">\n        <div>\n          <span className=\"qb-kicker\">{project.customerName} · {project.industry}</span>\n          <h3>{project.projectName}</h3>\n        </div>\n        <strong>{project.healthScore}</strong>\n      </div>\n      <p>状态：{project.status} · 风险数：{project.riskCount} · 上线建议：{project.launchDecision}</p>\n      <div className=\"qb-project-card__actions\">\n        <a href={`/projects/${project.projectId}`}>进入项目详情</a>\n        <button type=\"button\" onClick={() => onSelect?.(project.projectId)}>设为当前项目</button>\n      </div>\n    </article>\n  );\n}\n""",
    )
    _write_text(
        app_dir / "src/components/ProjectSwitcher.tsx",
        """import type { ProjectSummary } from '../app/projectContext';\n\nexport function ProjectSwitcher({ projects, selectedProjectId, onSelect }: { projects: ProjectSummary[]; selectedProjectId: string; onSelect: (projectId: string) => void }) {\n  return (\n    <label className=\"qb-project-switcher\">\n      <span>当前项目切换</span>\n      <select value={selectedProjectId} onChange={(event) => onSelect(event.target.value)}>\n        {projects.map((project) => (\n          <option key={project.projectId} value={project.projectId}>{project.projectName}</option>\n        ))}\n      </select>\n    </label>\n  );\n}\n""",
    )
    _write_text(
        app_dir / "src/components/ProjectRouteGuard.tsx",
        """import type { PropsWithChildren } from 'react';\n\nexport function ProjectRouteGuard({ ready, message, children }: PropsWithChildren<{ ready: boolean; message?: string }>) {\n  if (!ready) {\n    return (\n      <section className=\"qb-project-guard\">\n        <h2>项目级状态缓存准备中</h2>\n        <p>{message || '正在加载项目列表和当前项目上下文。'}</p>\n        <a href=\"/projects\">返回项目列表</a>\n      </section>\n    );\n  }\n  return <>{children}</>;\n}\n""",
    )


def _write_project_pages(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/pages/ProjectListPage.tsx",
        """import { useState } from 'react';\nimport { DataModeBadge } from '../components/DataModeBadge';\nimport { PageShell } from '../components/PageShell';\nimport { ProjectSwitcher } from '../components/ProjectSwitcher';\nimport { ProjectSummaryCard } from '../components/ProjectSummaryCard';\nimport { projectWorkspace } from '../services/projectWorkspace';\nimport { useProjectWorkspace } from '../hooks/useProjectWorkspace';\n\nexport function ProjectListPage() {\n  const workspace = useProjectWorkspace();\n  const [lastCreateResult, setLastCreateResult] = useState<string | null>(null);\n\n  async function createDraftProject() {\n    const result = await projectWorkspace.createProjectDraft({\n      project_id: `draft_${Date.now()}`,\n      project_name: '新客户上线质量评估草案',\n      customer_name: '待补充客户',\n      industry: 'unknown',\n      status: 'draft',\n      launch_decision: '待环境诊断',\n    });\n    setLastCreateResult(`创建项目草案已接收：${String(result.source)}`);\n    workspace.refresh();\n  }\n\n  return (\n    <PageShell title=\"项目列表\" subtitle=\"真实项目维度入口：项目列表、创建项目、当前项目切换、项目级状态缓存。\">\n      <div className=\"qb-project-toolbar\">\n        <DataModeBadge mode={workspace.mode || 'demo'} />\n        <ProjectSwitcher projects={workspace.projects} selectedProjectId={workspace.selectedProjectId} onSelect={workspace.selectProject} />\n        <button type=\"button\" onClick={createDraftProject}>创建项目草案</button>\n      </div>\n      {workspace.error && <p className=\"qb-error\">{workspace.error}</p>}\n      {lastCreateResult && <p className=\"qb-success\">{lastCreateResult}</p>}\n      <section className=\"qb-project-grid\">\n        {workspace.projects.map((project) => (\n          <ProjectSummaryCard key={project.projectId} project={project} active={project.projectId === workspace.selectedProjectId} onSelect={workspace.selectProject} />\n        ))}\n      </section>\n      <section className=\"qb-panel\">\n        <h3>项目级 API 请求</h3>\n        <p>每个核心页面后续都使用当前项目 ID 调用 Phase104 API，避免多个客户项目数据混用。</p>\n      </section>\n    </PageShell>\n  );\n}\n""",
    )
    _write_text(
        app_dir / "src/pages/ProjectDetailPage.tsx",
        """import { DataModeBadge } from '../components/DataModeBadge';\nimport { PageShell } from '../components/PageShell';\nimport { ProjectRouteGuard } from '../components/ProjectRouteGuard';\nimport { ProjectSwitcher } from '../components/ProjectSwitcher';\nimport { useProjectWorkspace } from '../hooks/useProjectWorkspace';\n\nfunction currentProjectIdFromPath(): string {\n  const parts = window.location.pathname.split('/').filter(Boolean);\n  return parts[0] === 'projects' && parts[1] ? decodeURIComponent(parts[1]) : '';\n}\n\nexport function ProjectDetailPage() {\n  const workspace = useProjectWorkspace();\n  const routeProjectId = currentProjectIdFromPath();\n  const project = workspace.projects.find((item) => item.projectId === routeProjectId) || workspace.currentProject;\n\n  return (\n    <PageShell title=\"项目详情\" subtitle=\"项目级路由：每个页面、每个 API 请求都绑定当前项目上下文。\">\n      <ProjectRouteGuard ready={!workspace.loading && Boolean(project)} message=\"没有找到该项目，请先回到项目列表选择当前项目。\">\n        {project && (\n          <>\n            <div className=\"qb-project-toolbar\">\n              <DataModeBadge mode={workspace.mode || 'demo'} />\n              <ProjectSwitcher projects={workspace.projects} selectedProjectId={project.projectId} onSelect={workspace.selectProject} />\n              <a href=\"/projects\">返回项目列表</a>\n            </div>\n            <section className=\"qb-project-hero\">\n              <span className=\"qb-kicker\">{project.customerName} · {project.industry}</span>\n              <h2>{project.projectName}</h2>\n              <p>项目 ID：<code>{project.projectId}</code></p>\n              <p>上线建议：{project.launchDecision} · 风险数：{project.riskCount} · 健康分：{project.healthScore}</p>\n            </section>\n            <section className=\"qb-project-actions\">\n              <a href=\"/environment\">进入环境诊断</a>\n              <a href=\"/test-execution\">进入 AI 测试执行</a>\n              <a href=\"/risk-evidence\">进入风险证据链</a>\n              <a href=\"/report-roi\">进入领导报告 / ROI</a>\n            </section>\n            <section className=\"qb-panel\">\n              <h3>项目级 API 路径清单</h3>\n              <div className=\"qb-api-paths\">\n                {workspace.scopedApiPaths.map((item) => (\n                  <article key={item.path}>\n                    <strong>{item.screen}</strong>\n                    <code>{item.method} {item.resolvedPath}</code>\n                    <small>{item.client}</small>\n                  </article>\n                ))}\n              </div>\n            </section>\n          </>\n        )}\n      </ProjectRouteGuard>\n    </PageShell>\n  );\n}\n""",
    )


def _patch_app_and_routes(app_dir: Path) -> None:
    app_path = app_dir / "src/App.tsx"
    app = _read_text(app_path)
    if "ProjectListPage" not in app:
        app = app.replace("import { ApiRuntimeWorkbenchPage } from './pages/ApiRuntimeWorkbenchPage';", "import { ApiRuntimeWorkbenchPage } from './pages/ApiRuntimeWorkbenchPage';\nimport { ProjectListPage } from './pages/ProjectListPage';\nimport { ProjectDetailPage } from './pages/ProjectDetailPage';")
    if "project-routes.css" not in app:
        app = app.replace("import './styles/api-runtime.css';", "import './styles/api-runtime.css';\nimport './styles/project-routes.css';")
    if "pathname.startsWith('/projects/')" not in app:
        app = app.replace(
            "function resolvePage(pathname: string) {\n  switch (pathname) {",
            "function resolvePage(pathname: string) {\n  if (pathname.startsWith('/projects/')) return <ProjectDetailPage />;\n  switch (pathname) {",
        )
        app = app.replace("    case '/customer-intake': return <CustomerIntakePage />;", "    case '/projects': return <ProjectListPage />;\n    case '/customer-intake': return <CustomerIntakePage />;")
    _write_text(app_path, app)

    routes_path = app_dir / "src/routes.ts"
    routes = _read_text(routes_path)
    if "key: 'projects'" not in routes:
        routes = routes.replace(
            "  { path: '/', key: 'dashboard', label: '质量驾驶舱', component: 'DashboardPage' },",
            "  { path: '/', key: 'dashboard', label: '质量驾驶舱', component: 'DashboardPage' },\n  { path: '/projects', key: 'projects', label: '项目列表', component: 'ProjectListPage' },\n  { path: '/projects/:projectId', key: 'project_detail', label: '项目详情', component: 'ProjectDetailPage' },",
        )
    _write_text(routes_path, routes)

    demo_path = app_dir / "src/data/demoData.ts"
    demo = _read_text(demo_path)
    if "projectRouteInventory" not in demo:
        demo = demo.replace(
            "export const routeInventory = demoData.routes;",
            "export const projectRouteInventory = [\n  { path: '/projects', key: 'projects', label: '项目列表', component: 'ProjectListPage' },\n  { path: '/projects/:projectId', key: 'project_detail', label: '项目详情', component: 'ProjectDetailPage' },\n] as const;\n\nexport const routeInventory = [...projectRouteInventory, ...demoData.routes];",
        )
    _write_text(demo_path, demo)


def _write_project_styles(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/styles/project-routes.css",
        """.qb-project-toolbar {\n  display: flex;\n  flex-wrap: wrap;\n  gap: 12px;\n  align-items: center;\n  margin-bottom: 18px;\n}\n.qb-project-switcher {\n  display: inline-flex;\n  gap: 8px;\n  align-items: center;\n  padding: 8px 10px;\n  border: 1px solid var(--qb-border);\n  border-radius: 12px;\n  background: var(--qb-surface);\n}\n.qb-project-switcher select {\n  min-width: 260px;\n}\n.qb-project-grid {\n  display: grid;\n  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));\n  gap: 16px;\n}\n.qb-project-card, .qb-project-hero, .qb-project-guard, .qb-panel {\n  border: 1px solid var(--qb-border);\n  border-radius: 18px;\n  background: var(--qb-surface);\n  padding: 18px;\n  box-shadow: var(--qb-shadow);\n}\n.qb-project-card.active {\n  outline: 2px solid var(--qb-accent);\n}\n.qb-project-card__head {\n  display: flex;\n  justify-content: space-between;\n  gap: 12px;\n}\n.qb-project-card__head strong {\n  font-size: 32px;\n}\n.qb-project-card__actions, .qb-project-actions {\n  display: flex;\n  flex-wrap: wrap;\n  gap: 10px;\n  margin-top: 14px;\n}\n.qb-project-actions a, .qb-project-card__actions a, .qb-project-card__actions button, .qb-project-toolbar button {\n  border: 1px solid var(--qb-border);\n  border-radius: 999px;\n  padding: 8px 12px;\n  text-decoration: none;\n  background: var(--qb-surface-muted);\n}\n.qb-api-paths {\n  display: grid;\n  gap: 10px;\n}\n.qb-api-paths article {\n  display: grid;\n  gap: 4px;\n  padding: 12px;\n  border-radius: 12px;\n  background: var(--qb-surface-muted);\n}\n.qb-success { color: #047857; }\n.qb-error { color: #b91c1c; }\n""",
    )


def _write_project_contract_test(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/__tests__/project-routes-contract.test.ts",
        """import { routes } from '../routes';\nimport { projectRouteInventory } from '../data/demoData';\nimport { normalizeProjectSummary, projectScopedApiPath } from '../app/projectContext';\nimport { ProjectWorkspace, projectScopedApiPaths } from '../services/projectWorkspace';\n\ndescribe('Phase106D project route contract', () => {\n  it('registers project list and project detail routes', () => {\n    expect(routes.some((route) => route.path === '/projects' && route.component === 'ProjectListPage')).toBe(true);\n    expect(routes.some((route) => route.path === '/projects/:projectId' && route.component === 'ProjectDetailPage')).toBe(true);\n    expect(projectRouteInventory.length).toBe(2);\n  });\n\n  it('normalizes project summaries and project-scoped API paths', () => {\n    const project = normalizeProjectSummary({ project_id: 'p-1', project_name: '项目详情验收', customer_name: '客户A', health_score: 88, risk_count: 2 });\n    expect(project.projectId).toBe('p-1');\n    expect(projectScopedApiPath('/api/v1/projects/{projectId}/risks', project.projectId)).toContain('/projects/p-1/risks');\n    expect(projectScopedApiPaths.some((item) => item.client === 'getExecutiveReport')).toBe(true);\n  });\n\n  it('exposes ProjectWorkspace for demo fallback and real API mode', () => {\n    const workspace = new ProjectWorkspace();\n    expect(workspace.listProjects).toBeInstanceOf(Function);\n    expect(workspace.createProjectDraft).toBeInstanceOf(Function);\n  });\n});\n""",
    )


def _write_project_readme(app_dir: Path) -> None:
    _write_text(
        app_dir / "README_FRONTEND_PROJECT_ROUTES.md",
        """# Phase106D Frontend Project Routes\n\n本目录由 `ai_test_asset_center.phase106_frontend_project_routes` 生成，用于把 Phase106C 的真实 API runtime 前端升级为项目级工作区。\n\n## 新增能力\n\n- `/projects`：项目列表、创建项目草案、当前项目切换。\n- `/projects/:projectId`：项目详情、项目级 API 请求清单、进入核心页面。\n- `ProjectWorkspace`：统一封装 demo mode / real API mode / demo fallback。\n- `useProjectWorkspace`：项目级状态缓存和当前项目持久化。\n- `ProjectSwitcher`：客户现场多项目切换。\n\n## 运行\n\n```powershell\ncd frontend_app\nnpm install\nnpm run dev\n```\n\n打开：`http://127.0.0.1:5173/projects`。\n\n## 安全说明\n\n项目路由只展示项目摘要和项目级 API 路径，不展示原始凭证、会话、密钥或客户敏感字段。默认仍保留 demo fallback，真实 API 不可用时不影响演示。\n""",
    )


def _write_manifest_files(output_dir: Path, report: FrontendProjectRoutesAcceptanceReport | None = None) -> dict[str, Any]:
    manifest = redact_value(
        {
            "version": PHASE106D_VERSION,
            "generated_at": _now(),
            "app_dir": FRONTEND_APP_DIR,
            "entrypoint": f"{FRONTEND_APP_DIR}/index.html",
            "project_routes": list(PROJECT_ROUTE_CONTRACT),
            "project_scoped_api_paths": list(PROJECT_SCOPED_API_PATHS),
            "required_files": list(REQUIRED_PROJECT_ROUTE_FILES),
            "core_labels": list(CORE_PROJECT_ROUTE_LABELS),
            "artifacts": {
                "manifest_json": PROJECT_ROUTES_MANIFEST_JSON,
                "manifest_md": PROJECT_ROUTES_MANIFEST_MD,
                "acceptance_json": PROJECT_ROUTES_ACCEPTANCE_JSON,
                "acceptance_md": PROJECT_ROUTES_ACCEPTANCE_MD,
                "checksums": PROJECT_ROUTES_CHECKSUMS,
                "zip": PROJECT_ROUTES_ZIP,
            },
            "acceptance": {"passed": report.passed, "score": report.score, "checks": len(report.checks)} if report else None,
        }
    )
    _write_text(output_dir / PROJECT_ROUTES_MANIFEST_JSON, _json_dump(manifest))
    route_lines = "\n".join(f"- `{item['path']}` → `{item['component']}`：{item['purpose']}" for item in manifest["project_routes"])
    api_lines = "\n".join(f"- `{item['method']} {item['path']}` → `{item['client']}` / {item['screen']}" for item in manifest["project_scoped_api_paths"])
    _write_text(
        output_dir / PROJECT_ROUTES_MANIFEST_MD,
        f"""# Phase106D Frontend Project Routes Manifest\n\n- Version: `{manifest['version']}`\n- App dir: `{manifest['app_dir']}`\n- Entrypoint: `{manifest['entrypoint']}`\n\n## Project Routes\n\n{route_lines}\n\n## Project-scoped API Paths\n\n{api_lines}\n\n## Security\n\n默认脱敏，保留 demo fallback，项目级 API 请求绑定当前项目 ID。\n""",
    )
    return manifest


def _write_report_files(output_dir: Path, report: FrontendProjectRoutesAcceptanceReport) -> None:
    payload = report.to_dict()
    _write_text(output_dir / PROJECT_ROUTES_ACCEPTANCE_JSON, _json_dump(payload))
    check_lines = "\n".join(f"- [{'x' if check['passed'] else ' '}] `{check['key']}`：{check['detail']}" for check in payload["checks"])
    _write_text(
        output_dir / PROJECT_ROUTES_ACCEPTANCE_MD,
        f"""# Phase106D Frontend Project Routes Acceptance\n\n- Passed: `{payload['passed']}`\n- Score: `{payload['score']}`\n- Version: `{payload['version']}`\n- App dir: `{payload['app_dir']}`\n\n## Checks\n\n{check_lines}\n""",
    )


def scan_frontend_project_routes_for_secret_leaks(output_dir: str | Path) -> list[str]:
    root = Path(output_dir)
    leaks: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() in {".zip", ".png", ".jpg", ".jpeg", ".webp"}:
            continue
        text = _read_text(path, limit=400_000)
        for pattern in FORBIDDEN_PROJECT_ROUTE_PATTERNS:
            if pattern in text:
                leaks.append(f"{path.relative_to(root).as_posix()} contains forbidden pattern {pattern}")
    return leaks


def build_frontend_project_routes(
    output_dir: str | Path,
    *,
    scenario: str = "manufacturing",
    api_base_url: str = "http://127.0.0.1:8790",
    clean: bool = True,
) -> FrontendProjectRoutesAcceptanceReport:
    root = Path(output_dir)
    if clean and root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    build_frontend_api_runtime(root, scenario=scenario, api_base_url=api_base_url, clean=False)
    app_dir = root / FRONTEND_APP_DIR

    _write_project_context(app_dir)
    _write_project_workspace_service(app_dir)
    _write_project_hook(app_dir)
    _write_project_components(app_dir)
    _write_project_pages(app_dir)
    _patch_app_and_routes(app_dir)
    _write_project_styles(app_dir)
    _write_project_contract_test(app_dir)
    _write_project_readme(app_dir)
    _write_manifest_files(root)

    report = validate_frontend_project_routes(root, scenario=scenario, write_report=True, skip_checksum=True)
    _write_manifest_files(root, report)
    write_frontend_project_routes_checksums(root)
    _zip_project_routes(root)
    report = validate_frontend_project_routes(root, scenario=scenario, write_report=True)
    _write_manifest_files(root, report)
    write_frontend_project_routes_checksums(root)
    _zip_project_routes(root)
    return report


def validate_frontend_project_routes(
    output_dir: str | Path,
    *,
    scenario: str = "manufacturing",
    write_report: bool = True,
    skip_checksum: bool = False,
) -> FrontendProjectRoutesAcceptanceReport:
    root = Path(output_dir)
    app_dir = root / FRONTEND_APP_DIR
    checks: list[FrontendProjectRoutesCheck] = []

    missing = [relative for relative in REQUIRED_PROJECT_ROUTE_FILES if not (root / relative).exists()]
    if skip_checksum:
        missing = [relative for relative in missing if relative != PROJECT_ROUTES_CHECKSUMS]
    if not skip_checksum and PROJECT_ROUTES_ZIP in missing:
        missing.remove(PROJECT_ROUTES_ZIP)
    checks.append(FrontendProjectRoutesCheck("required_files", not missing, "项目路由必需文件完整" if not missing else f"缺失文件: {missing}"))

    routes_text = _read_text(app_dir / "src/routes.ts")
    routes_ok = "'/projects'" in routes_text and "'/projects/:projectId'" in routes_text and "ProjectListPage" in routes_text and "ProjectDetailPage" in routes_text
    checks.append(FrontendProjectRoutesCheck("project_routes", routes_ok, "已注册 /projects 与 /projects/:projectId 路由" if routes_ok else "项目路由未完整注册"))

    app_text = _read_text(app_dir / "src/App.tsx")
    app_ok = "pathname.startsWith('/projects/')" in app_text and "ProjectListPage" in app_text and "ProjectDetailPage" in app_text and "project-routes.css" in app_text
    checks.append(FrontendProjectRoutesCheck("app_route_resolution", app_ok, "App 已支持项目列表和项目详情解析" if app_ok else "App 路由解析不完整"))

    context_text = _read_text(app_dir / "src/app/projectContext.ts")
    context_ok = all(keyword in context_text for keyword in ("ProjectSummary", "normalizeProjectSummary", "readSelectedProjectId", "persistSelectedProjectId", "projectScopedApiPath"))
    checks.append(FrontendProjectRoutesCheck("project_context", context_ok, "项目上下文支持当前项目持久化和项目级 API 路径" if context_ok else "项目上下文不完整"))

    service_text = _read_text(app_dir / "src/services/projectWorkspace.ts")
    service_ok = all(keyword in service_text for keyword in ("ProjectWorkspace", "listProjects", "createProjectDraft", "demo fallback", "phase104-api", "projectScopedApiPaths", "getExecutiveReport"))
    checks.append(FrontendProjectRoutesCheck("project_workspace_service", service_ok, "ProjectWorkspace 已支持 demo/real API/fallback 和项目级 API 清单" if service_ok else "ProjectWorkspace 不完整"))

    hook_text = _read_text(app_dir / "src/hooks/useProjectWorkspace.ts")
    hook_ok = all(keyword in hook_text for keyword in ("useProjectWorkspace", "selectedProjectId", "selectProject", "scopedApiPaths", "refresh"))
    checks.append(FrontendProjectRoutesCheck("project_workspace_hook", hook_ok, "useProjectWorkspace 已提供项目级状态缓存" if hook_ok else "项目 Hook 不完整"))

    pages_text = _read_text(app_dir / "src/pages/ProjectListPage.tsx") + _read_text(app_dir / "src/pages/ProjectDetailPage.tsx")
    missing_labels = [label for label in ("项目列表", "项目详情", "当前项目切换", "项目级 API 请求", "创建项目草案", "Phase104 API") if label not in pages_text]
    checks.append(FrontendProjectRoutesCheck("project_pages_semantics", not missing_labels, "项目页面覆盖关键业务语义" if not missing_labels else f"项目页面缺失文案: {missing_labels}"))

    components_text = "\n".join(_read_text(app_dir / relative) for relative in ("src/components/ProjectSwitcher.tsx", "src/components/ProjectSummaryCard.tsx", "src/components/ProjectRouteGuard.tsx"))
    components_ok = all(keyword in components_text for keyword in ("ProjectSwitcher", "ProjectSummaryCard", "ProjectRouteGuard", "设为当前项目", "项目级状态缓存"))
    checks.append(FrontendProjectRoutesCheck("project_components", components_ok, "项目切换、项目卡片和路由守卫组件完整" if components_ok else "项目组件不完整"))

    demo_text = _read_text(app_dir / "src/data/demoData.ts")
    sidebar_ok = "projectRouteInventory" in demo_text and "routeInventory = [...projectRouteInventory" in demo_text
    checks.append(FrontendProjectRoutesCheck("sidebar_inventory", sidebar_ok, "侧边栏 routeInventory 已注入项目入口" if sidebar_ok else "侧边栏未注入项目入口"))

    contract_test = _read_text(app_dir / "src/__tests__/project-routes-contract.test.ts")
    contract_ok = all(keyword in contract_test for keyword in ("/projects", "/projects/:projectId", "ProjectWorkspace", "normalizeProjectSummary", "projectScopedApiPath"))
    checks.append(FrontendProjectRoutesCheck("project_contract_test", contract_ok, "已生成项目路由合同测试" if contract_ok else "项目合同测试覆盖不足"))

    manifest = _read_json(root / PROJECT_ROUTES_MANIFEST_JSON)
    manifest_ok = manifest.get("version") == PHASE106D_VERSION and len(manifest.get("project_routes") or []) == 2 and len(manifest.get("project_scoped_api_paths") or []) >= 7
    checks.append(FrontendProjectRoutesCheck("manifest", manifest_ok, "manifest 描述项目路由和项目级 API 清单" if manifest_ok else "manifest 内容不完整"))

    if skip_checksum:
        checksum_ok = True
        checksum_detail = "构建中跳过 checksum 复验"
    else:
        checksum_failures = verify_frontend_project_routes_checksums(root)
        checksum_ok = not checksum_failures
        checksum_detail = "checksum 复验通过" if checksum_ok else f"checksum 失败: {checksum_failures}"
    checks.append(FrontendProjectRoutesCheck("checksums", checksum_ok, checksum_detail))

    leaks = scan_frontend_project_routes_for_secret_leaks(root)
    checks.append(FrontendProjectRoutesCheck("secret_leak_scan", not leaks, "未发现高风险敏感信息泄露模式" if not leaks else f"发现泄露风险: {leaks}"))

    passed = all(check.passed for check in checks)
    score = round(sum(1 for check in checks if check.passed) / len(checks) * 100) if checks else 0
    report = FrontendProjectRoutesAcceptanceReport(
        passed=passed,
        score=score,
        version=PHASE106D_VERSION,
        scenario=scenario,
        output_dir=str(root),
        app_dir=str(app_dir),
        checks=checks,
        artifacts={
            "app_dir": FRONTEND_APP_DIR,
            "entrypoint": f"{FRONTEND_APP_DIR}/index.html",
            "project_list_route": "/projects",
            "project_detail_route": "/projects/:projectId",
            "manifest_json": PROJECT_ROUTES_MANIFEST_JSON,
            "acceptance_json": PROJECT_ROUTES_ACCEPTANCE_JSON,
            "checksums": PROJECT_ROUTES_CHECKSUMS,
            "zip": PROJECT_ROUTES_ZIP,
        },
    )
    if write_report:
        _write_report_files(root, report)
    return report


def run_frontend_project_routes_export(
    output_dir: str | Path,
    *,
    scenario: str = "manufacturing",
    api_base_url: str = "http://127.0.0.1:8790",
    validate_only: bool = False,
) -> FrontendProjectRoutesAcceptanceReport:
    if validate_only:
        return validate_frontend_project_routes(output_dir, scenario=scenario, write_report=True)
    return build_frontend_project_routes(output_dir, scenario=scenario, api_base_url=api_base_url)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build or validate the Phase106D QualiBug frontend project routes.")
    parser.add_argument("--output-dir", default="outputs/phase106_frontend_project_routes", help="Output directory")
    parser.add_argument("--scenario", default="manufacturing", help="Seed demo scenario")
    parser.add_argument("--api-base-url", default="http://127.0.0.1:8790", help="Phase104 API base URL")
    parser.add_argument("--validate-only", action="store_true", help="Validate existing project routes output")
    args = parser.parse_args(argv)

    report = run_frontend_project_routes_export(
        args.output_dir,
        scenario=args.scenario,
        api_base_url=args.api_base_url,
        validate_only=args.validate_only,
    )
    print(_json_dump(report.to_dict()))
    return 0 if report.passed else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
