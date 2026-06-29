from __future__ import annotations

"""Phase106E: project-scoped environment diagnosis trigger and polling runtime.

Phase106D introduced project list/detail routing. Phase106E turns the environment
screen into an executable frontend runtime boundary:

* project-scoped preflight trigger
* diagnosis status polling with safe stop/retry controls
* blocker and customer supplement action normalization
* read-only execution mode visibility
* route and contract checks for /environment-runtime

The root repository still only receives a Python generator and tests. The React
application is emitted into an output directory so it can evolve without adding
npm dependencies to the repository root.
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
from ai_test_asset_center.phase106_frontend_project_routes import (
    FRONTEND_APP_DIR,
    build_frontend_project_routes,
)

PHASE106E_VERSION = "phase106e-frontend-environment-runtime-v1"

ENVIRONMENT_RUNTIME_MANIFEST_JSON = "frontend_environment_runtime_manifest.json"
ENVIRONMENT_RUNTIME_MANIFEST_MD = "frontend_environment_runtime_manifest.md"
ENVIRONMENT_RUNTIME_ACCEPTANCE_JSON = "frontend_environment_runtime_acceptance_report.json"
ENVIRONMENT_RUNTIME_ACCEPTANCE_MD = "frontend_environment_runtime_acceptance_report.md"
ENVIRONMENT_RUNTIME_CHECKSUMS = "CHECKSUMS_PHASE106E.sha256"
ENVIRONMENT_RUNTIME_ZIP = "phase106_frontend_environment_runtime.zip"

REQUIRED_ENVIRONMENT_RUNTIME_FILES: tuple[str, ...] = (
    f"{FRONTEND_APP_DIR}/src/app/environmentRuntimeTypes.ts",
    f"{FRONTEND_APP_DIR}/src/services/environmentDiagnosisRuntime.ts",
    f"{FRONTEND_APP_DIR}/src/hooks/useEnvironmentDiagnosisRuntime.ts",
    f"{FRONTEND_APP_DIR}/src/components/EnvironmentReadinessPanel.tsx",
    f"{FRONTEND_APP_DIR}/src/components/EnvironmentBlockerList.tsx",
    f"{FRONTEND_APP_DIR}/src/components/EnvironmentPollingTimeline.tsx",
    f"{FRONTEND_APP_DIR}/src/pages/EnvironmentRuntimePage.tsx",
    f"{FRONTEND_APP_DIR}/src/__tests__/environment-runtime-contract.test.ts",
    f"{FRONTEND_APP_DIR}/src/styles/environment-runtime.css",
    f"{FRONTEND_APP_DIR}/README_FRONTEND_ENVIRONMENT_RUNTIME.md",
    ENVIRONMENT_RUNTIME_MANIFEST_JSON,
    ENVIRONMENT_RUNTIME_MANIFEST_MD,
    ENVIRONMENT_RUNTIME_ACCEPTANCE_JSON,
    ENVIRONMENT_RUNTIME_ACCEPTANCE_MD,
    ENVIRONMENT_RUNTIME_CHECKSUMS,
    ENVIRONMENT_RUNTIME_ZIP,
)

CORE_ENVIRONMENT_RUNTIME_LABELS: tuple[str, ...] = (
    "环境诊断真实触发",
    "轮询状态",
    "阻断原因",
    "客户补料动作",
    "只读安全执行",
    "项目级预检",
    "EnvironmentDiagnosisRuntime",
    "useEnvironmentDiagnosisRuntime",
    "demo fallback",
    "real API mode",
    "Phase104 API",
    "默认脱敏",
)

ENVIRONMENT_RUNTIME_ENDPOINTS: tuple[dict[str, str], ...] = (
    {"method": "GET", "path": "/api/v1/projects/{projectId}/environment/readiness", "client": "getEnvironmentReadiness", "purpose": "读取环境可测性"},
    {"method": "POST", "path": "/api/v1/projects/{projectId}/environment/preflight", "client": "runEnvironmentPreflight", "purpose": "触发项目级预检"},
    {"method": "GET", "path": "/api/v1/projects/{projectId}/environment/preflight/{runId}", "client": "pollEnvironmentDiagnosis", "purpose": "轮询诊断状态"},
    {"method": "GET", "path": "/api/v1/projects/{projectId}/environment/blockers", "client": "loadEnvironmentBlockers", "purpose": "读取阻断原因"},
)

FORBIDDEN_ENVIRONMENT_RUNTIME_PATTERNS: tuple[str, ...] = (
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
class FrontendEnvironmentRuntimeCheck:
    key: str
    passed: bool
    detail: str
    owner: str = "frontend"
    severity: str = "critical"


@dataclass
class FrontendEnvironmentRuntimeAcceptanceReport:
    passed: bool
    score: int
    version: str
    scenario: str
    output_dir: str
    app_dir: str
    checks: list[FrontendEnvironmentRuntimeCheck] = field(default_factory=list)
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


def _read_text(path: Path, *, limit: int = 500_000) -> str:
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
    excluded = {ENVIRONMENT_RUNTIME_CHECKSUMS, ENVIRONMENT_RUNTIME_ZIP}
    return [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and path.name not in excluded
        and not path.name.endswith(".pyc")
        and "node_modules" not in path.parts
    ]


def write_frontend_environment_runtime_checksums(output_dir: str | Path) -> dict[str, str]:
    root = Path(output_dir)
    checksums: dict[str, str] = {}
    for path in _iter_checksum_files(root):
        try:
            checksums[path.relative_to(root).as_posix()] = _sha256(path)
        except FileNotFoundError:
            continue
    lines = [f"{digest}  {relative}" for relative, digest in sorted(checksums.items())]
    _write_text(root / ENVIRONMENT_RUNTIME_CHECKSUMS, "\n".join(lines) + "\n")
    return checksums


def verify_frontend_environment_runtime_checksums(output_dir: str | Path) -> list[str]:
    root = Path(output_dir)
    checksum_path = root / ENVIRONMENT_RUNTIME_CHECKSUMS
    if not checksum_path.exists():
        return [f"missing {ENVIRONMENT_RUNTIME_CHECKSUMS}"]
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


def _zip_environment_runtime(output_dir: Path) -> Path:
    archive_path = output_dir / ENVIRONMENT_RUNTIME_ZIP
    if archive_path.exists():
        archive_path.unlink()
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(output_dir.rglob("*")):
            if path.is_file() and path != archive_path and not path.name.endswith(".pyc") and "node_modules" not in path.parts:
                archive.write(path, path.relative_to(output_dir).as_posix())
    return archive_path


def _write_environment_types(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/app/environmentRuntimeTypes.ts",
        """export type EnvironmentDiagnosisStatus = 'idle' | 'queued' | 'running' | 'blocked' | 'ready' | 'failed';\n\nexport interface EnvironmentBlocker {\n  id: string;\n  layer: 'url' | 'dns' | 'http' | 'auth' | 'api-smoke' | 'sso' | 'network';\n  severity: 'critical' | 'high' | 'medium' | 'low';\n  title: string;\n  businessImpact: string;\n  suggestedOwner: string;\n}\n\nexport interface CustomerSupplementAction {\n  id: string;\n  title: string;\n  requiredFrom: string;\n  dueHint: string;\n  safeToShare: boolean;\n}\n\nexport interface EnvironmentPollingEvent {\n  at: string;\n  status: EnvironmentDiagnosisStatus;\n  message: string;\n}\n\nexport interface EnvironmentRuntimeSnapshot {\n  projectId: string;\n  runId: string;\n  status: EnvironmentDiagnosisStatus;\n  score: number;\n  safeExecutionMode: string;\n  source: 'demo-data' | 'phase104-api' | 'demo-fallback';\n  blockers: EnvironmentBlocker[];\n  customerSupplementActions: CustomerSupplementAction[];\n  pollingTimeline: EnvironmentPollingEvent[];\n}\n\nexport function normalizeEnvironmentRuntimeSnapshot(raw: Record<string, unknown>, fallbackProjectId: string): EnvironmentRuntimeSnapshot {\n  const blockers = Array.isArray(raw.blockers) ? raw.blockers : [];\n  const actions = Array.isArray(raw.customer_supplement_actions ?? raw.customerSupplementActions) ? (raw.customer_supplement_actions ?? raw.customerSupplementActions) as unknown[] : [];\n  const events = Array.isArray(raw.polling_timeline ?? raw.pollingTimeline) ? (raw.polling_timeline ?? raw.pollingTimeline) as unknown[] : [];\n  return {\n    projectId: String(raw.project_id ?? raw.projectId ?? fallbackProjectId),\n    runId: String(raw.run_id ?? raw.runId ?? 'env-demo-run'),\n    status: String(raw.status ?? 'idle') as EnvironmentDiagnosisStatus,\n    score: typeof raw.score === 'number' ? raw.score : Number(raw.environment_score ?? 0),\n    safeExecutionMode: String(raw.safe_execution_mode ?? raw.safeExecutionMode ?? 'read_only'),\n    source: String(raw.source ?? 'demo-data') as EnvironmentRuntimeSnapshot['source'],\n    blockers: blockers.map((item, index) => ({\n      id: String((item as Record<string, unknown>).id ?? `blocker-${index + 1}`),\n      layer: String((item as Record<string, unknown>).layer ?? 'http') as EnvironmentBlocker['layer'],\n      severity: String((item as Record<string, unknown>).severity ?? 'medium') as EnvironmentBlocker['severity'],\n      title: String((item as Record<string, unknown>).title ?? '环境阻断待确认'),\n      businessImpact: String((item as Record<string, unknown>).business_impact ?? (item as Record<string, unknown>).businessImpact ?? '影响核心链路可测性'),\n      suggestedOwner: String((item as Record<string, unknown>).suggested_owner ?? (item as Record<string, unknown>).suggestedOwner ?? '客户实施负责人'),\n    })),\n    customerSupplementActions: actions.map((item, index) => ({\n      id: String((item as Record<string, unknown>).id ?? `supplement-${index + 1}`),\n      title: String((item as Record<string, unknown>).title ?? '补充环境信息'),\n      requiredFrom: String((item as Record<string, unknown>).required_from ?? (item as Record<string, unknown>).requiredFrom ?? '客户侧'),\n      dueHint: String((item as Record<string, unknown>).due_hint ?? (item as Record<string, unknown>).dueHint ?? '进入测试前'),\n      safeToShare: Boolean((item as Record<string, unknown>).safe_to_share ?? (item as Record<string, unknown>).safeToShare ?? true),\n    })),\n    pollingTimeline: events.map((item, index) => ({\n      at: String((item as Record<string, unknown>).at ?? new Date(Date.now() + index * 1000).toISOString()),\n      status: String((item as Record<string, unknown>).status ?? 'running') as EnvironmentDiagnosisStatus,\n      message: String((item as Record<string, unknown>).message ?? '环境诊断轮询状态更新'),\n    })),\n  };\n}\n""",
    )


def _write_environment_service(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/services/environmentDiagnosisRuntime.ts",
        """import { runtimeConfig } from '../app/runtimeConfig';\nimport { qualiBugClient } from '../api/qualibugClient';\nimport { requestJson, redactApiError } from '../api/runtimeApi';\nimport { demoData } from '../data/demoData';\nimport { normalizeEnvironmentRuntimeSnapshot, type EnvironmentRuntimeSnapshot } from '../app/environmentRuntimeTypes';\n\nfunction demoEnvironmentSnapshot(projectId: string): EnvironmentRuntimeSnapshot {\n  return normalizeEnvironmentRuntimeSnapshot({\n    project_id: projectId,\n    run_id: 'demo-environment-preflight',\n    status: 'blocked',\n    score: Number(demoData.environment?.environment_score ?? 82),\n    safe_execution_mode: runtimeConfig.safeExecutionMode,\n    source: 'demo-data',\n    blockers: [\n      { id: 'auth-sso-blocker', layer: 'auth', severity: 'high', title: 'SSO / MFA 需要客户侧授权窗口', business_impact: '阻断订单审批链路自动登录与 API Smoke', suggested_owner: '客户 IAM / 实施负责人' },\n      { id: 'api-smoke-blocker', layer: 'api-smoke', severity: 'medium', title: '核心订单接口缺少稳定测试账号', business_impact: '影响订单创建到质检放行链路覆盖率', suggested_owner: '业务系统负责人' },\n    ],\n    customer_supplement_actions: [\n      { id: 'provide-test-account', title: '提供只读测试账号或临时演示账号', required_from: '客户 IAM', due_hint: '环境预检前', safe_to_share: true },\n      { id: 'confirm-callback-allowlist', title: '确认预发环境回调域名白名单', required_from: '网络 / 运维', due_hint: '执行 API Smoke 前', safe_to_share: true },\n    ],\n    polling_timeline: [\n      { status: 'queued', message: '项目级预检已进入队列' },\n      { status: 'running', message: '正在验证 URL / DNS / HTTP / auth / API Smoke' },\n      { status: 'blocked', message: '发现认证和接口烟测阻断，需要客户补料动作' },\n    ],\n  }, projectId);\n}\n\nexport class EnvironmentDiagnosisRuntime {\n  constructor(private readonly projectId: string) {}\n\n  async loadReadiness(): Promise<EnvironmentRuntimeSnapshot> {\n    if (runtimeConfig.demoMode) return demoEnvironmentSnapshot(this.projectId);\n    try {\n      const readiness = await qualiBugClient.getEnvironmentReadiness(this.projectId);\n      return normalizeEnvironmentRuntimeSnapshot({ ...readiness, source: 'phase104-api' }, this.projectId);\n    } catch (error) {\n      if (!runtimeConfig.fallbackToDemo) throw error;\n      const fallback = demoEnvironmentSnapshot(this.projectId);\n      return { ...fallback, source: 'demo-fallback', pollingTimeline: [...fallback.pollingTimeline, { at: new Date().toISOString(), status: 'failed', message: `真实 API 不可用，已安全回退：${JSON.stringify(redactApiError(error))}` }] };\n    }\n  }\n\n  async triggerEnvironmentPreflight(): Promise<EnvironmentRuntimeSnapshot> {\n    const payload = { safe_execution_mode: runtimeConfig.safeExecutionMode, project_id: this.projectId };\n    if (runtimeConfig.demoMode) return { ...demoEnvironmentSnapshot(this.projectId), status: 'queued' };\n    try {\n      const accepted = await qualiBugClient.runEnvironmentPreflight(this.projectId, payload);\n      return normalizeEnvironmentRuntimeSnapshot({ ...accepted, source: 'phase104-api', status: accepted.status ?? 'queued' }, this.projectId);\n    } catch (error) {\n      if (!runtimeConfig.fallbackToDemo) throw error;\n      return { ...demoEnvironmentSnapshot(this.projectId), source: 'demo-fallback', status: 'queued' };\n    }\n  }\n\n  async pollEnvironmentDiagnosis(runId: string): Promise<EnvironmentRuntimeSnapshot> {\n    if (runtimeConfig.demoMode) return { ...demoEnvironmentSnapshot(this.projectId), runId, status: 'running' };\n    try {\n      const status = await requestJson<Record<string, unknown>>({ method: 'GET', path: `/api/v1/projects/${this.projectId}/environment/preflight/${runId}` });\n      return normalizeEnvironmentRuntimeSnapshot({ ...status, source: 'phase104-api' }, this.projectId);\n    } catch (error) {\n      if (!runtimeConfig.fallbackToDemo) throw error;\n      return { ...demoEnvironmentSnapshot(this.projectId), source: 'demo-fallback', runId, status: 'blocked' };\n    }\n  }\n\n  async loadEnvironmentBlockers(): Promise<EnvironmentRuntimeSnapshot> {\n    if (runtimeConfig.demoMode) return demoEnvironmentSnapshot(this.projectId);\n    try {\n      const blockers = await requestJson<Record<string, unknown>>({ method: 'GET', path: `/api/v1/projects/${this.projectId}/environment/blockers` });\n      return normalizeEnvironmentRuntimeSnapshot({ ...blockers, source: 'phase104-api' }, this.projectId);\n    } catch (error) {\n      if (!runtimeConfig.fallbackToDemo) throw error;\n      return { ...demoEnvironmentSnapshot(this.projectId), source: 'demo-fallback' };\n    }\n  }\n}\n""",
    )


def _write_environment_hook(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/hooks/useEnvironmentDiagnosisRuntime.ts",
        """import { useCallback, useEffect, useMemo, useRef, useState } from 'react';\nimport { useSelectedProjectId } from './useSelectedProjectId';\nimport { EnvironmentDiagnosisRuntime } from '../services/environmentDiagnosisRuntime';\nimport type { EnvironmentRuntimeSnapshot } from '../app/environmentRuntimeTypes';\n\nexport function useEnvironmentDiagnosisRuntime(projectId?: string) {\n  const selectedProjectId = useSelectedProjectId();\n  const resolvedProjectId = projectId || selectedProjectId;\n  const runtime = useMemo(() => new EnvironmentDiagnosisRuntime(resolvedProjectId), [resolvedProjectId]);\n  const timerRef = useRef<number | undefined>();\n  const [snapshot, setSnapshot] = useState<EnvironmentRuntimeSnapshot | null>(null);\n  const [loading, setLoading] = useState(true);\n  const [pollingStatus, setPollingStatus] = useState<'idle' | 'polling' | 'stopped'>('idle');\n  const [error, setError] = useState<string | null>(null);\n\n  const stopPolling = useCallback(() => {\n    if (timerRef.current) window.clearInterval(timerRef.current);\n    timerRef.current = undefined;\n    setPollingStatus('stopped');\n  }, []);\n\n  const refresh = useCallback(() => {\n    setLoading(true);\n    runtime.loadReadiness()\n      .then((payload) => { setSnapshot(payload); setError(null); })\n      .catch((err: unknown) => setError(err instanceof Error ? err.message : '环境诊断加载失败'))\n      .finally(() => setLoading(false));\n  }, [runtime]);\n\n  useEffect(() => {\n    stopPolling();\n    setSnapshot(null);\n    refresh();\n    return () => { if (timerRef.current) window.clearInterval(timerRef.current); };\n  }, [refresh, stopPolling, resolvedProjectId]);\n\n  const startPolling = useCallback((runId: string) => {\n    stopPolling();\n    setPollingStatus('polling');\n    timerRef.current = window.setInterval(() => {\n      runtime.pollEnvironmentDiagnosis(runId)\n        .then((payload) => setSnapshot(payload))\n        .catch((err: unknown) => setError(err instanceof Error ? err.message : '环境诊断轮询失败'));\n    }, 2500);\n  }, [runtime, stopPolling]);\n\n  const startPreflight = useCallback(() => {\n    setLoading(true);\n    runtime.triggerEnvironmentPreflight()\n      .then((payload) => {\n        setSnapshot(payload);\n        setError(null);\n        startPolling(payload.runId);\n      })\n      .catch((err: unknown) => setError(err instanceof Error ? err.message : '环境预检触发失败'))\n      .finally(() => setLoading(false));\n  }, [runtime, startPolling]);\n\n  return {\n    loading,\n    error,\n    snapshot,\n    pollingStatus,\n    startPreflight,\n    startPolling,\n    stopPolling,\n    retry: refresh,\n    blockers: snapshot?.blockers || [],\n    customerSupplementActions: snapshot?.customerSupplementActions || [],\n    pollingTimeline: snapshot?.pollingTimeline || [],\n  };\n}\n""",
    )


def _write_environment_components(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/components/EnvironmentReadinessPanel.tsx",
        """import type { EnvironmentRuntimeSnapshot } from '../app/environmentRuntimeTypes';\n\nexport function EnvironmentReadinessPanel({ snapshot }: { snapshot: EnvironmentRuntimeSnapshot | null }) {\n  return (\n    <section className=\"env-runtime-panel\">\n      <div>\n        <p className=\"eyebrow\">环境诊断真实触发</p>\n        <h2>项目级预检 / 只读安全执行</h2>\n        <p>真实 API mode 会触发 Phase104 API；demo fallback 会保留演示链路。</p>\n      </div>\n      <div className=\"env-runtime-score\">\n        <strong>{snapshot?.score ?? '--'}</strong>\n        <span>可测性评分</span>\n      </div>\n      <dl>\n        <div><dt>轮询状态</dt><dd>{snapshot?.status ?? 'idle'}</dd></div>\n        <div><dt>安全模式</dt><dd>{snapshot?.safeExecutionMode ?? 'read_only'}</dd></div>\n        <div><dt>数据来源</dt><dd>{snapshot?.source ?? 'demo-data'}</dd></div>\n      </dl>\n    </section>\n  );\n}\n""",
    )
    _write_text(
        app_dir / "src/components/EnvironmentBlockerList.tsx",
        """import type { CustomerSupplementAction, EnvironmentBlocker } from '../app/environmentRuntimeTypes';\n\nexport function EnvironmentBlockerList({ blockers, actions }: { blockers: EnvironmentBlocker[]; actions: CustomerSupplementAction[] }) {\n  return (\n    <section className=\"env-runtime-grid\">\n      <article>\n        <p className=\"eyebrow\">阻断原因</p>\n        <h3>环境阻断与业务影响</h3>\n        {blockers.map((blocker) => (\n          <div className=\"env-runtime-blocker\" key={blocker.id}>\n            <strong>{blocker.title}</strong>\n            <span>{blocker.layer} · {blocker.severity}</span>\n            <p>{blocker.businessImpact}</p>\n            <small>建议负责人：{blocker.suggestedOwner}</small>\n          </div>\n        ))}\n      </article>\n      <article>\n        <p className=\"eyebrow\">客户补料动作</p>\n        <h3>进入测试前需要客户补齐</h3>\n        {actions.map((action) => (\n          <div className=\"env-runtime-action\" key={action.id}>\n            <strong>{action.title}</strong>\n            <p>{action.requiredFrom} · {action.dueHint}</p>\n            <small>{action.safeToShare ? '默认脱敏，可安全交付' : '需要内部确认后再共享'}</small>\n          </div>\n        ))}\n      </article>\n    </section>\n  );\n}\n""",
    )
    _write_text(
        app_dir / "src/components/EnvironmentPollingTimeline.tsx",
        """import type { EnvironmentPollingEvent } from '../app/environmentRuntimeTypes';\n\nexport function EnvironmentPollingTimeline({ events }: { events: EnvironmentPollingEvent[] }) {\n  return (\n    <section className=\"env-runtime-timeline\">\n      <p className=\"eyebrow\">轮询状态</p>\n      <h3>预检执行时间线</h3>\n      {events.map((event, index) => (\n        <div className=\"env-runtime-event\" key={`${event.status}-${index}`}>\n          <span>{event.status}</span>\n          <p>{event.message}</p>\n        </div>\n      ))}\n    </section>\n  );\n}\n""",
    )


def _write_environment_page(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/pages/EnvironmentRuntimePage.tsx",
        """import { PageShell } from '../components/PageShell';\nimport { DataModeBadge } from '../components/DataModeBadge';\nimport { DangerConfirmButton } from '../components/DangerConfirmButton';\nimport { EnvironmentReadinessPanel } from '../components/EnvironmentReadinessPanel';\nimport { EnvironmentBlockerList } from '../components/EnvironmentBlockerList';\nimport { EnvironmentPollingTimeline } from '../components/EnvironmentPollingTimeline';\nimport { useEnvironmentDiagnosisRuntime } from '../hooks/useEnvironmentDiagnosisRuntime';\n\nexport function EnvironmentRuntimePage() {\n  const runtime = useEnvironmentDiagnosisRuntime();\n  return (\n    <PageShell\n      eyebrow=\"Phase106E · real API mode\"\n      title=\"环境诊断真实触发与轮询状态\"\n      description=\"围绕当前项目 ID 触发 Phase104 API 环境预检，轮询状态，展示阻断原因与客户补料动作。\"\n      actions={<DataModeBadge mode={runtime.snapshot?.source === 'phase104-api' ? 'real' : 'demo'} source={runtime.snapshot?.source || 'demo-data'} />}\n    >\n      <div className=\"env-runtime-toolbar\">\n        <DangerConfirmButton\n          label=\"触发项目级预检\"\n          title=\"触发环境预检\"\n          description=\"将对当前项目执行环境诊断请求（默认只读安全模式），用于生成阻断原因与客户补料动作。\"\n          onConfirm={runtime.startPreflight}\n          disabled={runtime.loading}\n        />\n        <button className=\"secondary\" onClick={runtime.retry}>重新读取环境诊断</button>\n        <button className=\"secondary\" onClick={runtime.stopPolling}>停止轮询</button>\n        <span>当前轮询状态：{runtime.pollingStatus}</span>\n      </div>\n      {runtime.error && <p className=\"env-runtime-error\">{runtime.error}</p>}\n      <EnvironmentReadinessPanel snapshot={runtime.snapshot} />\n      <EnvironmentBlockerList blockers={runtime.blockers} actions={runtime.customerSupplementActions} />\n      <EnvironmentPollingTimeline events={runtime.pollingTimeline} />\n    </PageShell>\n  );\n}\n""",
    )


def _write_contract_test(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/__tests__/environment-runtime-contract.test.ts",
        """import { describe, expect, it } from 'vitest';\nimport { normalizeEnvironmentRuntimeSnapshot } from '../app/environmentRuntimeTypes';\nimport { EnvironmentDiagnosisRuntime } from '../services/environmentDiagnosisRuntime';\n\ndescribe('environment runtime contract', () => {\n  it('normalizes blockers, customer supplement actions and polling timeline', () => {\n    const snapshot = normalizeEnvironmentRuntimeSnapshot({\n      project_id: 'project-a',\n      status: 'blocked',\n      score: 71,\n      blockers: [{ title: '认证阻断', layer: 'auth', severity: 'high' }],\n      customer_supplement_actions: [{ title: '补充测试账号', required_from: '客户 IAM' }],\n      polling_timeline: [{ status: 'running', message: '轮询中' }],\n    }, 'fallback-project');\n\n    expect(snapshot.projectId).toBe('project-a');\n    expect(snapshot.blockers.length).toBe(1);\n    expect(snapshot.customerSupplementActions.length).toBe(1);\n    expect(snapshot.pollingTimeline.length).toBe(1);\n  });\n\n  it('exposes trigger and polling methods for Phase104 API runtime', () => {\n    const runtime = new EnvironmentDiagnosisRuntime('project-a');\n    expect(runtime.loadReadiness).toBeTypeOf('function');\n    expect(runtime.triggerEnvironmentPreflight).toBeTypeOf('function');\n    expect(runtime.pollEnvironmentDiagnosis).toBeTypeOf('function');\n    expect(runtime.loadEnvironmentBlockers).toBeTypeOf('function');\n  });\n});\n""",
    )


def _write_css(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/styles/environment-runtime.css",
        """.env-runtime-toolbar { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; margin-bottom: 18px; }\n.env-runtime-toolbar button { border: 0; border-radius: 12px; padding: 10px 14px; font-weight: 700; cursor: pointer; }\n.env-runtime-toolbar .secondary { background: var(--qb-surface-muted); }\n.env-runtime-panel, .env-runtime-grid article, .env-runtime-timeline { background: var(--qb-surface); border: 1px solid var(--qb-border); border-radius: 18px; padding: 20px; box-shadow: var(--qb-shadow-soft); }\n.env-runtime-panel { display: grid; grid-template-columns: 1fr auto auto; gap: 20px; align-items: center; }\n.env-runtime-score { text-align: center; min-width: 110px; }\n.env-runtime-score strong { display: block; font-size: 42px; }\n.env-runtime-panel dl { display: grid; gap: 8px; margin: 0; }\n.env-runtime-panel dt { color: var(--qb-text-muted); font-size: 12px; }\n.env-runtime-panel dd { margin: 0; font-weight: 700; }\n.env-runtime-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; margin-top: 16px; }\n.env-runtime-blocker, .env-runtime-action, .env-runtime-event { border: 1px solid var(--qb-border); border-radius: 14px; padding: 14px; margin-top: 12px; }\n.env-runtime-blocker span, .env-runtime-event span { display: inline-block; font-size: 12px; font-weight: 700; }\n.env-runtime-timeline { margin-top: 16px; }\n.env-runtime-error { border: 1px solid var(--qb-danger); border-radius: 12px; padding: 12px; }\n@media (max-width: 980px) { .env-runtime-panel, .env-runtime-grid { grid-template-columns: 1fr; } }\n""",
    )


def _patch_routes_and_app(app_dir: Path) -> None:
    routes = app_dir / "src/routes.ts"
    routes_text = _read_text(routes)
    route_line = "  { path: '/environment-runtime', key: 'environment_runtime', label: '环境诊断真实触发', component: 'EnvironmentRuntimePage' },\n"
    if "EnvironmentRuntimePage" not in routes_text:
        routes_text = routes_text.replace("  { path: '/business-flow'", route_line + "  { path: '/business-flow'")
        _write_text(routes, routes_text)

    app = app_dir / "src/App.tsx"
    app_text = _read_text(app)
    if "EnvironmentRuntimePage" not in app_text:
        app_text = app_text.replace("import { ProjectDetailPage } from './pages/ProjectDetailPage';", "import { ProjectDetailPage } from './pages/ProjectDetailPage';\nimport { EnvironmentRuntimePage } from './pages/EnvironmentRuntimePage';")
        app_text = app_text.replace("import './styles/project-routes.css';", "import './styles/project-routes.css';\nimport './styles/environment-runtime.css';")
        app_text = app_text.replace("    case '/business-flow': return <BusinessFlowMapPage />;", "    case '/environment-runtime': return <EnvironmentRuntimePage />;\n    case '/business-flow': return <BusinessFlowMapPage />;")
        _write_text(app, app_text)

    demo = app_dir / "src/data/demoData.ts"
    demo_text = _read_text(demo)
    if "environment_runtime" not in demo_text and "projectRouteInventory" in demo_text:
        demo_text = demo_text.replace("{ path: '/projects', key: 'projects', label: '项目列表', component: 'ProjectListPage' },", "{ path: '/projects', key: 'projects', label: '项目列表', component: 'ProjectListPage' },\n  { path: '/environment-runtime', key: 'environment_runtime', label: '环境诊断真实触发', component: 'EnvironmentRuntimePage' },")
        _write_text(demo, demo_text)


def _write_readme(app_dir: Path) -> None:
    _write_text(
        app_dir / "README_FRONTEND_ENVIRONMENT_RUNTIME.md",
        """# Phase106E Frontend Environment Runtime\n\n本阶段把环境诊断页面从静态展示推进到真实运行边界：\n\n- 环境诊断真实触发\n- 项目级预检\n- 轮询状态\n- 阻断原因\n- 客户补料动作\n- 只读安全执行\n- real API mode / demo fallback\n- Phase104 API 接入边界\n- 默认脱敏\n\n启动前端后打开：\n\n```text\nhttp://127.0.0.1:5173/environment-runtime\n```\n\n真实 API 模式示例：\n\n```text\nVITE_QUALIBUG_DEMO_MODE=false\nVITE_QUALIBUG_FALLBACK_TO_DEMO=true\nVITE_QUALIBUG_API_BASE_URL=http://127.0.0.1:8790\nVITE_QUALIBUG_SAFE_EXECUTION_MODE=read_only\n```\n""",
    )


def _write_report_files(root: Path, report: FrontendEnvironmentRuntimeAcceptanceReport) -> None:
    _write_text(root / ENVIRONMENT_RUNTIME_ACCEPTANCE_JSON, _json_dump(report.to_dict()))
    lines = [
        f"# Phase106E 前端环境诊断运行态验收报告",
        "",
        f"- version: `{report.version}`",
        f"- passed: `{report.passed}`",
        f"- score: `{report.score}`",
        f"- app_dir: `{report.app_dir}`",
        "",
        "## Checks",
    ]
    for check in report.checks:
        marker = "✅" if check.passed else "❌"
        lines.append(f"- {marker} **{check.key}**: {check.detail}")
    _write_text(root / ENVIRONMENT_RUNTIME_ACCEPTANCE_MD, "\n".join(lines) + "\n")


def _write_manifest_files(root: Path, report: FrontendEnvironmentRuntimeAcceptanceReport) -> dict[str, Any]:
    manifest = {
        "version": PHASE106E_VERSION,
        "generated_at": _now(),
        "scenario": report.scenario,
        "app_dir": FRONTEND_APP_DIR,
        "route": "/environment-runtime",
        "core_labels": list(CORE_ENVIRONMENT_RUNTIME_LABELS),
        "runtime_endpoints": list(ENVIRONMENT_RUNTIME_ENDPOINTS),
        "acceptance": {"passed": report.passed, "score": report.score},
        "artifacts": report.artifacts,
    }
    _write_text(root / ENVIRONMENT_RUNTIME_MANIFEST_JSON, _json_dump(manifest))
    lines = [
        "# Phase106E Frontend Environment Runtime Manifest",
        "",
        f"- version: `{PHASE106E_VERSION}`",
        "- route: `/environment-runtime`",
        f"- score: `{report.score}`",
        "",
        "## Runtime Endpoints",
    ]
    for endpoint in ENVIRONMENT_RUNTIME_ENDPOINTS:
        lines.append(f"- `{endpoint['method']}` `{endpoint['path']}` → `{endpoint['client']}` / {endpoint['purpose']}")
    _write_text(root / ENVIRONMENT_RUNTIME_MANIFEST_MD, "\n".join(lines) + "\n")
    return manifest


def scan_frontend_environment_runtime_for_secret_leaks(output_dir: str | Path) -> list[str]:
    root = Path(output_dir)
    leaks: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() in {".zip", ".png", ".jpg", ".jpeg", ".gif"} or "node_modules" in path.parts:
            continue
        text = _read_text(path)
        for pattern in FORBIDDEN_ENVIRONMENT_RUNTIME_PATTERNS:
            if pattern in text:
                leaks.append(f"{path.relative_to(root).as_posix()} contains {pattern}")
    return leaks


def build_frontend_environment_runtime(
    output_dir: str | Path,
    *,
    scenario: str = "manufacturing",
    clean: bool = True,
) -> FrontendEnvironmentRuntimeAcceptanceReport:
    root = Path(output_dir)
    if clean and root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)

    build_frontend_project_routes(root, scenario=scenario, clean=False)
    app_dir = root / FRONTEND_APP_DIR
    _write_environment_types(app_dir)
    _write_environment_service(app_dir)
    _write_environment_hook(app_dir)
    _write_environment_components(app_dir)
    _write_environment_page(app_dir)
    _write_contract_test(app_dir)
    _write_css(app_dir)
    _patch_routes_and_app(app_dir)
    _write_readme(app_dir)

    report = validate_frontend_environment_runtime(root, scenario=scenario, write_report=True, skip_checksum=True)
    _write_manifest_files(root, report)
    write_frontend_environment_runtime_checksums(root)
    _zip_environment_runtime(root)
    report = validate_frontend_environment_runtime(root, scenario=scenario, write_report=True)
    _write_manifest_files(root, report)
    write_frontend_environment_runtime_checksums(root)
    _zip_environment_runtime(root)
    return report


