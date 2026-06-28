from __future__ import annotations

"""Phase106F: project-scoped AI test plan generation and read-only execution launch runtime.

Phase106E made environment diagnosis executable from the frontend. Phase106F turns
AI test planning into the next executable frontend runtime boundary:

* project-scoped test plan generation
* executable / blocked probe normalization
* read-only execution launch with runId handoff
* execution status polling timeline
* demo mode / real API mode / demo fallback safety

The repository root still only receives a Python generator and tests. The React
application is emitted into an output directory so the frontend can evolve
without adding npm dependencies to the repository root.
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
from ai_test_asset_center.phase106_frontend_environment_runtime import (
    FRONTEND_APP_DIR,
    build_frontend_environment_runtime,
)

PHASE106F_VERSION = "phase106f-frontend-test-plan-runtime-v1"

TEST_PLAN_RUNTIME_MANIFEST_JSON = "frontend_test_plan_runtime_manifest.json"
TEST_PLAN_RUNTIME_MANIFEST_MD = "frontend_test_plan_runtime_manifest.md"
TEST_PLAN_RUNTIME_ACCEPTANCE_JSON = "frontend_test_plan_runtime_acceptance_report.json"
TEST_PLAN_RUNTIME_ACCEPTANCE_MD = "frontend_test_plan_runtime_acceptance_report.md"
TEST_PLAN_RUNTIME_CHECKSUMS = "CHECKSUMS_PHASE106F.sha256"
TEST_PLAN_RUNTIME_ZIP = "phase106_frontend_test_plan_runtime.zip"

REQUIRED_TEST_PLAN_RUNTIME_FILES: tuple[str, ...] = (
    f"{FRONTEND_APP_DIR}/src/app/testPlanRuntimeTypes.ts",
    f"{FRONTEND_APP_DIR}/src/services/testPlanExecutionRuntime.ts",
    f"{FRONTEND_APP_DIR}/src/hooks/useTestPlanExecutionRuntime.ts",
    f"{FRONTEND_APP_DIR}/src/components/TestPlanSummaryPanel.tsx",
    f"{FRONTEND_APP_DIR}/src/components/ProbeExecutionTable.tsx",
    f"{FRONTEND_APP_DIR}/src/components/ExecutionLaunchPanel.tsx",
    f"{FRONTEND_APP_DIR}/src/components/ExecutionRunTimeline.tsx",
    f"{FRONTEND_APP_DIR}/src/pages/TestPlanRuntimePage.tsx",
    f"{FRONTEND_APP_DIR}/src/__tests__/test-plan-runtime-contract.test.ts",
    f"{FRONTEND_APP_DIR}/src/styles/test-plan-runtime.css",
    f"{FRONTEND_APP_DIR}/README_FRONTEND_TEST_PLAN_RUNTIME.md",
    TEST_PLAN_RUNTIME_MANIFEST_JSON,
    TEST_PLAN_RUNTIME_MANIFEST_MD,
    TEST_PLAN_RUNTIME_ACCEPTANCE_JSON,
    TEST_PLAN_RUNTIME_ACCEPTANCE_MD,
    TEST_PLAN_RUNTIME_CHECKSUMS,
    TEST_PLAN_RUNTIME_ZIP,
)

CORE_TEST_PLAN_RUNTIME_LABELS: tuple[str, ...] = (
    "AI 测试计划真实生成",
    "执行启动",
    "可执行探针",
    "阻断探针",
    "runId",
    "只读安全执行",
    "项目级测试计划",
    "TestPlanExecutionRuntime",
    "useTestPlanExecutionRuntime",
    "demo fallback",
    "real API mode",
    "Phase104 API",
    "默认脱敏",
)

TEST_PLAN_RUNTIME_ENDPOINTS: tuple[dict[str, str], ...] = (
    {"method": "POST", "path": "/api/v1/projects/{projectId}/test-plan/generate", "client": "generateTestPlan", "purpose": "项目级生成 AI 测试计划"},
    {"method": "GET", "path": "/api/v1/projects/{projectId}/test-plan/{planId}", "client": "loadTestPlan", "purpose": "读取测试计划详情"},
    {"method": "POST", "path": "/api/v1/projects/{projectId}/test-execution/start", "client": "startReadOnlyExecution", "purpose": "启动只读测试执行并返回 runId"},
    {"method": "GET", "path": "/api/v1/projects/{projectId}/test-execution/{runId}", "client": "pollExecutionRun", "purpose": "轮询执行状态"},
    {"method": "GET", "path": "/api/v1/projects/{projectId}/test-execution/{runId}/events", "client": "loadExecutionEvents", "purpose": "读取执行事件流"},
)

FORBIDDEN_TEST_PLAN_RUNTIME_PATTERNS: tuple[str, ...] = (
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
class FrontendTestPlanRuntimeCheck:
    key: str
    passed: bool
    detail: str
    owner: str = "frontend"
    severity: str = "critical"


@dataclass
class FrontendTestPlanRuntimeAcceptanceReport:
    passed: bool
    score: int
    version: str
    scenario: str
    output_dir: str
    app_dir: str
    checks: list[FrontendTestPlanRuntimeCheck] = field(default_factory=list)
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
    excluded = {TEST_PLAN_RUNTIME_CHECKSUMS, TEST_PLAN_RUNTIME_ZIP}
    return [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and path.name not in excluded
        and not path.name.endswith(".pyc")
        and "node_modules" not in path.parts
    ]


def write_frontend_test_plan_runtime_checksums(output_dir: str | Path) -> dict[str, str]:
    root = Path(output_dir)
    checksums = {path.relative_to(root).as_posix(): _sha256(path) for path in _iter_checksum_files(root)}
    lines = [f"{digest}  {relative}" for relative, digest in sorted(checksums.items())]
    _write_text(root / TEST_PLAN_RUNTIME_CHECKSUMS, "\n".join(lines) + "\n")
    return checksums


def verify_frontend_test_plan_runtime_checksums(output_dir: str | Path) -> list[str]:
    root = Path(output_dir)
    checksum_path = root / TEST_PLAN_RUNTIME_CHECKSUMS
    if not checksum_path.exists():
        return [f"missing {TEST_PLAN_RUNTIME_CHECKSUMS}"]
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


def _zip_test_plan_runtime(output_dir: Path) -> Path:
    archive_path = output_dir / TEST_PLAN_RUNTIME_ZIP
    if archive_path.exists():
        archive_path.unlink()
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(output_dir.rglob("*")):
            if path.is_file() and path != archive_path and not path.name.endswith(".pyc") and "node_modules" not in path.parts:
                archive.write(path, path.relative_to(output_dir).as_posix())
    return archive_path


def _write_test_plan_types(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/app/testPlanRuntimeTypes.ts",
        """export type ProbeRuntimeStatus = 'planned' | 'executable' | 'blocked' | 'running' | 'passed' | 'failed';\nexport type ExecutionRunStatus = 'idle' | 'queued' | 'running' | 'completed' | 'blocked' | 'failed';\n\nexport interface RuntimeProbe {\n  id: string;\n  name: string;\n  businessFlow: string;\n  status: ProbeRuntimeStatus;\n  executionMode: 'read_only' | 'sandbox' | 'manual';\n  blockerReason?: string;\n  expectedEvidence: string;\n}\n\nexport interface ExecutionEvent {\n  at: string;\n  status: ExecutionRunStatus;\n  message: string;\n}\n\nexport interface TestPlanRuntimeSnapshot {\n  projectId: string;\n  planId: string;\n  runId: string;\n  status: ExecutionRunStatus;\n  source: 'demo-data' | 'phase104-api' | 'demo-fallback';\n  safeExecutionMode: string;\n  executableProbes: RuntimeProbe[];\n  blockedProbes: RuntimeProbe[];\n  executionTimeline: ExecutionEvent[];\n  nextActions: string[];\n}\n\nfunction normalizeProbe(raw: Record<string, unknown>, index: number, fallbackStatus: ProbeRuntimeStatus): RuntimeProbe {\n  return {\n    id: String(raw.id ?? raw.probe_id ?? `probe-${index + 1}`),\n    name: String(raw.name ?? raw.title ?? 'AI 测试探针'),\n    businessFlow: String(raw.business_flow ?? raw.businessFlow ?? '核心业务链路'),\n    status: String(raw.status ?? fallbackStatus) as ProbeRuntimeStatus,\n    executionMode: String(raw.execution_mode ?? raw.executionMode ?? 'read_only') as RuntimeProbe['executionMode'],\n    blockerReason: raw.blocker_reason || raw.blockerReason ? String(raw.blocker_reason ?? raw.blockerReason) : undefined,\n    expectedEvidence: String(raw.expected_evidence ?? raw.expectedEvidence ?? '请求摘要 / 响应摘要 / 业务断言'),\n  };\n}\n\nexport function normalizeTestPlanRuntimeSnapshot(raw: Record<string, unknown>, fallbackProjectId: string): TestPlanRuntimeSnapshot {\n  const executable = Array.isArray(raw.executable_probes ?? raw.executableProbes) ? (raw.executable_probes ?? raw.executableProbes) as unknown[] : [];\n  const blocked = Array.isArray(raw.blocked_probes ?? raw.blockedProbes) ? (raw.blocked_probes ?? raw.blockedProbes) as unknown[] : [];\n  const events = Array.isArray(raw.execution_timeline ?? raw.executionTimeline) ? (raw.execution_timeline ?? raw.executionTimeline) as unknown[] : [];\n  const actions = Array.isArray(raw.next_actions ?? raw.nextActions) ? (raw.next_actions ?? raw.nextActions) as unknown[] : [];\n  return {\n    projectId: String(raw.project_id ?? raw.projectId ?? fallbackProjectId),\n    planId: String(raw.plan_id ?? raw.planId ?? 'plan-demo'),\n    runId: String(raw.run_id ?? raw.runId ?? ''),\n    status: String(raw.status ?? 'idle') as ExecutionRunStatus,\n    source: String(raw.source ?? 'demo-data') as TestPlanRuntimeSnapshot['source'],\n    safeExecutionMode: String(raw.safe_execution_mode ?? raw.safeExecutionMode ?? 'read_only'),\n    executableProbes: executable.map((item, index) => normalizeProbe(item as Record<string, unknown>, index, 'executable')),\n    blockedProbes: blocked.map((item, index) => normalizeProbe(item as Record<string, unknown>, index, 'blocked')),\n    executionTimeline: events.map((item, index) => ({\n      at: String((item as Record<string, unknown>).at ?? new Date(Date.now() + index * 1000).toISOString()),\n      status: String((item as Record<string, unknown>).status ?? 'running') as ExecutionRunStatus,\n      message: String((item as Record<string, unknown>).message ?? '测试执行事件'),\n    })),\n    nextActions: actions.map((item) => String(item)),\n  };\n}\n""",
    )


def _write_test_plan_service(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/services/testPlanExecutionRuntime.ts",
        """import { runtimeConfig } from '../app/runtimeConfig';\nimport { qualiBugClient } from '../api/qualibugClient';\nimport { requestJson, redactApiError } from '../api/runtimeApi';\nimport { demoData } from '../data/demoData';\nimport { normalizeTestPlanRuntimeSnapshot, type TestPlanRuntimeSnapshot } from '../app/testPlanRuntimeTypes';\n\nfunction demoTestPlanSnapshot(projectId: string): TestPlanRuntimeSnapshot {\n  const flowName = String(demoData.businessFlows?.[0]?.name ?? '订单创建到质检放行');\n  return normalizeTestPlanRuntimeSnapshot({\n    project_id: projectId,\n    plan_id: 'demo-ai-test-plan',\n    run_id: 'demo-readonly-run',\n    status: 'idle',\n    source: 'demo-data',\n    safe_execution_mode: runtimeConfig.safeExecutionMode,\n    executable_probes: [\n      { id: 'probe-order-create', name: '订单创建 API Smoke', business_flow: flowName, status: 'executable', execution_mode: 'read_only', expected_evidence: '订单创建请求摘要、响应码、业务断言' },\n      { id: 'probe-quality-release', name: '质检放行链路一致性', business_flow: flowName, status: 'executable', execution_mode: 'read_only', expected_evidence: '质检状态迁移、放行前后快照' },\n      { id: 'probe-concurrency-read', name: '并发只读库存一致性探针', business_flow: '库存锁定与释放', status: 'executable', execution_mode: 'read_only', expected_evidence: '并发窗口、读一致性摘要' },\n    ],\n    blocked_probes: [\n      { id: 'probe-sso-write', name: 'SSO 后写入型回归探针', business_flow: flowName, status: 'blocked', execution_mode: 'manual', blocker_reason: '需要客户授权测试账号和写入窗口', expected_evidence: '客户授权后再生成写入证据' },\n    ],\n    execution_timeline: [\n      { status: 'queued', message: 'AI 测试计划已生成，等待只读执行启动' },\n      { status: 'running', message: '执行启动后将回传 runId 和事件流' },\n    ],\n    next_actions: ['确认只读安全执行模式', '补充阻断探针所需账号窗口', '启动可执行探针并观察 runId'],\n  }, projectId);\n}\n\nexport class TestPlanExecutionRuntime {\n  constructor(private readonly projectId: string) {}\n\n  async generateTestPlan(): Promise<TestPlanRuntimeSnapshot> {\n    const payload = { project_id: this.projectId, safe_execution_mode: runtimeConfig.safeExecutionMode };\n    if (runtimeConfig.demoMode) return demoTestPlanSnapshot(this.projectId);\n    try {\n      const plan = await qualiBugClient.generateTestPlan(this.projectId, payload);\n      return normalizeTestPlanRuntimeSnapshot({ ...plan, source: 'phase104-api' }, this.projectId);\n    } catch (error) {\n      if (!runtimeConfig.fallbackToDemo) throw error;\n      const fallback = demoTestPlanSnapshot(this.projectId);\n      return { ...fallback, source: 'demo-fallback', executionTimeline: [...fallback.executionTimeline, { at: new Date().toISOString(), status: 'failed', message: `真实 API 不可用，已安全回退：${JSON.stringify(redactApiError(error))}` }] };\n    }\n  }\n\n  async loadTestPlan(planId: string): Promise<TestPlanRuntimeSnapshot> {\n    if (runtimeConfig.demoMode) return { ...demoTestPlanSnapshot(this.projectId), planId };\n    try {\n      const plan = await requestJson<Record<string, unknown>>({ method: 'GET', path: `/api/v1/projects/${this.projectId}/test-plan/${planId}` });\n      return normalizeTestPlanRuntimeSnapshot({ ...plan, source: 'phase104-api' }, this.projectId);\n    } catch (error) {\n      if (!runtimeConfig.fallbackToDemo) throw error;\n      return { ...demoTestPlanSnapshot(this.projectId), source: 'demo-fallback', planId };\n    }\n  }\n\n  async startReadOnlyExecution(planId: string): Promise<TestPlanRuntimeSnapshot> {\n    const payload = { plan_id: planId, mode: 'read_only', safe_execution_mode: runtimeConfig.safeExecutionMode };\n    if (runtimeConfig.demoMode) return { ...demoTestPlanSnapshot(this.projectId), planId, runId: 'demo-readonly-run-001', status: 'queued' };\n    try {\n      const run = await requestJson<Record<string, unknown>>({ method: 'POST', path: `/api/v1/projects/${this.projectId}/test-execution/start`, body: payload });\n      return normalizeTestPlanRuntimeSnapshot({ ...run, source: 'phase104-api', status: run.status ?? 'queued' }, this.projectId);\n    } catch (error) {\n      if (!runtimeConfig.fallbackToDemo) throw error;\n      return { ...demoTestPlanSnapshot(this.projectId), source: 'demo-fallback', planId, runId: 'demo-fallback-run', status: 'queued' };\n    }\n  }\n\n  async pollExecutionRun(runId: string): Promise<TestPlanRuntimeSnapshot> {\n    if (runtimeConfig.demoMode) return { ...demoTestPlanSnapshot(this.projectId), runId, status: 'running' };\n    try {\n      const run = await requestJson<Record<string, unknown>>({ method: 'GET', path: `/api/v1/projects/${this.projectId}/test-execution/${runId}` });\n      return normalizeTestPlanRuntimeSnapshot({ ...run, source: 'phase104-api' }, this.projectId);\n    } catch (error) {\n      if (!runtimeConfig.fallbackToDemo) throw error;\n      return { ...demoTestPlanSnapshot(this.projectId), source: 'demo-fallback', runId, status: 'running' };\n    }\n  }\n\n  async loadExecutionEvents(runId: string): Promise<TestPlanRuntimeSnapshot> {\n    if (runtimeConfig.demoMode) return { ...demoTestPlanSnapshot(this.projectId), runId, status: 'running' };\n    try {\n      const events = await requestJson<Record<string, unknown>>({ method: 'GET', path: `/api/v1/projects/${this.projectId}/test-execution/${runId}/events` });\n      return normalizeTestPlanRuntimeSnapshot({ ...events, source: 'phase104-api', run_id: runId }, this.projectId);\n    } catch (error) {\n      if (!runtimeConfig.fallbackToDemo) throw error;\n      return { ...demoTestPlanSnapshot(this.projectId), source: 'demo-fallback', runId, status: 'running' };\n    }\n  }\n}\n""",
    )


def _write_test_plan_hook(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/hooks/useTestPlanExecutionRuntime.ts",
        """import { useCallback, useMemo, useRef, useState } from 'react';\nimport { readSelectedProjectId } from '../app/projectContext';\nimport { TestPlanExecutionRuntime } from '../services/testPlanExecutionRuntime';\nimport type { TestPlanRuntimeSnapshot } from '../app/testPlanRuntimeTypes';\n\nexport function useTestPlanExecutionRuntime(projectId = readSelectedProjectId()) {\n  const runtime = useMemo(() => new TestPlanExecutionRuntime(projectId), [projectId]);\n  const timerRef = useRef<number | undefined>();\n  const [snapshot, setSnapshot] = useState<TestPlanRuntimeSnapshot | null>(null);\n  const [loading, setLoading] = useState(false);\n  const [pollingStatus, setPollingStatus] = useState<'idle' | 'polling' | 'stopped'>('idle');\n  const [error, setError] = useState<string | null>(null);\n\n  const generatePlan = useCallback(() => {\n    setLoading(true);\n    runtime.generateTestPlan()\n      .then((payload) => { setSnapshot(payload); setError(null); })\n      .catch((err) => setError(err instanceof Error ? err.message : '测试计划生成失败'))\n      .finally(() => setLoading(false));\n  }, [runtime]);\n\n  const startReadOnlyExecution = useCallback(() => {\n    const planId = snapshot?.planId || 'demo-ai-test-plan';\n    setLoading(true);\n    runtime.startReadOnlyExecution(planId)\n      .then((payload) => { setSnapshot(payload); setError(null); })\n      .catch((err) => setError(err instanceof Error ? err.message : '执行启动失败'))\n      .finally(() => setLoading(false));\n  }, [runtime, snapshot?.planId]);\n\n  const startPolling = useCallback(() => {\n    const runId = snapshot?.runId || 'demo-readonly-run';\n    window.clearInterval(timerRef.current);\n    setPollingStatus('polling');\n    timerRef.current = window.setInterval(() => {\n      runtime.pollExecutionRun(runId).then(setSnapshot).catch((err) => setError(err instanceof Error ? err.message : '执行轮询失败'));\n    }, 3000);\n  }, [runtime, snapshot?.runId]);\n\n  const stopPolling = useCallback(() => {\n    window.clearInterval(timerRef.current);\n    setPollingStatus('stopped');\n  }, []);\n\n  const loadEvents = useCallback(() => {\n    const runId = snapshot?.runId || 'demo-readonly-run';\n    runtime.loadExecutionEvents(runId).then(setSnapshot).catch((err) => setError(err instanceof Error ? err.message : '执行事件读取失败'));\n  }, [runtime, snapshot?.runId]);\n\n  return { snapshot, loading, pollingStatus, error, generatePlan, startReadOnlyExecution, startPolling, stopPolling, loadEvents };\n}\n""",
    )


def _write_test_plan_components(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/components/TestPlanSummaryPanel.tsx",
        """import type { TestPlanRuntimeSnapshot } from '../app/testPlanRuntimeTypes';\n\nexport function TestPlanSummaryPanel({ snapshot }: { snapshot: TestPlanRuntimeSnapshot | null }) {\n  return (\n    <section className=\"runtime-panel test-plan-summary\">\n      <div>\n        <p className=\"eyebrow\">AI 测试计划真实生成</p>\n        <h2>项目级测试计划</h2>\n        <p>从当前项目环境、业务链路和 Phase104 API 合同生成可执行 / 阻断探针清单。</p>\n      </div>\n      <div className=\"runtime-kpis\">\n        <span>planId <strong>{snapshot?.planId || '待生成'}</strong></span>\n        <span>runId <strong>{snapshot?.runId || '待启动'}</strong></span>\n        <span>只读安全执行 <strong>{snapshot?.safeExecutionMode || 'read_only'}</strong></span>\n        <span>来源 <strong>{snapshot?.source || 'demo fallback 可用'}</strong></span>\n      </div>\n    </section>\n  );\n}\n""",
    )
    _write_text(
        app_dir / "src/components/ProbeExecutionTable.tsx",
        """import type { RuntimeProbe } from '../app/testPlanRuntimeTypes';\n\nexport function ProbeExecutionTable({ title, probes }: { title: string; probes: RuntimeProbe[] }) {\n  return (\n    <section className=\"runtime-panel\">\n      <h3>{title}</h3>\n      <table className=\"probe-runtime-table\">\n        <thead><tr><th>探针</th><th>业务链路</th><th>状态</th><th>执行模式</th><th>证据</th></tr></thead>\n        <tbody>\n          {probes.map((probe) => (\n            <tr key={probe.id}>\n              <td><strong>{probe.name}</strong>{probe.blockerReason && <small>{probe.blockerReason}</small>}</td>\n              <td>{probe.businessFlow}</td>\n              <td>{probe.status}</td>\n              <td>{probe.executionMode}</td>\n              <td>{probe.expectedEvidence}</td>\n            </tr>\n          ))}\n        </tbody>\n      </table>\n    </section>\n  );\n}\n""",
    )
    _write_text(
        app_dir / "src/components/ExecutionLaunchPanel.tsx",
        """export function ExecutionLaunchPanel({ loading, onGenerate, onStart, onPoll, onStop, onEvents }: { loading: boolean; onGenerate: () => void; onStart: () => void; onPoll: () => void; onStop: () => void; onEvents: () => void }) {\n  return (\n    <section className=\"runtime-panel launch-panel\">\n      <div>\n        <p className=\"eyebrow\">执行启动</p>\n        <h3>只读执行控制</h3>\n        <p>先生成计划，再启动只读执行，随后通过 runId 轮询执行状态和事件流。</p>\n      </div>\n      <div className=\"runtime-actions\">\n        <button onClick={onGenerate} disabled={loading}>生成 AI 测试计划</button>\n        <button onClick={onStart} disabled={loading}>启动只读测试执行</button>\n        <button onClick={onPoll}>开始轮询 runId</button>\n        <button onClick={onEvents}>读取执行事件</button>\n        <button onClick={onStop}>停止轮询</button>\n      </div>\n    </section>\n  );\n}\n""",
    )
    _write_text(
        app_dir / "src/components/ExecutionRunTimeline.tsx",
        """import type { ExecutionEvent } from '../app/testPlanRuntimeTypes';\n\nexport function ExecutionRunTimeline({ events }: { events: ExecutionEvent[] }) {\n  return (\n    <section className=\"runtime-panel\">\n      <h3>执行状态轮询时间线</h3>\n      <ol className=\"execution-timeline\">\n        {events.map((event, index) => (\n          <li key={`${event.status}-${index}`}>\n            <span>{event.status}</span>\n            <p>{event.message}</p>\n            <small>{event.at}</small>\n          </li>\n        ))}\n      </ol>\n    </section>\n  );\n}\n""",
    )


def _write_test_plan_page(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/pages/TestPlanRuntimePage.tsx",
        """import { DataModeBadge } from '../components/DataModeBadge';\nimport { ExecutionLaunchPanel } from '../components/ExecutionLaunchPanel';\nimport { ExecutionRunTimeline } from '../components/ExecutionRunTimeline';\nimport { PageShell } from '../components/PageShell';\nimport { ProbeExecutionTable } from '../components/ProbeExecutionTable';\nimport { ProjectSwitcher } from '../components/ProjectSwitcher';\nimport { TestPlanSummaryPanel } from '../components/TestPlanSummaryPanel';\nimport { useTestPlanExecutionRuntime } from '../hooks/useTestPlanExecutionRuntime';\n\nexport function TestPlanRuntimePage() {\n  const { snapshot, loading, pollingStatus, error, generatePlan, startReadOnlyExecution, startPolling, stopPolling, loadEvents } = useTestPlanExecutionRuntime();\n  return (\n    <PageShell title=\"AI 测试计划真实生成\" eyebrow=\"Phase106F · real API mode / demo fallback\" actions={<><DataModeBadge /><ProjectSwitcher /></>}>\n      <section className=\"runtime-hero\">\n        <div>\n          <h1>生成计划 → 启动只读执行 → 回读 runId</h1>\n          <p>围绕当前项目调用 Phase104 API，生成可执行探针和阻断探针，并把执行启动结果回传到前端运行态。</p>\n        </div>\n        <div className=\"runtime-status-card\">\n          <span>轮询状态</span>\n          <strong>{pollingStatus}</strong>\n          <small>{error || '默认脱敏，真实 API 错误只显示安全摘要'}</small>\n        </div>\n      </section>\n      <ExecutionLaunchPanel loading={loading} onGenerate={generatePlan} onStart={startReadOnlyExecution} onPoll={startPolling} onStop={stopPolling} onEvents={loadEvents} />\n      <TestPlanSummaryPanel snapshot={snapshot} />\n      <div className=\"runtime-grid\">\n        <ProbeExecutionTable title=\"可执行探针\" probes={snapshot?.executableProbes || []} />\n        <ProbeExecutionTable title=\"阻断探针\" probes={snapshot?.blockedProbes || []} />\n      </div>\n      <ExecutionRunTimeline events={snapshot?.executionTimeline || []} />\n      <section className=\"runtime-panel\">\n        <h3>客户下一步动作</h3>\n        <ul>{(snapshot?.nextActions || ['点击生成 AI 测试计划', '确认只读安全执行模式', '启动可执行探针并观察 runId']).map((item) => <li key={item}>{item}</li>)}</ul>\n        <p className=\"runtime-note\">项目级测试计划、执行启动、runId 轮询、Phase104 API 合同、demo fallback、默认脱敏已接入。</p>\n      </section>\n    </PageShell>\n  );\n}\n""",
    )


def _write_contract_test(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/__tests__/test-plan-runtime-contract.test.ts",
        """import { describe, expect, it } from 'vitest';\nimport { normalizeTestPlanRuntimeSnapshot } from '../app/testPlanRuntimeTypes';\nimport { TestPlanExecutionRuntime } from '../services/testPlanExecutionRuntime';\n\ndescribe('Phase106F test plan runtime contract', () => {\n  it('normalizes executable probes, blocked probes and runId safely', () => {\n    const snapshot = normalizeTestPlanRuntimeSnapshot({\n      project_id: 'project-1',\n      plan_id: 'plan-1',\n      run_id: 'run-1',\n      status: 'running',\n      executable_probes: [{ id: 'p1', name: '订单 API 探针' }],\n      blocked_probes: [{ id: 'p2', blocker_reason: '需要客户授权窗口' }],\n      execution_timeline: [{ status: 'queued', message: '执行启动' }],\n    }, 'fallback');\n    expect(snapshot.projectId).toBe('project-1');\n    expect(snapshot.planId).toBe('plan-1');\n    expect(snapshot.runId).toBe('run-1');\n    expect(snapshot.executableProbes).toHaveLength(1);\n    expect(snapshot.blockedProbes).toHaveLength(1);\n  });\n\n  it('exposes project-scoped runtime methods', () => {\n    const runtime = new TestPlanExecutionRuntime('project-1');\n    expect(runtime.generateTestPlan).toBeTypeOf('function');\n    expect(runtime.loadTestPlan).toBeTypeOf('function');\n    expect(runtime.startReadOnlyExecution).toBeTypeOf('function');\n    expect(runtime.pollExecutionRun).toBeTypeOf('function');\n    expect(runtime.loadExecutionEvents).toBeTypeOf('function');\n  });\n});\n""",
    )


def _write_css(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/styles/test-plan-runtime.css",
        """.runtime-hero { display: grid; grid-template-columns: 1fr 280px; gap: 18px; align-items: stretch; margin-bottom: 18px; }\n.runtime-status-card, .runtime-panel { border: 1px solid var(--qb-border); border-radius: 18px; background: var(--qb-surface); padding: 18px; box-shadow: var(--qb-shadow); }\n.runtime-status-card span, .eyebrow { color: var(--qb-muted); font-size: 12px; letter-spacing: .08em; text-transform: uppercase; }\n.runtime-status-card strong { display: block; font-size: 28px; margin: 8px 0; }\n.runtime-kpis { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin-top: 14px; }\n.runtime-kpis span { background: var(--qb-soft); border-radius: 14px; padding: 12px; color: var(--qb-muted); }\n.runtime-kpis strong { display: block; color: var(--qb-text); margin-top: 4px; }\n.runtime-actions { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 12px; }\n.runtime-actions button { border: 0; border-radius: 12px; padding: 10px 14px; background: var(--qb-primary); color: white; cursor: pointer; }\n.runtime-actions button:disabled { opacity: .55; cursor: not-allowed; }\n.runtime-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; margin: 18px 0; }\n.probe-runtime-table { width: 100%; border-collapse: collapse; }\n.probe-runtime-table th, .probe-runtime-table td { border-bottom: 1px solid var(--qb-border); padding: 10px; text-align: left; vertical-align: top; }\n.probe-runtime-table small { display: block; color: var(--qb-danger); margin-top: 4px; }\n.execution-timeline { display: grid; gap: 10px; padding-left: 0; list-style: none; }\n.execution-timeline li { border-left: 4px solid var(--qb-primary); padding: 10px 12px; background: var(--qb-soft); border-radius: 12px; }\n.execution-timeline span { font-weight: 700; }\n.execution-timeline small, .runtime-note { color: var(--qb-muted); }\n@media (max-width: 960px) { .runtime-hero, .runtime-grid, .runtime-kpis { grid-template-columns: 1fr; } }\n""",
    )


def _patch_routes_and_app(app_dir: Path) -> None:
    routes_path = app_dir / "src/routes.ts"
    routes = _read_text(routes_path)
    if "TestPlanRuntimePage" not in routes:
        routes = routes.replace(
            "  { path: '/test-execution', key: 'test_execution', label: 'AI 测试计划 / 实时测试执行', component: 'TestExecutionPage' },",
            "  { path: '/test-execution', key: 'test_execution', label: 'AI 测试计划 / 实时测试执行', component: 'TestExecutionPage' },\n  { path: '/test-plan-runtime', key: 'test_plan_runtime', label: 'AI 测试计划真实生成', component: 'TestPlanRuntimePage' },",
        )
    _write_text(routes_path, routes)

    app_path = app_dir / "src/App.tsx"
    app = _read_text(app_path)
    if "TestPlanRuntimePage" not in app:
        app = app.replace("import { EnvironmentRuntimePage } from './pages/EnvironmentRuntimePage';", "import { EnvironmentRuntimePage } from './pages/EnvironmentRuntimePage';\nimport { TestPlanRuntimePage } from './pages/TestPlanRuntimePage';")
        app = app.replace("import './styles/environment-runtime.css';", "import './styles/environment-runtime.css';\nimport './styles/test-plan-runtime.css';")
        app = app.replace("    case '/test-execution': return <TestExecutionPage />;", "    case '/test-execution': return <TestExecutionPage />;\n    case '/test-plan-runtime': return <TestPlanRuntimePage />;")
    _write_text(app_path, app)


def _write_readme(app_dir: Path) -> None:
    _write_text(
        app_dir / "README_FRONTEND_TEST_PLAN_RUNTIME.md",
        f"""# Phase106F Frontend Test Plan Runtime\n\nVersion: `{PHASE106F_VERSION}`\n\nThis generated frontend app adds `/test-plan-runtime` as the project-scoped AI test plan runtime page.\n\n## Capabilities\n\n- AI 测试计划真实生成\n- 可执行探针 / 阻断探针归一化\n- 只读安全执行启动\n- runId 回传与轮询状态\n- real API mode / demo fallback\n- Phase104 API 路径合同\n- 默认脱敏：不展示原始 token、cookie、session、password 或 client secret\n\n## Local run\n\n```powershell\nnpm install\nnpm run dev\n```\n\nOpen `/test-plan-runtime`.\n""",
    )


def _write_report_files(root: Path, report: FrontendTestPlanRuntimeAcceptanceReport) -> None:
    _write_text(root / TEST_PLAN_RUNTIME_ACCEPTANCE_JSON, _json_dump(report.to_dict()))
    lines = [
        "# Phase106F Frontend Test Plan Runtime Acceptance Report",
        "",
        f"- version: `{report.version}`",
        f"- passed: `{report.passed}`",
        f"- score: `{report.score}`",
        f"- scenario: `{report.scenario}`",
        "",
        "## Checks",
    ]
    for check in report.checks:
        marker = "✅" if check.passed else "❌"
        lines.append(f"- {marker} **{check.key}**: {check.detail}")
    _write_text(root / TEST_PLAN_RUNTIME_ACCEPTANCE_MD, "\n".join(lines) + "\n")


def _write_manifest_files(root: Path, report: FrontendTestPlanRuntimeAcceptanceReport) -> dict[str, Any]:
    manifest = {
        "version": PHASE106F_VERSION,
        "generated_at": _now(),
        "scenario": report.scenario,
        "app_dir": FRONTEND_APP_DIR,
        "route": "/test-plan-runtime",
        "core_labels": list(CORE_TEST_PLAN_RUNTIME_LABELS),
        "runtime_endpoints": list(TEST_PLAN_RUNTIME_ENDPOINTS),
        "acceptance": {"passed": report.passed, "score": report.score},
        "artifacts": report.artifacts,
    }
    _write_text(root / TEST_PLAN_RUNTIME_MANIFEST_JSON, _json_dump(manifest))
    lines = [
        "# Phase106F Frontend Test Plan Runtime Manifest",
        "",
        f"- version: `{PHASE106F_VERSION}`",
        "- route: `/test-plan-runtime`",
        f"- score: `{report.score}`",
        "",
        "## Runtime Endpoints",
    ]
    for endpoint in TEST_PLAN_RUNTIME_ENDPOINTS:
        lines.append(f"- `{endpoint['method']}` `{endpoint['path']}` → `{endpoint['client']}` / {endpoint['purpose']}")
    _write_text(root / TEST_PLAN_RUNTIME_MANIFEST_MD, "\n".join(lines) + "\n")
    return manifest


def scan_frontend_test_plan_runtime_for_secret_leaks(output_dir: str | Path) -> list[str]:
    root = Path(output_dir)
    leaks: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() in {".zip", ".png", ".jpg", ".jpeg", ".gif"} or "node_modules" in path.parts:
            continue
        text = _read_text(path)
        for pattern in FORBIDDEN_TEST_PLAN_RUNTIME_PATTERNS:
            if pattern in text:
                leaks.append(f"{path.relative_to(root).as_posix()} contains {pattern}")
    return leaks


def build_frontend_test_plan_runtime(
    output_dir: str | Path,
    *,
    scenario: str = "manufacturing",
    clean: bool = True,
) -> FrontendTestPlanRuntimeAcceptanceReport:
    root = Path(output_dir)
    if clean and root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)

    build_frontend_environment_runtime(root, scenario=scenario)
    app_dir = root / FRONTEND_APP_DIR
    _write_test_plan_types(app_dir)
    _write_test_plan_service(app_dir)
    _write_test_plan_hook(app_dir)
    _write_test_plan_components(app_dir)
    _write_test_plan_page(app_dir)
    _write_contract_test(app_dir)
    _write_css(app_dir)
    _patch_routes_and_app(app_dir)
    _write_readme(app_dir)

    report = validate_frontend_test_plan_runtime(root, scenario=scenario, write_report=True, skip_checksum=True)
    _write_manifest_files(root, report)
    write_frontend_test_plan_runtime_checksums(root)
    _zip_test_plan_runtime(root)
    report = validate_frontend_test_plan_runtime(root, scenario=scenario, write_report=True)
    _write_manifest_files(root, report)
    write_frontend_test_plan_runtime_checksums(root)
    _zip_test_plan_runtime(root)
    return report


