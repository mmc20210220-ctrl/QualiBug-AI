
from __future__ import annotations

"""Phase106G: live execution event stream and risk evidence feedback runtime.

Phase106F launched project-scoped AI test plans and returned a runId. Phase106G
continues that runId into the realtime execution surface:

* project/run-scoped live execution status
* event stream polling and normalization
* risk signal feedback from execution
* evidence snapshot feedback and jump-to-evidence handoff
* demo mode / real API mode / demo fallback safety

The repository root only receives a Python generator and tests. The React app is
emitted into an output directory, keeping npm artifacts out of the root repo.
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
from ai_test_asset_center.phase106_frontend_test_plan_runtime import (
    FRONTEND_APP_DIR,
    build_frontend_test_plan_runtime,
)

PHASE106G_VERSION = "phase106g-frontend-execution-runtime-v1"

EXECUTION_RUNTIME_MANIFEST_JSON = "frontend_execution_runtime_manifest.json"
EXECUTION_RUNTIME_MANIFEST_MD = "frontend_execution_runtime_manifest.md"
EXECUTION_RUNTIME_ACCEPTANCE_JSON = "frontend_execution_runtime_acceptance_report.json"
EXECUTION_RUNTIME_ACCEPTANCE_MD = "frontend_execution_runtime_acceptance_report.md"
EXECUTION_RUNTIME_CHECKSUMS = "CHECKSUMS_PHASE106G.sha256"
EXECUTION_RUNTIME_ZIP = "phase106_frontend_execution_runtime.zip"

REQUIRED_EXECUTION_RUNTIME_FILES: tuple[str, ...] = (
    f"{FRONTEND_APP_DIR}/src/app/executionRuntimeTypes.ts",
    f"{FRONTEND_APP_DIR}/src/services/executionEventRuntime.ts",
    f"{FRONTEND_APP_DIR}/src/hooks/useExecutionEventRuntime.ts",
    f"{FRONTEND_APP_DIR}/src/components/LiveExecutionStatusPanel.tsx",
    f"{FRONTEND_APP_DIR}/src/components/ExecutionEventStream.tsx",
    f"{FRONTEND_APP_DIR}/src/components/RuntimeRiskSignalList.tsx",
    f"{FRONTEND_APP_DIR}/src/components/EvidenceSnapshotPanel.tsx",
    f"{FRONTEND_APP_DIR}/src/pages/ExecutionRuntimePage.tsx",
    f"{FRONTEND_APP_DIR}/src/__tests__/execution-runtime-contract.test.ts",
    f"{FRONTEND_APP_DIR}/src/styles/execution-runtime.css",
    f"{FRONTEND_APP_DIR}/README_FRONTEND_EXECUTION_RUNTIME.md",
    EXECUTION_RUNTIME_MANIFEST_JSON,
    EXECUTION_RUNTIME_MANIFEST_MD,
    EXECUTION_RUNTIME_ACCEPTANCE_JSON,
    EXECUTION_RUNTIME_ACCEPTANCE_MD,
    EXECUTION_RUNTIME_CHECKSUMS,
    EXECUTION_RUNTIME_ZIP,
)

CORE_EXECUTION_RUNTIME_LABELS: tuple[str, ...] = (
    "实时执行事件流",
    "风险证据回流",
    "runId",
    "执行状态",
    "风险信号",
    "证据快照",
    "跳转证据链",
    "项目级执行",
    "ExecutionEventRuntime",
    "useExecutionEventRuntime",
    "demo fallback",
    "real API mode",
    "Phase104 API",
    "默认脱敏",
)

EXECUTION_RUNTIME_ENDPOINTS: tuple[dict[str, str], ...] = (
    {"method": "GET", "path": "/api/v1/projects/{projectId}/test-execution/{runId}", "client": "loadLiveRun", "purpose": "读取执行 run 状态"},
    {"method": "GET", "path": "/api/v1/projects/{projectId}/test-execution/{runId}/events", "client": "loadEventStream", "purpose": "读取实时执行事件流"},
    {"method": "GET", "path": "/api/v1/projects/{projectId}/test-execution/{runId}/risks", "client": "loadRiskSignals", "purpose": "读取执行中发现的风险信号"},
    {"method": "GET", "path": "/api/v1/projects/{projectId}/test-execution/{runId}/evidence", "client": "loadEvidenceSnapshots", "purpose": "读取证据快照回流"},
    {"method": "GET", "path": "/api/v1/projects/{projectId}/risks/{riskId}/evidence", "client": "openEvidenceDetail", "purpose": "跳转风险证据详情"},
)

FORBIDDEN_EXECUTION_RUNTIME_PATTERNS: tuple[str, ...] = (
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
class FrontendExecutionRuntimeCheck:
    key: str
    passed: bool
    detail: str
    owner: str = "frontend"
    severity: str = "critical"


@dataclass
class FrontendExecutionRuntimeAcceptanceReport:
    passed: bool
    score: int
    version: str
    scenario: str
    output_dir: str
    app_dir: str
    checks: list[FrontendExecutionRuntimeCheck] = field(default_factory=list)
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
    excluded = {EXECUTION_RUNTIME_CHECKSUMS, EXECUTION_RUNTIME_ZIP}
    return [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and path.name not in excluded
        and not path.name.endswith(".pyc")
        and "node_modules" not in path.parts
    ]


def write_frontend_execution_runtime_checksums(output_dir: str | Path) -> dict[str, str]:
    root = Path(output_dir)
    checksums: dict[str, str] = {}
    for path in _iter_checksum_files(root):
        try:
            checksums[path.relative_to(root).as_posix()] = _sha256(path)
        except FileNotFoundError:
            continue
    lines = [f"{digest}  {relative}" for relative, digest in sorted(checksums.items())]
    _write_text(root / EXECUTION_RUNTIME_CHECKSUMS, "\n".join(lines) + "\n")
    return checksums


def verify_frontend_execution_runtime_checksums(output_dir: str | Path) -> list[str]:
    root = Path(output_dir)
    checksum_path = root / EXECUTION_RUNTIME_CHECKSUMS
    if not checksum_path.exists():
        return [f"missing {EXECUTION_RUNTIME_CHECKSUMS}"]
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


def _zip_execution_runtime(output_dir: Path) -> Path:
    archive_path = output_dir / EXECUTION_RUNTIME_ZIP
    if archive_path.exists():
        archive_path.unlink()
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(output_dir.rglob("*")):
            if path.is_file() and path != archive_path and not path.name.endswith(".pyc") and "node_modules" not in path.parts:
                archive.write(path, path.relative_to(output_dir).as_posix())
    return archive_path


def _write_execution_types(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/app/executionRuntimeTypes.ts",
        """export type LiveRunStatus = 'queued' | 'running' | 'completed' | 'blocked' | 'failed';\nexport type RuntimeEventLevel = 'info' | 'warning' | 'risk' | 'evidence' | 'error';\nexport type RuntimeRiskSeverity = 'P0' | 'P1' | 'P2' | 'P3';\n\nexport interface LiveExecutionEvent {\n  id: string;\n  at: string;\n  level: RuntimeEventLevel;\n  probeId: string;\n  businessFlow: string;\n  message: string;\n  evidenceId?: string;\n}\n\nexport interface RuntimeRiskSignal {\n  riskId: string;\n  title: string;\n  severity: RuntimeRiskSeverity;\n  businessFlow: string;\n  impact: string;\n  evidenceId: string;\n  reproduction: string;\n}\n\nexport interface EvidenceSnapshot {\n  evidenceId: string;\n  riskId?: string;\n  kind: 'request' | 'response' | 'dom' | 'business-state' | 'log-summary';\n  title: string;\n  summary: string;\n  confidence: number;\n}\n\nexport interface ExecutionRuntimeSnapshot {\n  projectId: string;\n  runId: string;\n  status: LiveRunStatus;\n  source: 'demo-data' | 'phase104-api' | 'demo-fallback';\n  startedAt: string;\n  completedAt?: string;\n  progress: number;\n  activeProbe: string;\n  safeExecutionMode: string;\n  events: LiveExecutionEvent[];\n  risks: RuntimeRiskSignal[];\n  evidence: EvidenceSnapshot[];\n  nextActions: string[];\n}\n\nfunction asList(value: unknown): unknown[] {\n  return Array.isArray(value) ? value : [];\n}\n\nfunction normalizeEvent(raw: Record<string, unknown>, index: number): LiveExecutionEvent {\n  return {\n    id: String(raw.id ?? raw.event_id ?? `event-${index + 1}`),\n    at: String(raw.at ?? raw.timestamp ?? new Date(Date.now() + index * 1000).toISOString()),\n    level: String(raw.level ?? 'info') as RuntimeEventLevel,\n    probeId: String(raw.probe_id ?? raw.probeId ?? 'probe-runtime'),\n    businessFlow: String(raw.business_flow ?? raw.businessFlow ?? '核心业务链路'),\n    message: String(raw.message ?? '执行事件'),\n    evidenceId: raw.evidence_id || raw.evidenceId ? String(raw.evidence_id ?? raw.evidenceId) : undefined,\n  };\n}\n\nfunction normalizeRisk(raw: Record<string, unknown>, index: number): RuntimeRiskSignal {\n  return {\n    riskId: String(raw.risk_id ?? raw.riskId ?? `risk-${index + 1}`),\n    title: String(raw.title ?? raw.name ?? 'AI 发现风险'),\n    severity: String(raw.severity ?? 'P1') as RuntimeRiskSeverity,\n    businessFlow: String(raw.business_flow ?? raw.businessFlow ?? '核心业务链路'),\n    impact: String(raw.impact ?? '可能影响上线决策'),\n    evidenceId: String(raw.evidence_id ?? raw.evidenceId ?? `evidence-${index + 1}`),\n    reproduction: String(raw.reproduction ?? raw.reproduce_steps ?? '已生成可复验证据路径'),\n  };\n}\n\nfunction normalizeEvidence(raw: Record<string, unknown>, index: number): EvidenceSnapshot {\n  const confidence = Number(raw.confidence ?? 0.86);\n  return {\n    evidenceId: String(raw.evidence_id ?? raw.evidenceId ?? `evidence-${index + 1}`),\n    riskId: raw.risk_id || raw.riskId ? String(raw.risk_id ?? raw.riskId) : undefined,\n    kind: String(raw.kind ?? 'business-state') as EvidenceSnapshot['kind'],\n    title: String(raw.title ?? '证据快照'),\n    summary: String(raw.summary ?? '请求摘要、响应摘要与业务状态快照已回流'),\n    confidence: Number.isFinite(confidence) ? confidence : 0.86,\n  };\n}\n\nexport function normalizeExecutionRuntimeSnapshot(raw: Record<string, unknown>, fallbackProjectId: string, fallbackRunId: string): ExecutionRuntimeSnapshot {\n  const events = asList(raw.events ?? raw.execution_events ?? raw.executionEvents);\n  const risks = asList(raw.risks ?? raw.risk_signals ?? raw.riskSignals);\n  const evidence = asList(raw.evidence ?? raw.evidence_snapshots ?? raw.evidenceSnapshots);\n  const actions = asList(raw.next_actions ?? raw.nextActions);\n  const progress = Number(raw.progress ?? raw.progress_percent ?? 0);\n  return {\n    projectId: String(raw.project_id ?? raw.projectId ?? fallbackProjectId),\n    runId: String(raw.run_id ?? raw.runId ?? fallbackRunId),\n    status: String(raw.status ?? 'running') as LiveRunStatus,\n    source: String(raw.source ?? 'demo-data') as ExecutionRuntimeSnapshot['source'],\n    startedAt: String(raw.started_at ?? raw.startedAt ?? new Date().toISOString()),\n    completedAt: raw.completed_at || raw.completedAt ? String(raw.completed_at ?? raw.completedAt) : undefined,\n    progress: Number.isFinite(progress) ? progress : 0,\n    activeProbe: String(raw.active_probe ?? raw.activeProbe ?? 'AI 只读执行探针'),\n    safeExecutionMode: String(raw.safe_execution_mode ?? raw.safeExecutionMode ?? 'read_only'),\n    events: events.map((item, index) => normalizeEvent(item as Record<string, unknown>, index)),\n    risks: risks.map((item, index) => normalizeRisk(item as Record<string, unknown>, index)),\n    evidence: evidence.map((item, index) => normalizeEvidence(item as Record<string, unknown>, index)),\n    nextActions: actions.map((item) => String(item)),\n  };\n}\n""",
    )


def _write_execution_service(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/services/executionEventRuntime.ts",
        """import { runtimeConfig } from '../app/runtimeConfig';\nimport { requestJson, redactApiError } from '../api/runtimeApi';\nimport { normalizeExecutionRuntimeSnapshot, type ExecutionRuntimeSnapshot } from '../app/executionRuntimeTypes';\n\nfunction demoExecutionSnapshot(projectId: string, runId: string): ExecutionRuntimeSnapshot {\n  return normalizeExecutionRuntimeSnapshot({\n    project_id: projectId,\n    run_id: runId || 'demo-readonly-run-001',\n    status: 'running',\n    source: 'demo-data',\n    progress: 68,\n    active_probe: '质检放行链路一致性',\n    safe_execution_mode: runtimeConfig.safeExecutionMode,\n    events: [\n      { id: 'evt-001', level: 'info', probe_id: 'probe-order-create', business_flow: '订单创建到质检放行', message: 'runId 已建立，开始读取业务链路状态' },\n      { id: 'evt-002', level: 'evidence', probe_id: 'probe-order-create', business_flow: '订单创建到质检放行', message: '证据快照已回流：订单创建响应摘要', evidence_id: 'evidence-order-create' },\n      { id: 'evt-003', level: 'risk', probe_id: 'probe-quality-release', business_flow: '质检放行', message: '发现 P1 风险信号：质检失败后仍出现放行状态', evidence_id: 'evidence-quality-release' },\n    ],\n    risks: [\n      { risk_id: 'risk-quality-release-p1', title: '质检失败后仍允许放行', severity: 'P1', business_flow: '质检放行', impact: '可能导致不合格批次进入出库链路', evidence_id: 'evidence-quality-release', reproduction: '执行质检失败场景后读取放行状态，出现业务状态不一致' },\n    ],\n    evidence: [\n      { evidence_id: 'evidence-order-create', kind: 'response', title: '订单创建响应摘要', summary: '响应码、业务编号、状态迁移均已脱敏记录', confidence: 0.91 },\n      { evidence_id: 'evidence-quality-release', risk_id: 'risk-quality-release-p1', kind: 'business-state', title: '质检放行状态快照', summary: '失败质检记录与放行状态同时存在，形成可复验证据链', confidence: 0.94 },\n    ],\n    next_actions: ['查看风险证据详情', '确认是否阻断上线', '修复后发起复验'],\n  }, projectId, runId || 'demo-readonly-run-001');\n}\n\nexport class ExecutionEventRuntime {\n  constructor(private readonly projectId: string, private readonly runId: string) {}\n\n  async loadLiveRun(): Promise<ExecutionRuntimeSnapshot> {\n    if (runtimeConfig.demoMode) return demoExecutionSnapshot(this.projectId, this.runId);\n    try {\n      const payload = await requestJson<Record<string, unknown>>({ method: 'GET', path: `/api/v1/projects/${this.projectId}/test-execution/${this.runId}` });\n      return normalizeExecutionRuntimeSnapshot({ ...payload, source: 'phase104-api' }, this.projectId, this.runId);\n    } catch (error) {\n      if (!runtimeConfig.fallbackToDemo) throw error;\n      const fallback = demoExecutionSnapshot(this.projectId, this.runId);\n      return { ...fallback, source: 'demo-fallback', events: [...fallback.events, { id: 'evt-fallback', at: new Date().toISOString(), level: 'warning', probeId: 'runtime-api', businessFlow: '运行态 API', message: `真实执行 API 不可用，已安全回退：${JSON.stringify(redactApiError(error))}` }] };\n    }\n  }\n\n  async pollRunStatus(): Promise<ExecutionRuntimeSnapshot> {\n    return this.loadLiveRun();\n  }\n\n  async loadEventStream(): Promise<ExecutionRuntimeSnapshot> {\n    if (runtimeConfig.demoMode) return demoExecutionSnapshot(this.projectId, this.runId);\n    try {\n      const payload = await requestJson<Record<string, unknown>>({ method: 'GET', path: `/api/v1/projects/${this.projectId}/test-execution/${this.runId}/events` });\n      return normalizeExecutionRuntimeSnapshot({ ...payload, source: 'phase104-api', run_id: this.runId }, this.projectId, this.runId);\n    } catch (error) {\n      if (!runtimeConfig.fallbackToDemo) throw error;\n      return { ...demoExecutionSnapshot(this.projectId, this.runId), source: 'demo-fallback' };\n    }\n  }\n\n  async loadRiskSignals(): Promise<ExecutionRuntimeSnapshot> {\n    if (runtimeConfig.demoMode) return demoExecutionSnapshot(this.projectId, this.runId);\n    try {\n      const payload = await requestJson<Record<string, unknown>>({ method: 'GET', path: `/api/v1/projects/${this.projectId}/test-execution/${this.runId}/risks` });\n      return normalizeExecutionRuntimeSnapshot({ ...payload, source: 'phase104-api', run_id: this.runId }, this.projectId, this.runId);\n    } catch (error) {\n      if (!runtimeConfig.fallbackToDemo) throw error;\n      return { ...demoExecutionSnapshot(this.projectId, this.runId), source: 'demo-fallback' };\n    }\n  }\n\n  async loadEvidenceSnapshots(): Promise<ExecutionRuntimeSnapshot> {\n    if (runtimeConfig.demoMode) return demoExecutionSnapshot(this.projectId, this.runId);\n    try {\n      const payload = await requestJson<Record<string, unknown>>({ method: 'GET', path: `/api/v1/projects/${this.projectId}/test-execution/${this.runId}/evidence` });\n      return normalizeExecutionRuntimeSnapshot({ ...payload, source: 'phase104-api', run_id: this.runId }, this.projectId, this.runId);\n    } catch (error) {\n      if (!runtimeConfig.fallbackToDemo) throw error;\n      return { ...demoExecutionSnapshot(this.projectId, this.runId), source: 'demo-fallback' };\n    }\n  }\n\n  openEvidenceDetail(riskId: string, evidenceId: string): string {\n    return `/risk-evidence?projectId=${encodeURIComponent(this.projectId)}&runId=${encodeURIComponent(this.runId)}&riskId=${encodeURIComponent(riskId)}&evidenceId=${encodeURIComponent(evidenceId)}`;\n  }\n}\n""",
    )


def _write_execution_hook(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/hooks/useExecutionEventRuntime.ts",
        """import { useCallback, useEffect, useMemo, useRef, useState } from 'react';\nimport { useSelectedProjectId } from './useSelectedProjectId';\nimport { ExecutionEventRuntime } from '../services/executionEventRuntime';\nimport type { ExecutionRuntimeSnapshot } from '../app/executionRuntimeTypes';\n\nexport function useExecutionEventRuntime(initialRunId = 'demo-readonly-run-001') {\n  const projectId = useSelectedProjectId();\n  const [runId, setRunId] = useState(initialRunId);\n  const [snapshot, setSnapshot] = useState<ExecutionRuntimeSnapshot | null>(null);\n  const [loading, setLoading] = useState(false);\n  const [pollingStatus, setPollingStatus] = useState<'idle' | 'polling' | 'stopped'>('idle');\n  const [error, setError] = useState<string | null>(null);\n  const timerRef = useRef<number | null>(null);\n\n  const runtime = useMemo(() => new ExecutionEventRuntime(projectId, runId), [projectId, runId]);\n\n  const stopEventPolling = useCallback(() => {\n    if (timerRef.current) window.clearInterval(timerRef.current);\n    timerRef.current = null;\n    setPollingStatus('stopped');\n  }, []);\n\n  useEffect(() => {\n    stopEventPolling();\n    setSnapshot(null);\n    setError(null);\n    setRunId(initialRunId);\n    setPollingStatus('idle');\n  }, [projectId, initialRunId, stopEventPolling]);\n\n  const loadLiveRun = useCallback(async () => {\n    setLoading(true);\n    setError(null);\n    try {\n      const next = await runtime.loadLiveRun();\n      setSnapshot(next);\n      setRunId(next.runId || runId);\n      return next;\n    } catch (caught) {\n      setError(caught instanceof Error ? caught.message : '执行状态读取失败');\n      throw caught;\n    } finally {\n      setLoading(false);\n    }\n  }, [runtime, runId]);\n\n  const loadEventStream = useCallback(async () => {\n    const next = await runtime.loadEventStream();\n    setSnapshot(next);\n    return next;\n  }, [runtime]);\n\n  const loadRiskSignals = useCallback(async () => {\n    const next = await runtime.loadRiskSignals();\n    setSnapshot(next);\n    return next;\n  }, [runtime]);\n\n  const loadEvidenceSnapshots = useCallback(async () => {\n    const next = await runtime.loadEvidenceSnapshots();\n    setSnapshot(next);\n    return next;\n  }, [runtime]);\n\n  const startEventPolling = useCallback(() => {\n    stopEventPolling();\n    setPollingStatus('polling');\n    timerRef.current = window.setInterval(() => {\n      runtime.pollRunStatus().then(setSnapshot).catch((caught) => setError(caught instanceof Error ? caught.message : '轮询失败'));\n    }, 3000);\n  }, [runtime, stopEventPolling]);\n\n  const openEvidenceDetail = useCallback((riskId: string, evidenceId: string) => runtime.openEvidenceDetail(riskId, evidenceId), [runtime]);\n\n  return {\n    projectId,\n    runId,\n    setRunId,\n    snapshot,\n    loading,\n    error,\n    pollingStatus,\n    loadLiveRun,\n    loadEventStream,\n    loadRiskSignals,\n    loadEvidenceSnapshots,\n    startEventPolling,\n    stopEventPolling,\n    openEvidenceDetail,\n  };\n}\n""",
    )


def _write_execution_components(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/components/LiveExecutionStatusPanel.tsx",
        """import type { ExecutionRuntimeSnapshot } from '../app/executionRuntimeTypes';\n\nexport function LiveExecutionStatusPanel({ snapshot }: { snapshot: ExecutionRuntimeSnapshot | null }) {\n  if (!snapshot) return <section className=\"execution-card\"><h2>执行状态</h2><p>等待读取 runId 的实时执行状态。</p></section>;\n  return (\n    <section className=\"execution-card execution-status-panel\">\n      <div>\n        <p className=\"eyebrow\">项目级执行 / runId</p>\n        <h2>实时执行事件流</h2>\n        <p>当前 runId：<strong>{snapshot.runId}</strong>，执行状态：<strong>{snapshot.status}</strong></p>\n      </div>\n      <div className=\"progress-shell\"><span style={{ width: `${snapshot.progress}%` }} /></div>\n      <dl className=\"execution-kpis\">\n        <div><dt>执行状态</dt><dd>{snapshot.status}</dd></div>\n        <div><dt>风险信号</dt><dd>{snapshot.risks.length}</dd></div>\n        <div><dt>证据快照</dt><dd>{snapshot.evidence.length}</dd></div>\n        <div><dt>安全模式</dt><dd>{snapshot.safeExecutionMode}</dd></div>\n      </dl>\n    </section>\n  );\n}\n""",
    )
    _write_text(
        app_dir / "src/components/ExecutionEventStream.tsx",
        """import type { LiveExecutionEvent } from '../app/executionRuntimeTypes';\n\nexport function ExecutionEventStream({ events }: { events: LiveExecutionEvent[] }) {\n  return (\n    <section className=\"execution-card\">\n      <div className=\"section-heading\"><p className=\"eyebrow\">Live Events</p><h2>执行事件流</h2></div>\n      <ol className=\"event-stream\">\n        {events.map((event) => (\n          <li key={event.id} className={`event-level-${event.level}`}>\n            <span>{event.level}</span>\n            <strong>{event.businessFlow}</strong>\n            <p>{event.message}</p>\n            <small>{event.probeId} · {event.evidenceId || '等待证据'}</small>\n          </li>\n        ))}\n      </ol>\n    </section>\n  );\n}\n""",
    )
    _write_text(
        app_dir / "src/components/RuntimeRiskSignalList.tsx",
        """import type { RuntimeRiskSignal } from '../app/executionRuntimeTypes';\n\nexport function RuntimeRiskSignalList({ risks, onOpenEvidence }: { risks: RuntimeRiskSignal[]; onOpenEvidence: (riskId: string, evidenceId: string) => string }) {\n  return (\n    <section className=\"execution-card\">\n      <div className=\"section-heading\"><p className=\"eyebrow\">Risk Feedback</p><h2>风险证据回流</h2></div>\n      <div className=\"risk-signal-list\">\n        {risks.map((risk) => (\n          <article key={risk.riskId}>\n            <span className=\"severity-pill\">{risk.severity}</span>\n            <h3>{risk.title}</h3>\n            <p>{risk.impact}</p>\n            <small>{risk.businessFlow} · {risk.reproduction}</small>\n            <a href={onOpenEvidence(risk.riskId, risk.evidenceId)}>跳转证据链</a>\n          </article>\n        ))}\n      </div>\n    </section>\n  );\n}\n""",
    )
    _write_text(
        app_dir / "src/components/EvidenceSnapshotPanel.tsx",
        """import type { EvidenceSnapshot } from '../app/executionRuntimeTypes';\n\nexport function EvidenceSnapshotPanel({ evidence }: { evidence: EvidenceSnapshot[] }) {\n  return (\n    <section className=\"execution-card\">\n      <div className=\"section-heading\"><p className=\"eyebrow\">Evidence</p><h2>证据快照</h2></div>\n      <div className=\"evidence-grid\">\n        {evidence.map((item) => (\n          <article key={item.evidenceId}>\n            <strong>{item.title}</strong>\n            <p>{item.summary}</p>\n            <small>{item.kind} · 可信度 {Math.round(item.confidence * 100)}%</small>\n          </article>\n        ))}\n      </div>\n    </section>\n  );\n}\n""",
    )


