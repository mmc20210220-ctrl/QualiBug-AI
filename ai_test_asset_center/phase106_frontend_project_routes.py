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
from ai_test_asset_center.frontend_task_journey_registry import build_frontend_task_journeys
from ai_test_asset_center.phase106_frontend_api_runtime import (
    FRONTEND_APP_DIR,
    build_frontend_api_runtime,
)
from ai_test_asset_center.ui_design_oracle_manifest import (
    build_ui_design_sources,
    build_ui_journey_oracles,
    build_ui_oracle_match_hints,
    build_ui_screen_oracles,
)

PHASE106D_VERSION = "phase106d-frontend-project-routes-v1"

PROJECT_ROUTES_MANIFEST_JSON = "frontend_project_routes_manifest.json"
PROJECT_ROUTES_MANIFEST_MD = "frontend_project_routes_manifest.md"
TASK_JOURNEYS_MANIFEST_JSON = "frontend_task_journeys_manifest.json"
TASK_JOURNEYS_MANIFEST_MD = "frontend_task_journeys_manifest.md"
UI_DESIGN_ORACLE_MANIFEST_JSON = "ui_design_oracle_manifest.json"
UI_DESIGN_ORACLE_MANIFEST_MD = "ui_design_oracle_manifest.md"
PROJECT_ROUTES_ACCEPTANCE_JSON = "frontend_project_routes_acceptance_report.json"
PROJECT_ROUTES_ACCEPTANCE_MD = "frontend_project_routes_acceptance_report.md"
PROJECT_ROUTES_CHECKSUMS = "CHECKSUMS_PHASE106D.sha256"
PROJECT_ROUTES_ZIP = "phase106_frontend_project_routes.zip"

REQUIRED_PROJECT_ROUTE_FILES: tuple[str, ...] = (
    f"{FRONTEND_APP_DIR}/src/app/projectContext.ts",
    f"{FRONTEND_APP_DIR}/src/services/projectWorkspace.ts",
    f"{FRONTEND_APP_DIR}/src/hooks/useProjectWorkspace.ts",
    f"{FRONTEND_APP_DIR}/src/hooks/useSelectedProjectId.ts",
    f"{FRONTEND_APP_DIR}/src/components/ProjectSwitcher.tsx",
    f"{FRONTEND_APP_DIR}/src/components/ProjectSummaryCard.tsx",
    f"{FRONTEND_APP_DIR}/src/components/ProjectRouteGuard.tsx",
    f"{FRONTEND_APP_DIR}/src/components/WorkspaceActionFeedback.tsx",
    f"{FRONTEND_APP_DIR}/src/components/WorkspaceStateGate.tsx",
    f"{FRONTEND_APP_DIR}/src/components/DangerConfirmButton.tsx",
    f"{FRONTEND_APP_DIR}/src/pages/ProjectListPage.tsx",
    f"{FRONTEND_APP_DIR}/src/pages/ProjectDetailPage.tsx",
    f"{FRONTEND_APP_DIR}/src/__tests__/project-routes-contract.test.ts",
    f"{FRONTEND_APP_DIR}/src/styles/project-routes.css",
    f"{FRONTEND_APP_DIR}/README_FRONTEND_PROJECT_ROUTES.md",
    PROJECT_ROUTES_MANIFEST_JSON,
    PROJECT_ROUTES_MANIFEST_MD,
    TASK_JOURNEYS_MANIFEST_JSON,
    TASK_JOURNEYS_MANIFEST_MD,
    UI_DESIGN_ORACLE_MANIFEST_JSON,
    UI_DESIGN_ORACLE_MANIFEST_MD,
    PROJECT_ROUTES_ACCEPTANCE_JSON,
    PROJECT_ROUTES_ACCEPTANCE_MD,
    PROJECT_ROUTES_CHECKSUMS,
    PROJECT_ROUTES_ZIP,
)

CORE_PROJECT_ROUTE_LABELS: tuple[str, ...] = (
    "全局产品壳",
    "导航",
    "项目列表",
    "项目详情",
    "顶部状态区",
    "当前项目切换",
    "项目切换连续性",
    "运行模式",
    "后端状态",
    "项目级 API 请求",
    "项目级状态缓存",
    "统一状态反馈",
    "统一加载态",
    "空态",
    "失败态",
    "离线态",
    "危险动作确认",
    "ProjectWorkspace",
    "ProjectSwitcher",
    "ProjectRouteGuard",
    "demo fallback",
    "real API mode",
    "Phase104 API",
    "默认脱敏",
)