def validate_frontend_environment_runtime(
    output_dir: str | Path,
    *,
    scenario: str = "manufacturing",
    write_report: bool = True,
    skip_checksum: bool = False,
) -> FrontendEnvironmentRuntimeAcceptanceReport:
    root = Path(output_dir)
    app_dir = root / FRONTEND_APP_DIR
    checks: list[FrontendEnvironmentRuntimeCheck] = []

    missing = [relative for relative in REQUIRED_ENVIRONMENT_RUNTIME_FILES if not (root / relative).exists()]
    if skip_checksum:
        missing = [relative for relative in missing if relative != ENVIRONMENT_RUNTIME_CHECKSUMS]
    if not skip_checksum and ENVIRONMENT_RUNTIME_ZIP in missing:
        missing.remove(ENVIRONMENT_RUNTIME_ZIP)
    checks.append(FrontendEnvironmentRuntimeCheck("required_files", not missing, "环境诊断运行态必需文件完整" if not missing else f"缺失文件: {missing}"))

    routes_text = _read_text(app_dir / "src/routes.ts")
    routes_ok = "'/environment-runtime'" in routes_text and "EnvironmentRuntimePage" in routes_text
    checks.append(FrontendEnvironmentRuntimeCheck("environment_runtime_route", routes_ok, "已注册 /environment-runtime 路由" if routes_ok else "环境运行态路由未注册"))

    app_text = _read_text(app_dir / "src/App.tsx")
    app_ok = "EnvironmentRuntimePage" in app_text and "environment-runtime.css" in app_text and "case '/environment-runtime'" in app_text
    checks.append(FrontendEnvironmentRuntimeCheck("app_resolution", app_ok, "App 已接入环境运行态页面" if app_ok else "App 未完整接入环境运行态页面"))

    service_text = _read_text(app_dir / "src/services/environmentDiagnosisRuntime.ts")
    service_ok = all(keyword in service_text for keyword in ("EnvironmentDiagnosisRuntime", "triggerEnvironmentPreflight", "pollEnvironmentDiagnosis", "loadEnvironmentBlockers", "runEnvironmentPreflight", "requestJson", "demo-fallback"))
    checks.append(FrontendEnvironmentRuntimeCheck("runtime_service", service_ok, "环境诊断 runtime 支持触发、轮询、阻断读取和 demo fallback" if service_ok else "环境诊断 runtime 服务不完整"))

    hook_text = _read_text(app_dir / "src/hooks/useEnvironmentDiagnosisRuntime.ts")
    hook_ok = all(keyword in hook_text for keyword in ("useEnvironmentDiagnosisRuntime", "pollingStatus", "startPreflight", "startPolling", "stopPolling", "customerSupplementActions", "retry"))
    checks.append(FrontendEnvironmentRuntimeCheck("runtime_hook", hook_ok, "Hook 已支持触发、轮询、停止、重试和客户补料动作" if hook_ok else "环境 runtime Hook 不完整"))

    components_text = "\n".join(_read_text(app_dir / relative) for relative in (
        "src/components/EnvironmentReadinessPanel.tsx",
        "src/components/EnvironmentBlockerList.tsx",
        "src/components/EnvironmentPollingTimeline.tsx",
        "src/pages/EnvironmentRuntimePage.tsx",
    ))
    missing_labels = [label for label in ("环境诊断真实触发", "轮询状态", "阻断原因", "客户补料动作", "只读安全执行", "项目级预检", "Phase104 API") if label not in components_text]
    checks.append(FrontendEnvironmentRuntimeCheck("business_semantics", not missing_labels, "页面覆盖环境诊断真实触发关键语义" if not missing_labels else f"缺失文案: {missing_labels}"))

    types_text = _read_text(app_dir / "src/app/environmentRuntimeTypes.ts")
    types_ok = all(keyword in types_text for keyword in ("EnvironmentRuntimeSnapshot", "EnvironmentBlocker", "CustomerSupplementAction", "EnvironmentPollingEvent", "normalizeEnvironmentRuntimeSnapshot"))
    checks.append(FrontendEnvironmentRuntimeCheck("runtime_types", types_ok, "环境运行态类型模型完整" if types_ok else "环境运行态类型模型不完整"))

    contract_test = _read_text(app_dir / "src/__tests__/environment-runtime-contract.test.ts")
    contract_ok = all(keyword in contract_test for keyword in ("normalizeEnvironmentRuntimeSnapshot", "EnvironmentDiagnosisRuntime", "triggerEnvironmentPreflight", "pollEnvironmentDiagnosis", "loadEnvironmentBlockers"))
    checks.append(FrontendEnvironmentRuntimeCheck("contract_test", contract_ok, "已生成环境运行态合同测试" if contract_ok else "合同测试覆盖不足"))

    manifest = _read_json(root / ENVIRONMENT_RUNTIME_MANIFEST_JSON)
    manifest_ok = manifest.get("version") == PHASE106E_VERSION and manifest.get("route") == "/environment-runtime" and len(manifest.get("runtime_endpoints") or []) >= 4
    checks.append(FrontendEnvironmentRuntimeCheck("manifest", manifest_ok, "manifest 描述环境运行态路由与 API 合同" if manifest_ok else "manifest 内容不完整"))

    if skip_checksum:
        checksum_ok = True
        checksum_detail = "构建中跳过 checksum 复验"
    else:
        checksum_failures = verify_frontend_environment_runtime_checksums(root)
        checksum_ok = not checksum_failures
        checksum_detail = "checksum 复验通过" if checksum_ok else f"checksum 失败: {checksum_failures}"
    checks.append(FrontendEnvironmentRuntimeCheck("checksums", checksum_ok, checksum_detail))

    leaks = scan_frontend_environment_runtime_for_secret_leaks(root)
    checks.append(FrontendEnvironmentRuntimeCheck("secret_leak_scan", not leaks, "未发现高风险敏感信息泄露模式" if not leaks else f"发现泄露风险: {leaks}"))

    passed = all(check.passed for check in checks)
    score = round(sum(1 for check in checks if check.passed) / len(checks) * 100) if checks else 0
    report = FrontendEnvironmentRuntimeAcceptanceReport(
        passed=passed,
        score=score,
        version=PHASE106E_VERSION,
        scenario=scenario,
        output_dir=str(root),
        app_dir=str(app_dir),
        checks=checks,
        artifacts={
            "app_dir": FRONTEND_APP_DIR,
            "route": "/environment-runtime",
            "manifest_json": ENVIRONMENT_RUNTIME_MANIFEST_JSON,
            "acceptance_json": ENVIRONMENT_RUNTIME_ACCEPTANCE_JSON,
            "checksums": ENVIRONMENT_RUNTIME_CHECKSUMS,
            "zip": ENVIRONMENT_RUNTIME_ZIP,
        },
    )
    if write_report:
        _write_report_files(root, report)
    return report


def run_frontend_environment_runtime_export(
    output_dir: str | Path,
    *,
    scenario: str = "manufacturing",
    validate_only: bool = False,
) -> FrontendEnvironmentRuntimeAcceptanceReport:
    if validate_only:
        return validate_frontend_environment_runtime(output_dir, scenario=scenario, write_report=True)
    return build_frontend_environment_runtime(output_dir, scenario=scenario)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate Phase106E frontend environment runtime app")
    parser.add_argument("--scenario", default="manufacturing")
    parser.add_argument("--output-dir", default="outputs/phase106_frontend_environment_runtime")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)

    report = run_frontend_environment_runtime_export(args.output_dir, scenario=args.scenario, validate_only=args.validate_only)
    print(_json_dump(report.to_dict()))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