def _write_execution_page(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/pages/ExecutionRuntimePage.tsx",
        """import { PageShell } from '../components/PageShell';\nimport { LiveExecutionStatusPanel } from '../components/LiveExecutionStatusPanel';\nimport { ExecutionEventStream } from '../components/ExecutionEventStream';\nimport { RuntimeRiskSignalList } from '../components/RuntimeRiskSignalList';\nimport { EvidenceSnapshotPanel } from '../components/EvidenceSnapshotPanel';\nimport { useExecutionEventRuntime } from '../hooks/useExecutionEventRuntime';\n\nexport function ExecutionRuntimePage() {\n  const runtime = useExecutionEventRuntime();\n  const snapshot = runtime.snapshot;\n\n  return (\n    <PageShell\n      title=\"实时执行事件流 / 风险证据回流\"\n      description=\"围绕项目级 runId 读取执行状态、风险信号、证据快照，并跳转风险证据详情。\"\n      actions={(\n        <>\n          <button onClick={() => runtime.loadLiveRun()} disabled={runtime.loading}>读取执行状态</button>\n          <button onClick={() => runtime.loadEventStream()}>刷新事件流</button>\n          <button onClick={() => runtime.loadRiskSignals()}>读取风险信号</button>\n          <button onClick={() => runtime.loadEvidenceSnapshots()}>读取证据快照</button>\n          <button onClick={() => runtime.startEventPolling()}>启动轮询</button>\n          <button onClick={() => runtime.stopEventPolling()}>停止轮询</button>\n        </>\n      )}\n    >\n      <section className=\"runtime-hero\">\n        <p className=\"eyebrow\">Phase104 API · real API mode · demo fallback · 默认脱敏</p>\n        <h1>项目级执行运行态</h1>\n        <p>runId：<input value={runtime.runId} onChange={(event) => runtime.setRunId(event.target.value)} aria-label=\"runId\" /> · 轮询状态：{runtime.pollingStatus}</p>\n        {runtime.error ? <p className=\"runtime-error\">{runtime.error}</p> : null}\n      </section>\n      <LiveExecutionStatusPanel snapshot={snapshot} />\n      <ExecutionEventStream events={snapshot?.events ?? []} />\n      <RuntimeRiskSignalList risks={snapshot?.risks ?? []} onOpenEvidence={runtime.openEvidenceDetail} />\n      <EvidenceSnapshotPanel evidence={snapshot?.evidence ?? []} />\n      <section className=\"execution-card\">\n        <h2>下一步动作</h2>\n        <ul>{(snapshot?.nextActions ?? ['启动事件流读取', '查看风险证据回流', '确认是否进入修复闭环']).map((action) => <li key={action}>{action}</li>)}</ul>\n      </section>\n    </PageShell>\n  );\n}\n""",
    )