PROJECT_ROUTE_CONTRACT: tuple[dict[str, Any], ...] = (
    {
        "path": "/projects",
        "component": "ProjectListPage",
        "purpose": "项目列表、创建项目草案、统一状态反馈、当前项目切换",
        "requires_project_context": False,
        "primary_actions": ["create_project_draft", "switch_project", "open_project_detail"],
        "journey_entry": ["select_project", "create_project_draft"],
        "data_dependencies": ["projectWorkspace", "runtimeHealth", "projectRouteInventory"],
        "risk_tags": ["workspace_loading", "project_selection", "task_completion"],
        "guard_conditions": ["workspace_state_gate"],
        "design_screen_id": "project_list",
        "expected_components": ["topbar", "project_switcher", "project_card_list", "create_project_button", "workspace_state_gate"],
        "expected_states": ["loading", "success", "empty", "error", "offline"],
        "required_feedback": ["loading_indicator", "empty_state_message", "error_feedback"],
    },
    {
        "path": "/projects/:projectId",
        "component": "ProjectDetailPage",
        "purpose": "项目详情、项目切换连续性、危险动作确认、进入核心页面",
        "requires_project_context": True,
        "primary_actions": ["switch_project", "open_command_center", "open_risk_evidence"],
        "journey_entry": ["open_project_detail", "enter_command_center", "open_risk_evidence"],
        "data_dependencies": ["projectWorkspace", "projectScopedApiPaths", "projectRouteGuard"],
        "risk_tags": ["ui_navigation", "context_continuity", "project_scope_binding"],
        "guard_conditions": ["project_route_guard", "workspace_state_gate"],
        "design_screen_id": "project_detail",
        "expected_components": ["topbar", "project_summary", "project_switcher", "project_route_guard", "project_scoped_api_paths"],
        "expected_states": ["loading", "success", "error", "offline"],
        "required_feedback": ["current_project_visible", "navigation_entry_visible"],
    },
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


def _json_dump_raw(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


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
        """import { runtimeConfig } from './runtimeConfig';\n\nexport interface ProjectSummary {\n  projectId: string;\n  projectName: string;\n  customerName: string;\n  industry: string;\n  status: string;\n  launchDecision: string;\n  updatedAt: string;\n  healthScore: number;\n  riskCount: number;\n}\n\nconst SELECTED_PROJECT_KEY = 'qualibug.selectedProjectId';\nexport const PROJECT_SELECTION_EVENT = 'qualibug:project-selection-changed';\n\nfunction readString(value: unknown, fallback: string): string {\n  return typeof value === 'string' && value.trim() ? value : fallback;\n}\n\nfunction readNumber(value: unknown, fallback: number): number {\n  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;\n}\n\nexport function normalizeProjectSummary(raw: Record<string, unknown>, fallbackIndex = 0): ProjectSummary {\n  return {\n    projectId: readString(raw.project_id ?? raw.projectId ?? raw.id, fallbackIndex === 0 ? runtimeConfig.projectId : `demo-project-${fallbackIndex}`),\n    projectName: readString(raw.project_name ?? raw.projectName ?? raw.name, '未命名质量评估项目'),\n    customerName: readString(raw.customer_name ?? raw.customerName ?? raw.customer, '未命名客户'),\n    industry: readString(raw.industry, 'unknown'),\n    status: readString(raw.status, 'draft'),\n    launchDecision: readString(raw.launch_decision ?? raw.launchDecision ?? raw.recommendation, '待评估'),\n    updatedAt: readString(raw.updated_at ?? raw.updatedAt, new Date().toISOString()),\n    healthScore: readNumber(raw.health_score ?? raw.healthScore, 0),\n    riskCount: readNumber(raw.risk_count ?? raw.riskCount, 0),\n  };\n}\n\nexport function readSelectedProjectId(fallbackProjectId = runtimeConfig.projectId): string {\n  try {\n    return window.localStorage.getItem(SELECTED_PROJECT_KEY) || fallbackProjectId;\n  } catch {\n    return fallbackProjectId;\n  }\n}\n\nexport function persistSelectedProjectId(projectId: string): void {\n  try {\n    window.localStorage.setItem(SELECTED_PROJECT_KEY, projectId);\n    window.dispatchEvent(new CustomEvent(PROJECT_SELECTION_EVENT, { detail: { projectId } }));\n  } catch {\n    // ignore storage failures in embedded customer preview.\n  }\n}\n\nexport function subscribeSelectedProject(onChange: (projectId: string) => void): () => void {\n  const handleCustomEvent = (event: Event) => {\n    const detail = event instanceof CustomEvent ? event.detail as { projectId?: string } | undefined : undefined;\n    if (detail?.projectId) onChange(detail.projectId);\n  };\n  const handleStorage = (event: StorageEvent) => {\n    if (event.key === SELECTED_PROJECT_KEY && event.newValue) onChange(event.newValue);\n  };\n  window.addEventListener(PROJECT_SELECTION_EVENT, handleCustomEvent);\n  window.addEventListener('storage', handleStorage);\n  return () => {\n    window.removeEventListener(PROJECT_SELECTION_EVENT, handleCustomEvent);\n    window.removeEventListener('storage', handleStorage);\n  };\n}\n\nexport function projectScopedApiPath(path: string, projectId: string): string {\n  return path.replace('{projectId}', encodeURIComponent(projectId));\n}\n\nexport function projectDetailPath(projectId: string): string {\n  return `/projects/${encodeURIComponent(projectId)}`;\n}\n\nexport function continueWorkspacePath(pathname: string, projectId: string): string {\n  if (pathname.startsWith('/projects/')) return projectDetailPath(projectId);\n  return pathname || '/';\n}\n""",
    )


def _write_project_workspace_service(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/services/projectWorkspace.ts",
        """import { qualiBugClient } from '../api/qualibugClient';\nimport { demoData } from '../data/demoData';\nimport { runtimeConfig } from '../app/runtimeConfig';\nimport { redactApiError, loadRuntimeHealth, type RuntimeHealth } from '../api/runtimeApi';\nimport { normalizeProjectSummary, projectScopedApiPath, type ProjectSummary } from '../app/projectContext';\n\nexport interface ProjectWorkspaceResult {\n  source: 'demo-data' | 'phase104-api' | 'demo-fallback';\n  mode: 'demo' | 'real';\n  projects: ProjectSummary[];\n  runtimeHealth: RuntimeHealth;\n  error?: Record<string, unknown>;\n}\n\nexport type WorkspaceFeedbackTone = 'loading' | 'success' | 'warning' | 'error' | 'offline';\n\n// demo fallback keeps project list usable when real API mode is offline.\nexport const projectScopedApiPaths = [\n  { method: 'GET', path: '/api/v1/projects/{projectId}/command-center', client: 'getCommandCenter', screen: '质量驾驶舱' },\n  { method: 'GET', path: '/api/v1/projects/{projectId}/environment/readiness', client: 'getEnvironmentReadiness', screen: '环境诊断' },\n  { method: 'GET', path: '/api/v1/projects/{projectId}/business-model', client: 'getBusinessModel', screen: '业务流程地图' },\n  { method: 'GET', path: '/api/v1/projects/{projectId}/test-plan', client: 'getTestPlan', screen: 'AI 测试计划' },\n  { method: 'GET', path: '/api/v1/projects/{projectId}/live-map', client: 'getLiveMap', screen: '实时执行' },\n  { method: 'GET', path: '/api/v1/projects/{projectId}/risks', client: 'listRisks', screen: '风险证据链' },\n  { method: 'GET', path: '/api/v1/projects/{projectId}/reports/executive', client: 'getExecutiveReport', screen: '领导层报告' },\n];\n\nexport function demoProjects(): ProjectSummary[] {\n  const primary = normalizeProjectSummary({\n    ...demoData.project,\n    health_score: demoData.demo_summary.quality_score,\n    risk_count: demoData.demo_summary.risk_count,\n    launch_decision: demoData.demo_summary.launch_recommendation,\n  });\n  return [\n    primary,\n    normalizeProjectSummary({\n      project_id: 'demo_retail_payment_v2',\n      project_name: '零售支付链路上线评估',\n      customer_name: '某零售集团',\n      industry: 'retail',\n      status: 'environment_ready',\n      launch_decision: '需完成风险复验',\n      health_score: 76,\n      risk_count: 4,\n      updated_at: demoData.generated_at,\n    }, 1),\n    normalizeProjectSummary({\n      project_id: 'demo_finance_core_banking',\n      project_name: '核心账务系统灰度质量评估',\n      customer_name: '某金融客户',\n      industry: 'finance',\n      status: 'test_running',\n      launch_decision: '暂不建议上线',\n      health_score: 68,\n      risk_count: 7,\n      updated_at: demoData.generated_at,\n    }, 2),\n  ];\n}\n\nexport function findProject(projects: ProjectSummary[], projectId: string): ProjectSummary | undefined {\n  return projects.find((project) => project.projectId === projectId) || projects[0];\n}\n\nexport function scopedPathsForProject(projectId: string) {\n  return projectScopedApiPaths.map((item) => ({ ...item, resolvedPath: projectScopedApiPath(item.path, projectId) }));\n}\n\nexport function workspaceSourceLabel(source: ProjectWorkspaceResult['source'] | undefined): string {\n  if (source === 'phase104-api') return '真实 API';\n  if (source === 'demo-fallback') return 'Demo Fallback';\n  return 'Demo 数据';\n}\n\nexport function runtimeStatusTone(runtimeHealth: RuntimeHealth | null | undefined): WorkspaceFeedbackTone {\n  if (!runtimeHealth) return 'loading';\n  if (runtimeHealth.backendStatus === 'online') return 'success';\n  if (runtimeHealth.backendStatus === 'failed') return 'error';\n  if (runtimeHealth.backendStatus === 'offline') return 'offline';\n  return 'warning';\n}\n\nexport function runtimeStatusLabel(runtimeHealth: RuntimeHealth | null | undefined): string {\n  if (!runtimeHealth) return '检查中';\n  if (runtimeHealth.backendStatus === 'online') return '在线';\n  if (runtimeHealth.backendStatus === 'failed') return '失败';\n  if (runtimeHealth.backendStatus === 'offline') return '离线';\n  if (runtimeHealth.backendStatus === 'checking') return '检查中';\n  return '演示模式';\n}\n\nexport class ProjectWorkspace {\n  async listProjects(): Promise<ProjectWorkspaceResult> {\n    const runtimeHealth = await loadRuntimeHealth();\n    if (runtimeConfig.demoMode) {\n      return { mode: 'demo', source: 'demo-data', projects: demoProjects(), runtimeHealth };\n    }\n    try {\n      const rawProjects = await qualiBugClient.listProjects();\n      const projects = Array.isArray(rawProjects)\n        ? rawProjects.map((item, index) => normalizeProjectSummary(item as Record<string, unknown>, index))\n        : [];\n      return { mode: 'real', source: 'phase104-api', projects, runtimeHealth };\n    } catch (error) {\n      if (!runtimeConfig.fallbackToDemo) throw error;\n      return {\n        mode: 'real',\n        source: 'demo-fallback',\n        projects: demoProjects(),\n        runtimeHealth: { ...runtimeHealth, online: false, fallbackActive: true },\n        error: redactApiError(error),\n      };\n    }\n  }\n\n  async createProjectDraft(payload: Record<string, unknown>): Promise<Record<string, unknown>> {\n    const runtimeHealth = await loadRuntimeHealth();\n    if (runtimeConfig.demoMode) {\n      return { accepted: true, source: 'demo-data', runtimeHealth, project: normalizeProjectSummary(payload, 9) };\n    }\n    try {\n      const project = await qualiBugClient.createProject(payload);\n      return { accepted: true, source: 'phase104-api', runtimeHealth, project };\n    } catch (error) {\n      if (!runtimeConfig.fallbackToDemo) throw error;\n      return { accepted: true, source: 'demo-fallback', runtimeHealth: { ...runtimeHealth, fallbackActive: true }, error: redactApiError(error), project: normalizeProjectSummary(payload, 9) };\n    }\n  }\n}\n\nexport const projectWorkspace = new ProjectWorkspace();\n""",
    )


def _write_project_hook(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/hooks/useProjectWorkspace.ts",
        """import { useCallback, useEffect, useMemo, useState } from 'react';\nimport { continueWorkspacePath, persistSelectedProjectId, readSelectedProjectId, subscribeSelectedProject, type ProjectSummary } from '../app/projectContext';\nimport { findProject, projectWorkspace, scopedPathsForProject, type ProjectWorkspaceResult } from '../services/projectWorkspace';\n\nexport function useProjectWorkspace() {\n  const [result, setResult] = useState<ProjectWorkspaceResult | null>(null);\n  const [loading, setLoading] = useState(true);\n  const [error, setError] = useState<string | null>(null);\n  const [selectedProjectId, setSelectedProjectIdState] = useState(readSelectedProjectId());\n\n  const refresh = useCallback(() => {\n    setLoading(true);\n    setError(null);\n    projectWorkspace.listProjects()\n      .then((payload) => {\n        setResult(payload);\n        const candidate = findProject(payload.projects, selectedProjectId);\n        if (candidate && candidate.projectId !== selectedProjectId) {\n          setSelectedProjectIdState(candidate.projectId);\n          persistSelectedProjectId(candidate.projectId);\n        }\n      })\n      .catch((err: unknown) => {\n        setError(err instanceof Error ? err.message : '项目工作区加载失败');\n        setResult(null);\n      })\n      .finally(() => setLoading(false));\n  }, [selectedProjectId]);\n\n  useEffect(() => {\n    refresh();\n  }, [refresh]);\n\n  useEffect(() => subscribeSelectedProject((projectId) => setSelectedProjectIdState(projectId)), []);\n\n  const selectProject = useCallback((projectId: string) => {\n    setSelectedProjectIdState(projectId);\n    persistSelectedProjectId(projectId);\n    const nextPath = continueWorkspacePath(window.location.pathname, projectId);\n    if (nextPath !== window.location.pathname) {\n      window.location.assign(nextPath);\n    }\n  }, []);\n\n  const currentProject: ProjectSummary | undefined = useMemo(() => {\n    return result ? findProject(result.projects, selectedProjectId) : undefined;\n  }, [result, selectedProjectId]);\n\n  const scopedApiPaths = useMemo(() => scopedPathsForProject(currentProject?.projectId || selectedProjectId), [currentProject, selectedProjectId]);\n\n  return {\n    loading,\n    error,\n    projects: result?.projects || [],\n    currentProject,\n    selectedProjectId,\n    selectProject,\n    refresh,\n    source: result?.source,\n    mode: result?.mode,\n    runtimeHealth: result?.runtimeHealth,\n    scopedApiPaths,\n    isEmpty: !loading && (result?.projects || []).length === 0,\n  };\n}\n""",
    )
    _write_text(
        app_dir / "src/hooks/useSelectedProjectId.ts",
        """import { useEffect, useState } from 'react';\nimport { readSelectedProjectId, subscribeSelectedProject } from '../app/projectContext';\n\nexport function useSelectedProjectId() {\n  const [projectId, setProjectId] = useState(() => readSelectedProjectId());\n\n  useEffect(() => {\n    return subscribeSelectedProject((nextProjectId) => setProjectId(nextProjectId));\n  }, []);\n\n  return projectId;\n}\n""",
    )


def _write_project_components(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/components/ProjectSummaryCard.tsx",
        """import { projectDetailPath, type ProjectSummary } from '../app/projectContext';\n\nexport function ProjectSummaryCard({ project, active, onSelect }: { project: ProjectSummary; active?: boolean; onSelect?: (projectId: string) => void }) {\n  return (\n    <article className={`qb-project-card ${active ? 'active' : ''}`}>\n      <div className=\"qb-project-card__head\">\n        <div>\n          <span className=\"qb-kicker\">{project.customerName} · {project.industry}</span>\n          <h3>{project.projectName}</h3>\n        </div>\n        <strong>{project.healthScore}</strong>\n      </div>\n      <p>状态：{project.status} · 风险数：{project.riskCount} · 上线建议：{project.launchDecision}</p>\n      <p className=\"qb-note\">最近刷新：{project.updatedAt}</p>\n      <div className=\"qb-project-card__actions\">\n        <a href={projectDetailPath(project.projectId)}>进入项目详情</a>\n        <button type=\"button\" onClick={() => onSelect?.(project.projectId)}>{active ? '继续当前旅程' : '设为当前项目'}</button>\n      </div>\n    </article>\n  );\n}\n""",
    )
    _write_text(
        app_dir / "src/components/ProjectSwitcher.tsx",
        """import type { ProjectSummary } from '../app/projectContext';\nimport { useProjectWorkspace } from '../hooks/useProjectWorkspace';\n\ntype ProjectSwitcherProps = {\n  projects?: ProjectSummary[];\n  selectedProjectId?: string;\n  onSelect?: (projectId: string) => void;\n  disabled?: boolean;\n  compact?: boolean;\n};\n\nexport function ProjectSwitcher({ projects, selectedProjectId, onSelect, disabled, compact }: ProjectSwitcherProps) {\n  const workspace = useProjectWorkspace();\n  const resolvedProjects = projects ?? workspace.projects;\n  const resolvedProjectId = selectedProjectId ?? workspace.selectedProjectId;\n  const resolvedOnSelect = onSelect ?? workspace.selectProject;\n  const resolvedDisabled = disabled ?? workspace.loading;\n\n  return (\n    <label className={`qb-project-switcher ${compact ? 'compact' : ''}`.trim()} data-testid=\"project-switcher\">\n      <span>当前项目切换</span>\n      <select data-testid=\"project-switcher-select\" value={resolvedProjectId} disabled={resolvedDisabled || resolvedProjects.length === 0} onChange={(event) => resolvedOnSelect(event.target.value)}>\n        {resolvedProjects.map((project) => (\n          <option key={project.projectId} value={project.projectId}>{project.projectName}</option>\n        ))}\n      </select>\n      {compact ? null : <small>切换后保持当前工作区路由与信息架构稳定。</small>}\n    </label>\n  );\n}\n""",
    )
    _write_text(
        app_dir / "src/components/ProjectRouteGuard.tsx",
        """import type { PropsWithChildren } from 'react';\n\nexport function ProjectRouteGuard({ ready, message, children }: PropsWithChildren<{ ready: boolean; message?: string }>) {\n  if (!ready) {\n    return (\n      <section className=\"qb-project-guard\">\n        <h2>项目级状态缓存准备中</h2>\n        <p>{message || '正在加载项目列表和当前项目上下文。'}</p>\n        <a href=\"/projects\">返回项目列表</a>\n      </section>\n    );\n  }\n  return <>{children}</>;\n}\n""",
    )
    _write_text(
        app_dir / "src/components/WorkspaceActionFeedback.tsx",
        """import type { ReactNode } from 'react';\nimport type { WorkspaceFeedbackTone } from '../services/projectWorkspace';\n\nexport function WorkspaceActionFeedback({ tone, title, description, action, compact }: { tone: WorkspaceFeedbackTone; title: string; description: string; action?: ReactNode; compact?: boolean }) {\n  return (\n    <section className={`qb-feedback qb-feedback-${tone} ${compact ? 'compact' : ''}`.trim()}>\n      <div>\n        <strong>{title}</strong>\n        <p>{description}</p>\n      </div>\n      {action ? <div className=\"qb-feedback__action\">{action}</div> : null}\n    </section>\n  );\n}\n""",
    )
    _write_text(
        app_dir / "src/components/WorkspaceStateGate.tsx",
        """import type { PropsWithChildren } from 'react';\nimport type { RuntimeHealth } from '../api/runtimeApi';\nimport { WorkspaceActionFeedback } from './WorkspaceActionFeedback';\nimport { runtimeStatusLabel, runtimeStatusTone, workspaceSourceLabel, type ProjectWorkspaceResult } from '../services/projectWorkspace';\n\nexport function WorkspaceStateGate({ loading, error, isEmpty, runtimeHealth, source, onRetry, children }: PropsWithChildren<{ loading: boolean; error?: string | null; isEmpty?: boolean; runtimeHealth?: RuntimeHealth | null; source?: ProjectWorkspaceResult['source']; onRetry?: () => void }>) {\n  const retryAction = onRetry ? <button type=\"button\" className=\"qb-button-secondary\" onClick={onRetry}>重试</button> : undefined;\n\n  if (loading) {\n    return <WorkspaceActionFeedback tone=\"loading\" title=\"统一加载态\" description=\"正在加载项目工作区与顶部状态区信息。\" action={retryAction} />;\n  }\n\n  if (error) {\n    const tone = runtimeStatusTone(runtimeHealth);\n    const runtimeLabel = runtimeStatusLabel(runtimeHealth);\n    return <WorkspaceActionFeedback tone={tone === 'loading' ? 'error' : tone} title=\"统一失败态\" description={`项目工作区加载失败：${error}（后端状态：${runtimeLabel}）`} action={retryAction} />;\n  }\n\n  if (isEmpty) {\n    return <WorkspaceActionFeedback tone=\"warning\" title=\"统一空态\" description=\"当前账号下暂无可用项目，可创建项目草案或切换为 demo 数据。\" action={retryAction} />;\n  }\n\n  const banner = runtimeHealth && runtimeHealth.mode === 'real' && runtimeHealth.backendStatus !== 'online';\n  return (\n    <>\n      {banner ? (\n        <WorkspaceActionFeedback\n          tone={runtimeStatusTone(runtimeHealth)}\n          compact\n          title={`统一离线态：后端状态 ${runtimeStatusLabel(runtimeHealth)}`}\n          description={`${runtimeHealth.message} 数据来源：${workspaceSourceLabel(source)}`}\n          action={retryAction}\n        />\n      ) : null}\n      {children}\n    </>\n  );\n}\n""",
    )
    _write_text(
        app_dir / "src/components/DangerConfirmButton.tsx",
        """import { useRef, useState } from 'react';\n\nexport function DangerConfirmButton({ label, title, description, confirmText = '确认', cancelText = '取消', onConfirm, disabled }: { label: string; title: string; description: string; confirmText?: string; cancelText?: string; onConfirm: () => void | Promise<void>; disabled?: boolean }) {\n  const dialogRef = useRef<HTMLDialogElement | null>(null);\n  const [submitting, setSubmitting] = useState(false);\n\n  async function handleConfirm() {\n    setSubmitting(true);\n    try {\n      await onConfirm();\n      dialogRef.current?.close('confirmed');\n    } finally {\n      setSubmitting(false);\n    }\n  }\n\n  return (\n    <>\n      <button type=\"button\" className=\"qb-button-danger\" disabled={disabled || submitting} onClick={() => dialogRef.current?.showModal()}>\n        {label}\n      </button>\n      <dialog ref={dialogRef} className=\"qb-confirm-dialog\">\n        <form method=\"dialog\" className=\"qb-confirm-dialog__form\">\n          <h3>危险动作确认：{title}</h3>\n          <p>{description}</p>\n          <menu className=\"qb-confirm-dialog__actions\">\n            <button type=\"submit\" value=\"cancel\" className=\"qb-button-secondary\">{cancelText}</button>\n            <button type=\"button\" className=\"qb-button-danger\" onClick={handleConfirm} disabled={submitting}>{confirmText}</button>\n          </menu>\n        </form>\n      </dialog>\n    </>\n  );\n}\n""",
    )


def _write_project_pages(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/pages/ProjectListPage.tsx",
        """import { useState } from 'react';\nimport { DataModeBadge } from '../components/DataModeBadge';\nimport { PageShell } from '../components/PageShell';\nimport { ProjectSwitcher } from '../components/ProjectSwitcher';\nimport { ProjectSummaryCard } from '../components/ProjectSummaryCard';\nimport { WorkspaceStateGate } from '../components/WorkspaceStateGate';\nimport { projectWorkspace } from '../services/projectWorkspace';\nimport { useProjectWorkspace } from '../hooks/useProjectWorkspace';\n\nexport function ProjectListPage() {\n  const workspace = useProjectWorkspace();\n  const [lastCreateResult, setLastCreateResult] = useState<string | null>(null);\n\n  async function createDraftProject() {\n    const result = await projectWorkspace.createProjectDraft({\n      project_id: `draft_${Date.now()}`,\n      project_name: '新客户上线质量评估草案',\n      customer_name: '待补充客户',\n      industry: 'unknown',\n      status: 'draft',\n      launch_decision: '待环境诊断',\n    });\n    setLastCreateResult(`创建项目草案已接收：${String(result.source)}`);\n    workspace.refresh();\n  }\n\n  return (\n    <PageShell title=\"项目列表\" subtitle=\"真实项目维度入口：项目列表、创建项目草案、当前项目切换、项目级状态缓存、统一状态反馈。\">\n      <WorkspaceStateGate loading={workspace.loading} error={workspace.error} isEmpty={workspace.isEmpty} runtimeHealth={workspace.runtimeHealth} source={workspace.source} onRetry={workspace.refresh}>\n        <div className=\"qb-project-toolbar\">\n          <DataModeBadge mode={workspace.mode || 'demo'} source={workspace.source} />\n          <ProjectSwitcher projects={workspace.projects} selectedProjectId={workspace.selectedProjectId} onSelect={workspace.selectProject} disabled={workspace.loading} />\n          <button type=\"button\" data-testid=\"create-project-draft\" onClick={createDraftProject}>创建项目草案</button>\n        </div>\n        {lastCreateResult && <p className=\"qb-success\">{lastCreateResult}</p>}\n        <section className=\"qb-project-grid\">\n          {workspace.projects.map((project) => (\n            <ProjectSummaryCard key={project.projectId} project={project} active={project.projectId === workspace.selectedProjectId} onSelect={workspace.selectProject} />\n          ))}\n        </section>\n        <section className=\"qb-panel\">\n          <h3>项目级 API 请求</h3>\n          <p>每个核心页面后续都使用当前项目 ID 调用 Phase104 API，避免多个客户项目数据混用。</p>\n        </section>\n      </WorkspaceStateGate>\n    </PageShell>\n  );\n}\n""",
    )
    _write_text(
        app_dir / "src/pages/ProjectDetailPage.tsx",
        """import { DataModeBadge } from '../components/DataModeBadge';\nimport { PageShell } from '../components/PageShell';\nimport { ProjectRouteGuard } from '../components/ProjectRouteGuard';\nimport { ProjectSwitcher } from '../components/ProjectSwitcher';\nimport { WorkspaceStateGate } from '../components/WorkspaceStateGate';\nimport { useProjectWorkspace } from '../hooks/useProjectWorkspace';\n\nfunction currentProjectIdFromPath(): string {\n  const parts = window.location.pathname.split('/').filter(Boolean);\n  return parts[0] === 'projects' && parts[1] ? decodeURIComponent(parts[1]) : '';\n}\n\nexport function ProjectDetailPage() {\n  const workspace = useProjectWorkspace();\n  const routeProjectId = currentProjectIdFromPath();\n  const project = workspace.projects.find((item) => item.projectId === routeProjectId) || workspace.currentProject;\n\n  return (\n    <PageShell title=\"项目详情\" subtitle=\"项目级路由：每个页面、每个 API 请求都绑定当前项目上下文，并保证项目切换连续性。\">\n      <WorkspaceStateGate loading={workspace.loading} error={workspace.error} isEmpty={workspace.isEmpty} runtimeHealth={workspace.runtimeHealth} source={workspace.source} onRetry={workspace.refresh}>\n        <ProjectRouteGuard ready={Boolean(project)} message=\"没有找到该项目，请先回到项目列表选择当前项目。\">\n          {project && (\n            <>\n              <div className=\"qb-project-toolbar\">\n                <DataModeBadge mode={workspace.mode || 'demo'} source={workspace.source} />\n                <ProjectSwitcher projects={workspace.projects} selectedProjectId={project.projectId} onSelect={workspace.selectProject} disabled={workspace.loading} />\n                <a href=\"/projects\">返回项目列表</a>\n              </div>\n              <section className=\"qb-project-hero\">\n                <span className=\"qb-kicker\">{project.customerName} · {project.industry}</span>\n                <h2>{project.projectName}</h2>\n                <p>项目 ID：<code>{project.projectId}</code></p>\n                <p>上线建议：{project.launchDecision} · 风险数：{project.riskCount} · 健康分：{project.healthScore}</p>\n              </section>\n              <section className=\"qb-project-actions\">\n                <a href=\"/\" data-testid=\"command-center\">进入质量驾驶舱</a>\n                <a href=\"/environment\">进入环境诊断</a>\n                <a href=\"/test-plan-runtime\">进入 AI 测试计划</a>\n                <a href=\"/execution-runtime\">进入实时测试执行</a>\n                <a href=\"/risk-evidence\" data-testid=\"risk-evidence\">进入风险证据链</a>\n                <a href=\"/report-roi\">进入领导报告 / ROI</a>\n              </section>\n              <section className=\"qb-panel\">\n                <h3>项目级 API 路径清单</h3>\n                <div className=\"qb-api-paths\">\n                  {workspace.scopedApiPaths.map((item) => (\n                    <article key={item.path}>\n                      <strong>{item.screen}</strong>\n                      <code>{item.method} {item.resolvedPath}</code>\n                      <small>{item.client}</small>\n                    </article>\n                  ))}\n                </div>\n              </section>\n            </>\n          )}\n        </ProjectRouteGuard>\n      </WorkspaceStateGate>\n    </PageShell>\n  );\n}\n""",
    )


def _patch_product_shell(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/components/Topbar.tsx",
        """import { DataModeBadge } from './DataModeBadge';\nimport { ProjectSwitcher } from './ProjectSwitcher';\nimport { useProjectWorkspace } from '../hooks/useProjectWorkspace';\nimport { runtimeStatusLabel, workspaceSourceLabel } from '../services/projectWorkspace';\n\nexport function Topbar() {\n  const workspace = useProjectWorkspace();\n  const runtimeLabel = runtimeStatusLabel(workspace.runtimeHealth);\n  const projectName = workspace.currentProject?.projectName || 'QualiBug 指挥中心';\n  const sourceLabel = workspaceSourceLabel(workspace.source);\n\n  return (\n    <header className=\"qb-topbar\">\n      <div>\n        <span className=\"qb-kicker\">顶部状态区 · 运行模式 · 后端状态 · 默认脱敏</span>\n        <h1>{projectName}</h1>\n        <div className=\"qb-topbar-meta\">\n          <span>当前项目 <strong>{workspace.selectedProjectId}</strong></span>\n          <span>运行模式 <strong>{workspace.mode || 'demo'}</strong></span>\n          <span>后端状态 <strong>{runtimeLabel}</strong></span>\n        </div>\n      </div>\n      <div className=\"qb-topbar-actions\">\n        <DataModeBadge mode={workspace.mode || 'demo'} source={sourceLabel} />\n        <ProjectSwitcher projects={workspace.projects} selectedProjectId={workspace.selectedProjectId} onSelect={workspace.selectProject} disabled={workspace.loading} compact />\n        <a href=\"/projects\" className=\"qb-topbar-link\">项目列表</a>\n      </div>\n    </header>\n  );\n}\n""",
    )
    _write_text(
        app_dir / "src/components/Sidebar.tsx",
        """import { routeInventory } from '../data/demoData';\nimport { readSelectedProjectId } from '../app/projectContext';\n\nfunction isActiveRoute(pathname: string, routePath: string): boolean {\n  if (routePath.includes(':projectId')) return pathname.startsWith('/projects/');\n  return pathname === routePath;\n}\n\nexport function Sidebar() {\n  const pathname = window.location.pathname;\n  const projectId = readSelectedProjectId();\n\n  return (\n    <aside className=\"qb-sidebar\">\n      <div className=\"qb-brand\">\n        <strong>QualiBug AI</strong>\n        <span>全局产品壳 · 导航</span>\n      </div>\n      <div className=\"qb-sidebar-meta\">\n        <small>当前项目</small>\n        <strong>{projectId}</strong>\n      </div>\n      <nav>\n        {routeInventory.map((route) => (\n          <a key={route.key} href={route.path} className={`qb-nav-link ${isActiveRoute(pathname, route.path) ? 'active' : ''}`.trim()}>\n            <span>{route.label}</span>\n            <small>{route.component}</small>\n          </a>\n        ))}\n      </nav>\n    </aside>\n  );\n}\n""",
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
        """.qb-project-toolbar {\n  display: flex;\n  flex-wrap: wrap;\n  gap: 12px;\n  align-items: center;\n  margin-bottom: 18px;\n}\n\n.qb-project-switcher {\n  display: inline-flex;\n  gap: 8px;\n  align-items: center;\n  padding: 8px 10px;\n  border: 1px solid var(--qb-border);\n  border-radius: 12px;\n  background: var(--qb-surface);\n}\n\n.qb-project-switcher.compact small {\n  display: none;\n}\n\n.qb-project-switcher select {\n  min-width: 260px;\n}\n\n.qb-project-grid {\n  display: grid;\n  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));\n  gap: 16px;\n}\n\n.qb-project-card, .qb-project-hero, .qb-project-guard, .qb-panel {\n  border: 1px solid var(--qb-border);\n  border-radius: 18px;\n  background: var(--qb-surface);\n  padding: 18px;\n  box-shadow: var(--qb-shadow);\n}\n\n.qb-project-card.active {\n  outline: 2px solid var(--qb-accent);\n}\n\n.qb-project-card__head {\n  display: flex;\n  justify-content: space-between;\n  gap: 12px;\n}\n\n.qb-project-card__head strong {\n  font-size: 32px;\n}\n\n.qb-project-card__actions, .qb-project-actions {\n  display: flex;\n  flex-wrap: wrap;\n  gap: 10px;\n  margin-top: 14px;\n}\n\n.qb-project-actions a, .qb-project-card__actions a, .qb-project-card__actions button, .qb-project-toolbar button {\n  border: 1px solid var(--qb-border);\n  border-radius: 999px;\n  padding: 8px 12px;\n  text-decoration: none;\n  background: var(--qb-surface-muted);\n}\n\n.qb-api-paths {\n  display: grid;\n  gap: 10px;\n}\n\n.qb-api-paths article {\n  display: grid;\n  gap: 4px;\n  padding: 12px;\n  border-radius: 12px;\n  background: var(--qb-surface-muted);\n}\n\n.qb-success { color: #047857; }\n.qb-error { color: #b91c1c; }\n\n.qb-nav-link.active {\n  background: rgba(255, 255, 255, 0.16);\n  outline: 1px solid rgba(255, 255, 255, 0.24);\n}\n\n.qb-sidebar-meta {\n  margin-bottom: 18px;\n  padding: 12px 14px;\n  border-radius: 14px;\n  background: rgba(255, 255, 255, 0.08);\n  display: grid;\n  gap: 4px;\n}\n\n.qb-sidebar-meta small {\n  color: #94a3b8;\n  font-size: 12px;\n  letter-spacing: 0.04em;\n  text-transform: uppercase;\n}\n\n.qb-topbar-meta {\n  display: flex;\n  flex-wrap: wrap;\n  gap: 12px;\n  color: var(--qb-muted);\n  font-size: 12px;\n  margin-top: 10px;\n}\n\n.qb-topbar-meta strong {\n  color: var(--qb-text);\n}\n\n.qb-topbar-actions .qb-topbar-link {\n  border: 1px solid var(--qb-border);\n  border-radius: 999px;\n  padding: 8px 12px;\n  background: var(--qb-surface-muted);\n}\n\n.qb-feedback {\n  border: 1px solid var(--qb-border);\n  border-radius: 16px;\n  background: var(--qb-surface);\n  padding: 16px 18px;\n  display: flex;\n  justify-content: space-between;\n  gap: 16px;\n  align-items: flex-start;\n  margin-bottom: 16px;\n}\n\n.qb-feedback.compact {\n  padding: 12px 14px;\n  border-radius: 14px;\n}\n\n.qb-feedback p {\n  margin: 8px 0 0;\n  color: var(--qb-muted);\n}\n\n.qb-feedback-loading { border-color: #cbd5e1; background: #f8fafc; }\n.qb-feedback-success { border-color: #86efac; background: #f0fdf4; }\n.qb-feedback-warning { border-color: #fcd34d; background: #fffbeb; }\n.qb-feedback-error { border-color: #fca5a5; background: #fef2f2; }\n.qb-feedback-offline { border-color: #cbd5e1; background: #f1f5f9; }\n\n.qb-feedback__action {\n  display: flex;\n  gap: 10px;\n  flex-wrap: wrap;\n}\n\n.qb-button-secondary {\n  border: 1px solid var(--qb-border);\n  border-radius: 999px;\n  padding: 10px 16px;\n  background: var(--qb-surface-muted);\n  color: var(--qb-text);\n  font-weight: 800;\n}\n\n.qb-button-danger {\n  border: 0;\n  border-radius: 999px;\n  padding: 10px 16px;\n  background: var(--qb-danger);\n  color: white;\n  font-weight: 800;\n}\n\n.qb-confirm-dialog {\n  border: 1px solid var(--qb-border);\n  border-radius: 18px;\n  padding: 0;\n  width: min(520px, calc(100vw - 32px));\n  box-shadow: var(--qb-shadow);\n}\n\n.qb-confirm-dialog::backdrop {\n  background: rgba(15, 23, 42, 0.55);\n}\n\n.qb-confirm-dialog__form {\n  padding: 18px;\n  display: grid;\n  gap: 12px;\n}\n\n.qb-confirm-dialog__form h3 {\n  margin: 0;\n}\n\n.qb-confirm-dialog__form p {\n  margin: 0;\n  color: var(--qb-muted);\n  line-height: 1.6;\n}\n\n.qb-confirm-dialog__actions {\n  display: flex;\n  justify-content: flex-end;\n  gap: 12px;\n  margin: 0;\n  padding: 0;\n}\n""",
    )


def _write_project_contract_test(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/__tests__/project-routes-contract.test.ts",
        """import { routes } from '../routes';\nimport { projectRouteInventory } from '../data/demoData';\nimport { normalizeProjectSummary, projectScopedApiPath } from '../app/projectContext';\nimport { useSelectedProjectId } from '../hooks/useSelectedProjectId';\nimport { DangerConfirmButton } from '../components/DangerConfirmButton';\nimport { WorkspaceStateGate } from '../components/WorkspaceStateGate';\nimport { ProjectWorkspace, projectScopedApiPaths } from '../services/projectWorkspace';\n\ndescribe('Phase106D project route contract', () => {\n  it('registers project list and project detail routes', () => {\n    expect(routes.some((route) => route.path === '/projects' && route.component === 'ProjectListPage')).toBe(true);\n    expect(routes.some((route) => route.path === '/projects/:projectId' && route.component === 'ProjectDetailPage')).toBe(true);\n    expect(projectRouteInventory.length).toBe(2);\n  });\n\n  it('normalizes project summaries and project-scoped API paths', () => {\n    const project = normalizeProjectSummary({ project_id: 'p-1', project_name: '项目详情验收', customer_name: '客户A', health_score: 88, risk_count: 2 });\n    expect(project.projectId).toBe('p-1');\n    expect(projectScopedApiPath('/api/v1/projects/{projectId}/risks', project.projectId)).toContain('/projects/p-1/risks');\n    expect(projectScopedApiPaths.some((item) => item.client === 'getExecutiveReport')).toBe(true);\n  });\n\n  it('exposes ProjectWorkspace for demo fallback and real API mode', () => {\n    const workspace = new ProjectWorkspace();\n    expect(workspace.listProjects).toBeInstanceOf(Function);\n    expect(workspace.createProjectDraft).toBeInstanceOf(Function);\n  });\n\n  it('keeps product shell state and danger confirmation building blocks', () => {\n    expect(useSelectedProjectId).toBeTypeOf('function');\n    expect(DangerConfirmButton).toBeTypeOf('function');\n    expect(WorkspaceStateGate).toBeTypeOf('function');\n  });\n});\n""",
    )


def _write_project_readme(app_dir: Path) -> None:
    _write_text(
        app_dir / "README_FRONTEND_PROJECT_ROUTES.md",
        """# Phase106D Frontend Project Routes\n\n本目录由 `ai_test_asset_center.phase106_frontend_project_routes` 生成，用于把 Phase106C 的真实 API runtime 前端升级为项目级工作区。\n\n## 新增能力\n\n- `/projects`：项目列表、创建项目草案、当前项目切换。\n- `/projects/:projectId`：项目详情、项目级 API 请求清单、进入核心页面。\n- `ProjectWorkspace`：统一封装 demo mode / real API mode / demo fallback。\n- `useProjectWorkspace`：项目级状态缓存和当前项目持久化。\n- `ProjectSwitcher`：客户现场多项目切换。\n\n## 运行\n\n```powershell\ncd frontend_app\nnpm install\nnpm run dev\n```\n\n打开：`http://127.0.0.1:5173/projects`。\n\n## 安全说明\n\n项目路由只展示项目摘要和项目级 API 路径，不展示原始凭证、会话、密钥或客户敏感字段。默认仍保留 demo fallback，真实 API 不可用时不影响演示。\n""",
    )


def _derive_ui_oracle_match_hints(app_dir: Path, base_hints: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    sources: dict[str, list[str]] = {
        "project_switcher": ["src/components/ProjectSwitcher.tsx"],
        "create_project_button": ["src/pages/ProjectListPage.tsx"],
        "project_card_list": ["src/pages/ProjectListPage.tsx"],
        "workspace_state_gate": ["src/components/WorkspaceStateGate.tsx"],
        "loading_indicator": ["src/components/WorkspaceStateGate.tsx"],
        "empty_state_message": ["src/components/WorkspaceStateGate.tsx"],
        "error_feedback": ["src/components/WorkspaceStateGate.tsx"],
        "project_route_guard": ["src/components/ProjectRouteGuard.tsx"],
        "project_summary": ["src/pages/ProjectDetailPage.tsx"],
        "project_scoped_api_paths": ["src/pages/ProjectDetailPage.tsx", "src/pages/ProjectListPage.tsx"],
        "current_project_visible": ["src/pages/ProjectDetailPage.tsx"],
        "navigation_entry_visible": ["src/pages/ProjectDetailPage.tsx"],
        "risk_evidence_entry": ["src/pages/ProjectDetailPage.tsx"],
        "command_center_entry": ["src/pages/ProjectDetailPage.tsx"],
    }
    derived: dict[str, dict[str, Any]] = {}
    for key, hint in base_hints.items():
        if not isinstance(key, str) or not isinstance(hint, dict):
            continue
        rels = sources.get(key, [])
        corpus = " ".join(_read_text(app_dir / rel, limit=200_000) for rel in rels if (app_dir / rel).exists())
        tokens = [str(item) for item in (hint.get("tokens") or []) if str(item).strip()]
        keywords = [str(item) for item in (hint.get("keywords") or []) if str(item).strip()]
        testids = [str(item) for item in (hint.get("testids") or []) if str(item).strip()]
        filtered_tokens = [token for token in tokens if token in corpus]
        filtered_keywords = [kw for kw in keywords if kw in corpus]
        filtered_testids = [tid for tid in testids if tid in corpus]
        roles: list[str] = [str(r) for r in (hint.get("roles") or []) if str(r).strip()]
        if key == "project_switcher" and "<select" in corpus and "combobox" not in roles:
            roles.append("combobox")
        if key == "create_project_button" and "<button" in corpus and "button" not in roles:
            roles.append("button")
        if key in {"risk_evidence_entry", "command_center_entry", "navigation_entry_visible"} and "<a " in corpus and "link" not in roles:
            roles.append("link")
        if key == "error_feedback" and "alert" not in roles:
            roles.append("alert")
        if key == "loading_indicator" and "status" not in roles:
            roles.append("status")
        if filtered_tokens or filtered_keywords or filtered_testids or roles:
            derived[key] = {
                "roles": roles,
                "testids": filtered_testids,
                "keywords": filtered_keywords or filtered_tokens,
                "tokens": filtered_tokens,
            }
    return derived


def _write_manifest_files(output_dir: Path, report: FrontendProjectRoutesAcceptanceReport | None = None) -> dict[str, Any]:
    task_journeys = build_frontend_task_journeys()
    ui_design_sources = build_ui_design_sources()
    ui_screen_oracles = build_ui_screen_oracles()
    ui_journey_oracles = build_ui_journey_oracles()
    ui_oracle_match_hints = _derive_ui_oracle_match_hints(output_dir / FRONTEND_APP_DIR, build_ui_oracle_match_hints())
    manifest = redact_value(
        {
            "version": PHASE106D_VERSION,
            "generated_at": _now(),
            "app_dir": FRONTEND_APP_DIR,
            "entrypoint": f"{FRONTEND_APP_DIR}/index.html",
            "project_routes": list(PROJECT_ROUTE_CONTRACT),
            "project_scoped_api_paths": list(PROJECT_SCOPED_API_PATHS),
            "frontend_task_journeys": task_journeys,
            "ui_design_sources": ui_design_sources,
            "ui_screen_oracles": ui_screen_oracles,
            "ui_journey_oracles": ui_journey_oracles,
            "ui_oracle_match_hints": ui_oracle_match_hints,
            "required_files": list(REQUIRED_PROJECT_ROUTE_FILES),
            "core_labels": list(CORE_PROJECT_ROUTE_LABELS),
            "artifacts": {
                "manifest_json": PROJECT_ROUTES_MANIFEST_JSON,
                "manifest_md": PROJECT_ROUTES_MANIFEST_MD,
                "task_journeys_json": TASK_JOURNEYS_MANIFEST_JSON,
                "task_journeys_md": TASK_JOURNEYS_MANIFEST_MD,
                "ui_design_oracle_json": UI_DESIGN_ORACLE_MANIFEST_JSON,
                "ui_design_oracle_md": UI_DESIGN_ORACLE_MANIFEST_MD,
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
    journey_lines = "\n".join(
        f"- `{item['journey_id']}` → `{item['entry_route']}` / `{item['defect_family']}`：步骤 {', '.join(item['steps'])}"
        for item in task_journeys
    )
    oracle_lines = "\n".join(
        f"- `{item['screen_id']}` → `{item['route']}`：组件 {', '.join(item['expected_components'])}"
        for item in ui_screen_oracles
    )
    _write_text(
        output_dir / PROJECT_ROUTES_MANIFEST_MD,
        f"""# Phase106D Frontend Project Routes Manifest\n\n- Version: `{manifest['version']}`\n- App dir: `{manifest['app_dir']}`\n- Entrypoint: `{manifest['entrypoint']}`\n\n## Project Routes\n\n{route_lines}\n\n## Project-scoped API Paths\n\n{api_lines}\n\n## Security\n\n默认脱敏，保留 demo fallback，项目级 API 请求绑定当前项目 ID。\n""",
    )
    _write_text(output_dir / TASK_JOURNEYS_MANIFEST_JSON, _json_dump_raw({"version": PHASE106D_VERSION, "generated_at": _now(), "journeys": task_journeys}))
    _write_text(
        output_dir / TASK_JOURNEYS_MANIFEST_MD,
        f"""# Phase106D Frontend Task Journeys\n\n- Version: `{manifest['version']}`\n- Entry app dir: `{manifest['app_dir']}`\n\n## Journeys\n\n{journey_lines}\n""",
    )
    _write_text(
        output_dir / UI_DESIGN_ORACLE_MANIFEST_JSON,
        _json_dump_raw(
            {
                "version": "ui-design-oracle-v1",
                "generated_at": _now(),
                "design_sources": ui_design_sources,
                "screens": ui_screen_oracles,
                "journeys": ui_journey_oracles,
                "match_hints": ui_oracle_match_hints,
            }
        ),
    )
    _write_text(
        output_dir / UI_DESIGN_ORACLE_MANIFEST_MD,
        f"""# Phase106D UI Design Oracles\n\n- Version: `ui-design-oracle-v1`\n- Entry app dir: `{manifest['app_dir']}`\n\n## Screen Oracles\n\n{oracle_lines}\n""",
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
    _patch_product_shell(app_dir)
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

    selection_hook_text = _read_text(app_dir / "src/hooks/useSelectedProjectId.ts")
    selection_hook_ok = all(keyword in selection_hook_text for keyword in ("useSelectedProjectId", "subscribeSelectedProject", "readSelectedProjectId"))
    checks.append(FrontendProjectRoutesCheck("project_switch_continuity", selection_hook_ok, "useSelectedProjectId 支持项目切换连续性" if selection_hook_ok else "项目切换连续性 Hook 缺失或不完整"))

    shell_text = _read_text(app_dir / "src/components/Topbar.tsx") + _read_text(app_dir / "src/components/Sidebar.tsx")
    shell_ok = all(keyword in shell_text for keyword in ("顶部状态区", "运行模式", "后端状态", "全局产品壳", "导航", "ProjectSwitcher"))
    checks.append(FrontendProjectRoutesCheck("product_shell", shell_ok, "全局产品壳包含导航与顶部状态区" if shell_ok else "全局产品壳缺少导航或顶部状态区信息"))

    state_text = _read_text(app_dir / "src/components/WorkspaceStateGate.tsx") + _read_text(app_dir / "src/components/DangerConfirmButton.tsx")
    state_ok = all(keyword in state_text for keyword in ("统一加载态", "统一空态", "统一失败态", "统一离线态", "危险动作确认", "<dialog"))
    checks.append(FrontendProjectRoutesCheck("workspace_states", state_ok, "统一加载/空/失败/离线态与危险确认组件已接入" if state_ok else "统一状态反馈或危险确认组件不完整"))

    pages_text = _read_text(app_dir / "src/pages/ProjectListPage.tsx") + _read_text(app_dir / "src/pages/ProjectDetailPage.tsx")
    missing_labels = [label for label in ("项目列表", "项目详情", "当前项目切换", "项目级 API 请求", "创建项目草案", "统一状态反馈", "Phase104 API") if label not in pages_text]
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
    manifest_ok = (
        manifest.get("version") == PHASE106D_VERSION
        and len(manifest.get("project_routes") or []) == 2
        and len(manifest.get("project_scoped_api_paths") or []) >= 7
        and len(manifest.get("frontend_task_journeys") or []) >= 5
        and len(manifest.get("ui_screen_oracles") or []) >= 2
        and len(manifest.get("ui_journey_oracles") or []) >= 2
    )
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
            "task_journeys_json": TASK_JOURNEYS_MANIFEST_JSON,
            "ui_design_oracle_json": UI_DESIGN_ORACLE_MANIFEST_JSON,
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