def validate_frontend_test_plan_runtime(
    output_dir: str | Path,
    *,
    scenario: str = "manufacturing",
    write_report: bool = True,
    skip_checksum: bool = False,
) -> FrontendTestPlanRuntimeAcceptanceReport:
    root = Path(output_dir)
    app_dir = root / FRONTEND_APP_DIR
    checks: list[FrontendTestPlanRuntimeCheck] = []

    missing = [relative for relative in REQUIRED_TEST_PLAN_RUNTIME_FILES if not (root / relative).exists()]
    if skip_checksum:
        missing = [relative for relative in missing if relative != TEST_PLAN_RUNTIME_CHECKSUMS]
    if not skip_checksum and TEST_PLAN_RUNTIME_ZIP in missing:
        missing.remove(TEST_PLAN_RUNTIME_ZIP)
    checks.append(FrontendTestPlanRuntimeCheck("required_files", not missing, "测试计划运行态必需文件完整" if not missing else f"缺失文件: {missing}"))

    routes_text = _read_text(app_dir / "src/routes.ts")
    routes_ok = "'/test-plan-runtime'" in routes_text and "TestPlanRuntimePage" in routes_text
    checks.append(FrontendTestPlanRuntimeCheck("test_plan_runtime_route", routes_ok, "已注册 /test-plan-runtime 路由" if routes_ok else "测试计划运行态路由未注册"))

    app_text = _read_text(app_dir / "src/App.tsx")
    app_ok = "TestPlanRuntimePage" in app_text and "test-plan-runtime.css" in app_text and "case '/test-plan-runtime'" in app_text
    checks.append(FrontendTestPlanRuntimeCheck("app_resolution", app_ok, "App 已接入测试计划运行态页面" if app_ok else "App 未完整接入测试计划运行态页面"))

    service_text = _read_text(app_dir / "src/services/testPlanExecutionRuntime.ts")
    service_ok = all(keyword in service_text for keyword in ("TestPlanExecutionRuntime", "generateTestPlan", "loadTestPlan", "startReadOnlyExecution", "pollExecutionRun", "loadExecutionEvents", "demo-fallback", "requestJson"))
    checks.append(FrontendTestPlanRuntimeCheck("runtime_service", service_ok, "测试计划 runtime 支持生成、启动、轮询、事件读取和 demo fallback" if service_ok else "测试计划 runtime 服务不完整"))

    hook_text = _read_text(app_dir / "src/hooks/useTestPlanExecutionRuntime.ts")
    hook_ok = all(keyword in hook_text for keyword in ("useTestPlanExecutionRuntime", "generatePlan", "startReadOnlyExecution", "startPolling", "stopPolling", "loadEvents", "pollingStatus"))
    checks.append(FrontendTestPlanRuntimeCheck("runtime_hook", hook_ok, "Hook 已支持计划生成、只读执行启动、轮询、停止和事件读取" if hook_ok else "测试计划 runtime Hook 不完整"))

    components_text = "\n".join(_read_text(app_dir / relative) for relative in (
        "src/components/TestPlanSummaryPanel.tsx",
        "src/components/ProbeExecutionTable.tsx",
        "src/components/ExecutionLaunchPanel.tsx",
        "src/components/ExecutionRunTimeline.tsx",
        "src/pages/TestPlanRuntimePage.tsx",
    ))
    missing_labels = [label for label in ("AI 测试计划真实生成", "执行启动", "可执行探针", "阻断探针", "runId", "只读安全执行", "项目级测试计划", "Phase104 API") if label not in components_text]
    checks.append(FrontendTestPlanRuntimeCheck("business_semantics", not missing_labels, "页面覆盖测试计划真实生成关键语义" if not missing_labels else f"缺失文案: {missing_labels}"))

    types_text = _read_text(app_dir / "src/app/testPlanRuntimeTypes.ts")
    types_ok = all(keyword in types_text for keyword in ("TestPlanRuntimeSnapshot", "RuntimeProbe", "ExecutionEvent", "normalizeTestPlanRuntimeSnapshot", "executableProbes", "blockedProbes"))
    checks.append(FrontendTestPlanRuntimeCheck("runtime_types", types_ok, "测试计划运行态类型模型完整" if types_ok else "测试计划运行态类型模型不完整"))

    contract_test = _read_text(app_dir / "src/__tests__/test-plan-runtime-contract.test.ts")
    contract_ok = all(keyword in contract_test for keyword in ("normalizeTestPlanRuntimeSnapshot", "TestPlanExecutionRuntime", "generateTestPlan", "startReadOnlyExecution", "pollExecutionRun", "loadExecutionEvents"))
    checks.append(FrontendTestPlanRuntimeCheck("contract_test", contract_ok, "已生成测试计划运行态合同测试" if contract_ok else "合同测试覆盖不足"))

    manifest = _read_json(root / TEST_PLAN_RUNTIME_MANIFEST_JSON)
    manifest_ok = manifest.get("version") == PHASE106F_VERSION and manifest.get("route") == "/test-plan-runtime" and len(manifest.get("runtime_endpoints") or []) >= 5
    checks.append(FrontendTestPlanRuntimeCheck("manifest", manifest_ok, "manifest 描述测试计划运行态路由与 API 合同" if manifest_ok else "manifest 内容不完整"))

    if skip_checksum:
        checksum_ok = True
        checksum_detail = "构建中跳过 checksum 复验"
    else:
        checksum_failures = verify_frontend_test_plan_runtime_checksums(root)
        checksum_ok = not checksum_failures
        checksum_detail = "checksum 复验通过" if checksum_ok else f"checksum 失败: {checksum_failures}"
    checks.append(FrontendTestPlanRuntimeCheck("checksums", checksum_ok, checksum_detail))

    leaks = scan_frontend_test_plan_runtime_for_secret_leaks(root)
    checks.append(FrontendTestPlanRuntimeCheck("secret_leak_scan", not leaks, "未发现高风险敏感信息泄露模式" if not leaks else f"发现泄露风险: {leaks}"))

    passed = all(check.passed for check in checks)
    score = round(sum(1 for check in checks if check.passed) / len(checks) * 100) if checks else 0
    report = FrontendTestPlanRuntimeAcceptanceReport(
        passed=passed,
        score=score,
        version=PHASE106F_VERSION,
        scenario=scenario,
        output_dir=str(root),
        app_dir=str(app_dir),
        checks=checks,
        artifacts={
            "app_dir": FRONTEND_APP_DIR,
            "route": "/test-plan-runtime",
            "manifest_json": TEST_PLAN_RUNTIME_MANIFEST_JSON,
            "acceptance_json": TEST_PLAN_RUNTIME_ACCEPTANCE_JSON,
            "checksums": TEST_PLAN_RUNTIME_CHECKSUMS,
            "zip": TEST_PLAN_RUNTIME_ZIP,
        },
    )
    if write_report:
        _write_report_files(root, report)
    return report


def run_frontend_test_plan_runtime_export(
    output_dir: str | Path,
    *,
    scenario: str = "manufacturing",
    validate_only: bool = False,
) -> FrontendTestPlanRuntimeAcceptanceReport:
    if validate_only:
        return validate_frontend_test_plan_runtime(output_dir, scenario=scenario, write_report=True)
    return build_frontend_test_plan_runtime(output_dir, scenario=scenario)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate Phase106F frontend test plan runtime app")
    parser.add_argument("--scenario", default="manufacturing")
    parser.add_argument("--output-dir", default="outputs/phase106_frontend_test_plan_runtime")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)

    report = run_frontend_test_plan_runtime_export(args.output_dir, scenario=args.scenario, validate_only=args.validate_only)
    print(_json_dump(report.to_dict()))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