def _write_contract_test(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/__tests__/execution-runtime-contract.test.ts",
        """import { normalizeExecutionRuntimeSnapshot } from '../app/executionRuntimeTypes';\nimport { ExecutionEventRuntime } from '../services/executionEventRuntime';\n\ndescribe('Phase106G execution runtime contract', () => {\n  it('normalizes live execution events, risk signals, and evidence snapshots', () => {\n    const snapshot = normalizeExecutionRuntimeSnapshot({\n      project_id: 'project-1',\n      run_id: 'run-1',\n      status: 'running',\n      events: [{ level: 'risk', message: '风险事件', evidence_id: 'e-1' }],\n      risks: [{ risk_id: 'r-1', title: '状态不一致', evidence_id: 'e-1' }],\n      evidence: [{ evidence_id: 'e-1', title: '证据快照', confidence: 0.93 }],\n    }, 'fallback-project', 'fallback-run');\n    expect(snapshot.runId).toBe('run-1');\n    expect(snapshot.events[0].level).toBe('risk');\n    expect(snapshot.risks[0].riskId).toBe('r-1');\n    expect(snapshot.evidence[0].evidenceId).toBe('e-1');\n  });\n\n  it('exposes read-only runtime client methods', () => {\n    const runtime = new ExecutionEventRuntime('project-1', 'run-1');\n    expect(runtime.loadLiveRun).toBeDefined();\n    expect(runtime.pollRunStatus).toBeDefined();\n    expect(runtime.loadEventStream).toBeDefined();\n    expect(runtime.loadRiskSignals).toBeDefined();\n    expect(runtime.loadEvidenceSnapshots).toBeDefined();\n    expect(runtime.openEvidenceDetail('risk-1', 'evidence-1')).toContain('/risk-evidence');\n  });\n});\n""",
    )


def _write_css(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/styles/execution-runtime.css",
        """.runtime-hero, .execution-card { background: rgba(255,255,255,0.92); border: 1px solid rgba(15,23,42,0.08); border-radius: 18px; padding: 20px; margin-bottom: 18px; box-shadow: 0 18px 45px rgba(15,23,42,0.08); }\n.runtime-hero input { border: 1px solid rgba(15,23,42,0.18); border-radius: 10px; padding: 6px 10px; min-width: 240px; }\n.runtime-error { color: #b42318; font-weight: 700; }\n.execution-status-panel { display: grid; gap: 18px; }\n.progress-shell { height: 12px; background: rgba(15,23,42,0.08); border-radius: 999px; overflow: hidden; }\n.progress-shell span { display: block; height: 100%; background: linear-gradient(90deg, #2563eb, #16a34a); }\n.execution-kpis { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }\n.execution-kpis div, .risk-signal-list article, .evidence-grid article { border: 1px solid rgba(15,23,42,0.08); border-radius: 14px; padding: 14px; background: rgba(248,250,252,0.9); }\n.section-heading { display: flex; justify-content: space-between; gap: 16px; align-items: center; }\n.event-stream { list-style: none; padding: 0; display: grid; gap: 10px; }\n.event-stream li { border-left: 4px solid #2563eb; padding: 12px 14px; background: rgba(248,250,252,0.9); border-radius: 12px; }\n.event-level-risk { border-left-color: #dc2626 !important; }\n.event-level-evidence { border-left-color: #16a34a !important; }\n.risk-signal-list, .evidence-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 14px; }\n.severity-pill { display: inline-flex; padding: 4px 8px; border-radius: 999px; background: #fee2e2; color: #991b1b; font-weight: 800; }\n.eyebrow { text-transform: uppercase; letter-spacing: .08em; color: #475569; font-size: 12px; font-weight: 800; }\n""",
    )


def _patch_routes_and_app(app_dir: Path) -> None:
    routes_path = app_dir / "src/routes.ts"
    routes = _read_text(routes_path)
    if "ExecutionRuntimePage" not in routes:
        target = "  { path: '/test-plan-runtime', key: 'test_plan_runtime', label: 'AI 测试计划真实生成', component: 'TestPlanRuntimePage' },"
        replacement = target + "\n  { path: '/execution-runtime', key: 'execution_runtime', label: '实时执行事件流', component: 'ExecutionRuntimePage' },"
        if target in routes:
            routes = routes.replace(target, replacement)
        else:
            routes = routes.replace("];", "  { path: '/execution-runtime', key: 'execution_runtime', label: '实时执行事件流', component: 'ExecutionRuntimePage' },\n];")
    _write_text(routes_path, routes)

    app_path = app_dir / "src/App.tsx"
    app = _read_text(app_path)
    if "ExecutionRuntimePage" not in app:
        app = app.replace("import { TestPlanRuntimePage } from './pages/TestPlanRuntimePage';", "import { TestPlanRuntimePage } from './pages/TestPlanRuntimePage';\nimport { ExecutionRuntimePage } from './pages/ExecutionRuntimePage';")
        app = app.replace("import './styles/test-plan-runtime.css';", "import './styles/test-plan-runtime.css';\nimport './styles/execution-runtime.css';")
        app = app.replace("    case '/test-plan-runtime': return <TestPlanRuntimePage />;", "    case '/test-plan-runtime': return <TestPlanRuntimePage />;\n    case '/execution-runtime': return <ExecutionRuntimePage />;")
    _write_text(app_path, app)


def _write_readme(app_dir: Path) -> None:
    _write_text(
        app_dir / "README_FRONTEND_EXECUTION_RUNTIME.md",
        f"""# Phase106G Frontend Execution Runtime\n\nVersion: `{PHASE106G_VERSION}`\n\nThis generated frontend app adds `/execution-runtime` as the project/run-scoped realtime execution page.\n\n## Capabilities\n\n- 实时执行事件流\n- 风险证据回流\n- runId 执行状态读取\n- 风险信号与证据快照归一化\n- 跳转证据链 URL handoff\n- real API mode / demo fallback\n- Phase104 API 路径合同\n- 默认脱敏：不展示原始 token、cookie、session、password 或 client secret\n\n## Local run\n\n```powershell\nnpm install\nnpm run dev\n```\n\nOpen `/execution-runtime`.\n""",
    )


def _write_report_files(root: Path, report: FrontendExecutionRuntimeAcceptanceReport) -> None:
    _write_text(root / EXECUTION_RUNTIME_ACCEPTANCE_JSON, _json_dump(report.to_dict()))
    lines = [
        "# Phase106G Frontend Execution Runtime Acceptance Report",
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
    _write_text(root / EXECUTION_RUNTIME_ACCEPTANCE_MD, "\n".join(lines) + "\n")


def _write_manifest_files(root: Path, report: FrontendExecutionRuntimeAcceptanceReport) -> dict[str, Any]:
    manifest = {
        "version": PHASE106G_VERSION,
        "generated_at": _now(),
        "scenario": report.scenario,
        "app_dir": FRONTEND_APP_DIR,
        "route": "/execution-runtime",
        "core_labels": list(CORE_EXECUTION_RUNTIME_LABELS),
        "runtime_endpoints": list(EXECUTION_RUNTIME_ENDPOINTS),
        "acceptance": {"passed": report.passed, "score": report.score},
        "artifacts": report.artifacts,
    }
    _write_text(root / EXECUTION_RUNTIME_MANIFEST_JSON, _json_dump(manifest))
    lines = [
        "# Phase106G Frontend Execution Runtime Manifest",
        "",
        f"- version: `{PHASE106G_VERSION}`",
        "- route: `/execution-runtime`",
        f"- score: `{report.score}`",
        "",
        "## Runtime Endpoints",
    ]
    for endpoint in EXECUTION_RUNTIME_ENDPOINTS:
        lines.append(f"- `{endpoint['method']}` `{endpoint['path']}` → `{endpoint['client']}` / {endpoint['purpose']}")
    _write_text(root / EXECUTION_RUNTIME_MANIFEST_MD, "\n".join(lines) + "\n")
    return manifest


def scan_frontend_execution_runtime_for_secret_leaks(output_dir: str | Path) -> list[str]:
    root = Path(output_dir)
    leaks: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() in {".zip", ".png", ".jpg", ".jpeg", ".gif"} or "node_modules" in path.parts:
            continue
        text = _read_text(path)
        for pattern in FORBIDDEN_EXECUTION_RUNTIME_PATTERNS:
            if pattern in text:
                leaks.append(f"{path.relative_to(root).as_posix()} contains {pattern}")
    return leaks


def build_frontend_execution_runtime(
    output_dir: str | Path,
    *,
    scenario: str = "manufacturing",
    clean: bool = True,
) -> FrontendExecutionRuntimeAcceptanceReport:
    root = Path(output_dir)
    if clean and root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)

    build_frontend_test_plan_runtime(root, scenario=scenario, clean=False)
    app_dir = root / FRONTEND_APP_DIR
    _write_execution_types(app_dir)
    _write_execution_service(app_dir)
    _write_execution_hook(app_dir)
    _write_execution_components(app_dir)
    _write_execution_page(app_dir)
    _write_contract_test(app_dir)
    _write_css(app_dir)
    _patch_routes_and_app(app_dir)
    _write_readme(app_dir)

    report = validate_frontend_execution_runtime(root, scenario=scenario, write_report=True, skip_checksum=True)
    _write_manifest_files(root, report)
    write_frontend_execution_runtime_checksums(root)
    _zip_execution_runtime(root)
    report = validate_frontend_execution_runtime(root, scenario=scenario, write_report=True)
    _write_manifest_files(root, report)
    write_frontend_execution_runtime_checksums(root)
    _zip_execution_runtime(root)
    return report


def validate_frontend_execution_runtime(
    output_dir: str | Path,
    *,
    scenario: str = "manufacturing",
    write_report: bool = True,
    skip_checksum: bool = False,
) -> FrontendExecutionRuntimeAcceptanceReport:
    root = Path(output_dir)
    app_dir = root / FRONTEND_APP_DIR
    checks: list[FrontendExecutionRuntimeCheck] = []

    missing = [relative for relative in REQUIRED_EXECUTION_RUNTIME_FILES if not (root / relative).exists()]
    if skip_checksum:
        missing = [relative for relative in missing if relative != EXECUTION_RUNTIME_CHECKSUMS]
    if not skip_checksum and EXECUTION_RUNTIME_ZIP in missing:
        missing.remove(EXECUTION_RUNTIME_ZIP)
    checks.append(FrontendExecutionRuntimeCheck("required_files", not missing, "执行运行态必需文件完整" if not missing else f"缺失文件: {missing}"))

    routes_text = _read_text(app_dir / "src/routes.ts")
    routes_ok = "'/execution-runtime'" in routes_text and "ExecutionRuntimePage" in routes_text
    checks.append(FrontendExecutionRuntimeCheck("execution_runtime_route", routes_ok, "已注册 /execution-runtime 路由" if routes_ok else "执行运行态路由未注册"))

    app_text = _read_text(app_dir / "src/App.tsx")
    app_ok = "ExecutionRuntimePage" in app_text and "execution-runtime.css" in app_text and "case '/execution-runtime'" in app_text
    checks.append(FrontendExecutionRuntimeCheck("app_resolution", app_ok, "App 已接入执行运行态页面" if app_ok else "App 未完整接入执行运行态页面"))

    service_text = _read_text(app_dir / "src/services/executionEventRuntime.ts")
    service_ok = all(keyword in service_text for keyword in ("ExecutionEventRuntime", "loadLiveRun", "pollRunStatus", "loadEventStream", "loadRiskSignals", "loadEvidenceSnapshots", "openEvidenceDetail", "demo-fallback", "requestJson"))
    checks.append(FrontendExecutionRuntimeCheck("runtime_service", service_ok, "执行 runtime 支持状态、事件、风险、证据和 demo fallback" if service_ok else "执行 runtime 服务不完整"))

    hook_text = _read_text(app_dir / "src/hooks/useExecutionEventRuntime.ts")
    hook_ok = all(keyword in hook_text for keyword in ("useExecutionEventRuntime", "loadLiveRun", "loadEventStream", "loadRiskSignals", "loadEvidenceSnapshots", "startEventPolling", "stopEventPolling", "openEvidenceDetail"))
    checks.append(FrontendExecutionRuntimeCheck("runtime_hook", hook_ok, "Hook 已支持事件流、风险、证据、轮询和证据跳转" if hook_ok else "执行 runtime Hook 不完整"))

    components_text = "\n".join(_read_text(app_dir / relative) for relative in (
        "src/components/LiveExecutionStatusPanel.tsx",
        "src/components/ExecutionEventStream.tsx",
        "src/components/RuntimeRiskSignalList.tsx",
        "src/components/EvidenceSnapshotPanel.tsx",
        "src/pages/ExecutionRuntimePage.tsx",
    ))
    missing_labels = [label for label in ("实时执行事件流", "风险证据回流", "runId", "执行状态", "风险信号", "证据快照", "跳转证据链", "项目级执行", "Phase104 API") if label not in components_text]
    checks.append(FrontendExecutionRuntimeCheck("business_semantics", not missing_labels, "页面覆盖实时执行与证据回流关键语义" if not missing_labels else f"缺失文案: {missing_labels}"))

    types_text = _read_text(app_dir / "src/app/executionRuntimeTypes.ts")
    types_ok = all(keyword in types_text for keyword in ("ExecutionRuntimeSnapshot", "LiveExecutionEvent", "RuntimeRiskSignal", "EvidenceSnapshot", "normalizeExecutionRuntimeSnapshot", "risks", "evidence"))
    checks.append(FrontendExecutionRuntimeCheck("runtime_types", types_ok, "执行运行态类型模型完整" if types_ok else "执行运行态类型模型不完整"))

    contract_test = _read_text(app_dir / "src/__tests__/execution-runtime-contract.test.ts")
    contract_ok = all(keyword in contract_test for keyword in ("normalizeExecutionRuntimeSnapshot", "ExecutionEventRuntime", "loadLiveRun", "pollRunStatus", "loadEventStream", "loadRiskSignals", "loadEvidenceSnapshots", "openEvidenceDetail"))
    checks.append(FrontendExecutionRuntimeCheck("contract_test", contract_ok, "已生成执行运行态合同测试" if contract_ok else "合同测试覆盖不足"))

    manifest = _read_json(root / EXECUTION_RUNTIME_MANIFEST_JSON)
    manifest_ok = manifest.get("version") == PHASE106G_VERSION and manifest.get("route") == "/execution-runtime" and len(manifest.get("runtime_endpoints") or []) >= 5
    checks.append(FrontendExecutionRuntimeCheck("manifest", manifest_ok, "manifest 描述执行运行态路由与 API 合同" if manifest_ok else "manifest 内容不完整"))

    if skip_checksum:
        checksum_ok = True
        checksum_detail = "构建中跳过 checksum 复验"
    else:
        checksum_failures = verify_frontend_execution_runtime_checksums(root)
        checksum_ok = not checksum_failures
        checksum_detail = "checksum 复验通过" if checksum_ok else f"checksum 失败: {checksum_failures}"
    checks.append(FrontendExecutionRuntimeCheck("checksums", checksum_ok, checksum_detail))

    leaks = scan_frontend_execution_runtime_for_secret_leaks(root)
    checks.append(FrontendExecutionRuntimeCheck("secret_leak_scan", not leaks, "未发现高风险敏感信息泄露模式" if not leaks else f"发现泄露风险: {leaks}"))

    passed = all(check.passed for check in checks)
    score = round(sum(1 for check in checks if check.passed) / len(checks) * 100) if checks else 0
    report = FrontendExecutionRuntimeAcceptanceReport(
        passed=passed,
        score=score,
        version=PHASE106G_VERSION,
        scenario=scenario,
        output_dir=str(root),
        app_dir=str(app_dir),
        checks=checks,
        artifacts={
            "app_dir": FRONTEND_APP_DIR,
            "route": "/execution-runtime",
            "manifest_json": EXECUTION_RUNTIME_MANIFEST_JSON,
            "acceptance_json": EXECUTION_RUNTIME_ACCEPTANCE_JSON,
            "checksums": EXECUTION_RUNTIME_CHECKSUMS,
            "zip": EXECUTION_RUNTIME_ZIP,
        },
    )
    if write_report:
        _write_report_files(root, report)
    return report


def run_frontend_execution_runtime_export(
    output_dir: str | Path,
    *,
    scenario: str = "manufacturing",
    validate_only: bool = False,
) -> FrontendExecutionRuntimeAcceptanceReport:
    if validate_only:
        return validate_frontend_execution_runtime(output_dir, scenario=scenario, write_report=True)
    return build_frontend_execution_runtime(output_dir, scenario=scenario)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate Phase106G frontend execution runtime app")
    parser.add_argument("--scenario", default="manufacturing")
    parser.add_argument("--output-dir", default="outputs/phase106_frontend_execution_runtime")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)

    report = run_frontend_execution_runtime_export(args.output_dir, scenario=args.scenario, validate_only=args.validate_only)
    print(_json_dump(report.to_dict()))
    return 0 if report.passed else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
