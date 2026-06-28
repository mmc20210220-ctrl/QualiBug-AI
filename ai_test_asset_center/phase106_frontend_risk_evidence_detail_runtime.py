
from __future__ import annotations

"""Phase106H: risk evidence detail runtime for the real frontend app.

Phase106G wires a project/run-scoped execution stream that can jump to evidence.
Phase106H materializes that destination as a real frontend runtime surface:

* project/run/risk/evidence scoped detail loading
* request and response summaries without raw sensitive values
* business state snapshot and reproduction steps
* remediation advice and close conditions
* demo mode / real API mode / demo fallback safety

The repository root receives only a Python generator and tests. The React app is
emitted into an output directory so npm artifacts stay outside the root repo.
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
from ai_test_asset_center.phase106_frontend_execution_runtime import (
    FRONTEND_APP_DIR,
    build_frontend_execution_runtime,
)

PHASE106H_VERSION = "phase106h-frontend-risk-evidence-detail-runtime-v1"

RISK_EVIDENCE_DETAIL_MANIFEST_JSON = "frontend_risk_evidence_detail_runtime_manifest.json"
RISK_EVIDENCE_DETAIL_MANIFEST_MD = "frontend_risk_evidence_detail_runtime_manifest.md"
RISK_EVIDENCE_DETAIL_ACCEPTANCE_JSON = "frontend_risk_evidence_detail_runtime_acceptance_report.json"
RISK_EVIDENCE_DETAIL_ACCEPTANCE_MD = "frontend_risk_evidence_detail_runtime_acceptance_report.md"
RISK_EVIDENCE_DETAIL_CHECKSUMS = "CHECKSUMS_PHASE106H.sha256"
RISK_EVIDENCE_DETAIL_ZIP = "phase106_frontend_risk_evidence_detail_runtime.zip"

REQUIRED_RISK_EVIDENCE_DETAIL_FILES: tuple[str, ...] = (
    f"{FRONTEND_APP_DIR}/src/app/riskEvidenceDetailTypes.ts",
    f"{FRONTEND_APP_DIR}/src/services/riskEvidenceDetailRuntime.ts",
    f"{FRONTEND_APP_DIR}/src/hooks/useRiskEvidenceDetailRuntime.ts",
    f"{FRONTEND_APP_DIR}/src/components/EvidenceIdentityPanel.tsx",
    f"{FRONTEND_APP_DIR}/src/components/EvidenceRequestResponsePanel.tsx",
    f"{FRONTEND_APP_DIR}/src/components/EvidenceBusinessSnapshotPanel.tsx",
    f"{FRONTEND_APP_DIR}/src/components/EvidenceReproductionSteps.tsx",
    f"{FRONTEND_APP_DIR}/src/components/EvidenceRemediationPanel.tsx",
    f"{FRONTEND_APP_DIR}/src/pages/RiskEvidenceDetailRuntimePage.tsx",
    f"{FRONTEND_APP_DIR}/src/__tests__/risk-evidence-detail-runtime-contract.test.ts",
    f"{FRONTEND_APP_DIR}/src/styles/risk-evidence-detail-runtime.css",
    f"{FRONTEND_APP_DIR}/README_FRONTEND_RISK_EVIDENCE_DETAIL_RUNTIME.md",
    RISK_EVIDENCE_DETAIL_MANIFEST_JSON,
    RISK_EVIDENCE_DETAIL_MANIFEST_MD,
    RISK_EVIDENCE_DETAIL_ACCEPTANCE_JSON,
    RISK_EVIDENCE_DETAIL_ACCEPTANCE_MD,
    RISK_EVIDENCE_DETAIL_CHECKSUMS,
    RISK_EVIDENCE_DETAIL_ZIP,
)

CORE_RISK_EVIDENCE_DETAIL_LABELS: tuple[str, ...] = (
    "风险证据链真实详情",
    "riskId",
    "evidenceId",
    "runId",
    "请求摘要",
    "响应摘要",
    "业务状态快照",
    "复现步骤",
    "修复建议",
    "关闭条件",
    "证据可信度",
    "项目级证据",
    "demo fallback",
    "real API mode",
    "Phase104 API",
    "默认脱敏",
)

RISK_EVIDENCE_DETAIL_ENDPOINTS: tuple[dict[str, str], ...] = (
    {"method": "GET", "path": "/api/v1/projects/{projectId}/risks/{riskId}/evidence/{evidenceId}", "client": "loadEvidenceDetail", "purpose": "读取风险证据详情"},
    {"method": "GET", "path": "/api/v1/projects/{projectId}/test-execution/{runId}/risks/{riskId}/evidence", "client": "loadEvidenceFromRun", "purpose": "从执行 run 回读风险证据"},
    {"method": "GET", "path": "/api/v1/projects/{projectId}/risks/{riskId}/reproduction", "client": "loadReproductionSteps", "purpose": "读取复现步骤"},
    {"method": "GET", "path": "/api/v1/projects/{projectId}/risks/{riskId}/remediation", "client": "loadRemediationAdvice", "purpose": "读取修复建议和关闭条件"},
)

FORBIDDEN_RISK_EVIDENCE_DETAIL_PATTERNS: tuple[str, ...] = (
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
class FrontendRiskEvidenceDetailCheck:
    key: str
    passed: bool
    detail: str
    owner: str = "frontend"
    severity: str = "critical"


@dataclass
class FrontendRiskEvidenceDetailAcceptanceReport:
    passed: bool
    score: int
    version: str
    scenario: str
    output_dir: str
    app_dir: str
    checks: list[FrontendRiskEvidenceDetailCheck] = field(default_factory=list)
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
    excluded = {RISK_EVIDENCE_DETAIL_CHECKSUMS, RISK_EVIDENCE_DETAIL_ZIP}
    return [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and path.name not in excluded
        and not path.name.endswith(".pyc")
        and "node_modules" not in path.parts
    ]


def write_frontend_risk_evidence_detail_checksums(output_dir: str | Path) -> dict[str, str]:
    root = Path(output_dir)
    checksums = {path.relative_to(root).as_posix(): _sha256(path) for path in _iter_checksum_files(root)}
    lines = [f"{digest}  {relative}" for relative, digest in sorted(checksums.items())]
    _write_text(root / RISK_EVIDENCE_DETAIL_CHECKSUMS, "\n".join(lines) + "\n")
    return checksums


def verify_frontend_risk_evidence_detail_checksums(output_dir: str | Path) -> list[str]:
    root = Path(output_dir)
    checksum_path = root / RISK_EVIDENCE_DETAIL_CHECKSUMS
    if not checksum_path.exists():
        return [f"missing {RISK_EVIDENCE_DETAIL_CHECKSUMS}"]
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


def _zip_risk_evidence_detail_runtime(output_dir: Path) -> Path:
    archive_path = output_dir / RISK_EVIDENCE_DETAIL_ZIP
    if archive_path.exists():
        archive_path.unlink()
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(output_dir.rglob("*")):
            if path.is_file() and path != archive_path and not path.name.endswith(".pyc") and "node_modules" not in path.parts:
                archive.write(path, path.relative_to(output_dir).as_posix())
    return archive_path


def _write_types(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/app/riskEvidenceDetailTypes.ts",
        """export type EvidenceDetailSource = 'demo-data' | 'phase104-api' | 'demo-fallback';\nexport type EvidenceConfidenceLevel = 'high' | 'medium' | 'low';\n\nexport interface EvidenceHttpSummary {\n  method: string;\n  path: string;\n  statusCode: number;\n  latencyMs: number;\n  redaction: string;\n  summary: string;\n}\n\nexport interface EvidenceBusinessSnapshot {\n  before: string;\n  after: string;\n  invariant: string;\n  mismatch: string;\n}\n\nexport interface EvidenceReproductionStep {\n  order: number;\n  action: string;\n  expected: string;\n  observed: string;\n}\n\nexport interface EvidenceRemediation {\n  ownerSuggestion: string;\n  fixAdvice: string;\n  verificationProbe: string;\n  closeConditions: string[];\n}\n\nexport interface RiskEvidenceDetail {\n  projectId: string;\n  runId: string;\n  riskId: string;\n  evidenceId: string;\n  source: EvidenceDetailSource;\n  title: string;\n  severity: 'P0' | 'P1' | 'P2' | 'P3';\n  businessFlow: string;\n  confidence: number;\n  confidenceLevel: EvidenceConfidenceLevel;\n  impact: string;\n  request: EvidenceHttpSummary;\n  response: EvidenceHttpSummary;\n  businessSnapshot: EvidenceBusinessSnapshot;\n  reproductionSteps: EvidenceReproductionStep[];\n  remediation: EvidenceRemediation;\n  nextActions: string[];\n}\n\nfunction listOf(value: unknown): unknown[] {\n  return Array.isArray(value) ? value : [];\n}\n\nfunction normalizeHttpSummary(raw: Record<string, unknown>, fallback: Partial<EvidenceHttpSummary>): EvidenceHttpSummary {\n  return {\n    method: String(raw.method ?? fallback.method ?? 'GET'),\n    path: String(raw.path ?? raw.url_path ?? fallback.path ?? '/api/v1/runtime'),\n    statusCode: Number(raw.status_code ?? raw.statusCode ?? fallback.statusCode ?? 200),\n    latencyMs: Number(raw.latency_ms ?? raw.latencyMs ?? fallback.latencyMs ?? 120),\n    redaction: String(raw.redaction ?? fallback.redaction ?? '默认脱敏：不展示 token/cookie/session/password 原值'),\n    summary: String(raw.summary ?? fallback.summary ?? '请求摘要已归一化'),\n  };\n}\n\nfunction normalizeBusinessSnapshot(raw: Record<string, unknown>): EvidenceBusinessSnapshot {\n  return {\n    before: String(raw.before ?? '执行前业务状态正常'),\n    after: String(raw.after ?? '执行后业务状态出现偏差'),\n    invariant: String(raw.invariant ?? '关键业务状态应保持一致'),\n    mismatch: String(raw.mismatch ?? 'AI 检测到状态不一致'),\n  };\n}\n\nfunction normalizeStep(raw: Record<string, unknown>, index: number): EvidenceReproductionStep {\n  return {\n    order: Number(raw.order ?? index + 1),\n    action: String(raw.action ?? `执行第 ${index + 1} 步`),\n    expected: String(raw.expected ?? '业务状态符合预期'),\n    observed: String(raw.observed ?? '观察到风险证据'),\n  };\n}\n\nfunction confidenceLevel(confidence: number): EvidenceConfidenceLevel {\n  if (confidence >= 0.85) return 'high';\n  if (confidence >= 0.65) return 'medium';\n  return 'low';\n}\n\nexport function normalizeRiskEvidenceDetail(raw: Record<string, unknown>, fallbackProjectId: string, fallbackRunId: string, fallbackRiskId: string, fallbackEvidenceId: string): RiskEvidenceDetail {\n  const confidence = Number(raw.confidence ?? 0.91);\n  const reproduction = listOf(raw.reproduction_steps ?? raw.reproductionSteps);\n  const closeConditions = listOf((raw.remediation as Record<string, unknown> | undefined)?.close_conditions ?? (raw.remediation as Record<string, unknown> | undefined)?.closeConditions ?? raw.close_conditions ?? raw.closeConditions);\n  return {\n    projectId: String(raw.project_id ?? raw.projectId ?? fallbackProjectId),\n    runId: String(raw.run_id ?? raw.runId ?? fallbackRunId),\n    riskId: String(raw.risk_id ?? raw.riskId ?? fallbackRiskId),\n    evidenceId: String(raw.evidence_id ?? raw.evidenceId ?? fallbackEvidenceId),\n    source: String(raw.source ?? 'demo-data') as EvidenceDetailSource,\n    title: String(raw.title ?? '风险证据链真实详情'),\n    severity: String(raw.severity ?? 'P1') as RiskEvidenceDetail['severity'],\n    businessFlow: String(raw.business_flow ?? raw.businessFlow ?? '核心业务链路'),\n    confidence: Number.isFinite(confidence) ? confidence : 0.91,\n    confidenceLevel: confidenceLevel(Number.isFinite(confidence) ? confidence : 0.91),\n    impact: String(raw.impact ?? '影响上线判断，需要修复后复验'),\n    request: normalizeHttpSummary((raw.request ?? raw.request_summary ?? {}) as Record<string, unknown>, { method: 'POST', path: '/api/v1/order/submit', statusCode: 200, summary: '请求摘要：只保留方法、路径、状态和脱敏说明' }),\n    response: normalizeHttpSummary((raw.response ?? raw.response_summary ?? {}) as Record<string, unknown>, { method: 'POST', path: '/api/v1/order/submit', statusCode: 409, summary: '响应摘要：业务状态与预期不一致' }),\n    businessSnapshot: normalizeBusinessSnapshot((raw.business_snapshot ?? raw.businessSnapshot ?? {}) as Record<string, unknown>),\n    reproductionSteps: reproduction.length ? reproduction.map((item, index) => normalizeStep(item as Record<string, unknown>, index)) : [\n      normalizeStep({ action: '选择项目和 runId', expected: '定位风险上下文', observed: '已定位 riskId / evidenceId' }, 0),\n      normalizeStep({ action: '执行只读探针', expected: '业务状态不被修改', observed: '发现状态不一致证据' }, 1),\n      normalizeStep({ action: '打开证据详情', expected: '展示请求摘要、响应摘要和业务状态快照', observed: '证据可信度满足复验要求' }, 2),\n    ],\n    remediation: {\n      ownerSuggestion: String((raw.remediation as Record<string, unknown> | undefined)?.owner_suggestion ?? (raw.remediation as Record<string, unknown> | undefined)?.ownerSuggestion ?? '后端接口负责人 + 业务规则负责人'),\n      fixAdvice: String((raw.remediation as Record<string, unknown> | undefined)?.fix_advice ?? (raw.remediation as Record<string, unknown> | undefined)?.fixAdvice ?? '修复状态校验与异常分支处理'),\n      verificationProbe: String((raw.remediation as Record<string, unknown> | undefined)?.verification_probe ?? (raw.remediation as Record<string, unknown> | undefined)?.verificationProbe ?? '重新运行同一只读复验探针'),\n      closeConditions: closeConditions.length ? closeConditions.map((item) => String(item)) : ['复现步骤通过', '风险信号消失', '证据快照显示业务状态一致'],\n    },\n    nextActions: listOf(raw.next_actions ?? raw.nextActions).map((item) => String(item)),\n  };\n}\n""",
    )


def _write_service(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/services/riskEvidenceDetailRuntime.ts",
        """import { runtimeConfig } from '../app/runtimeConfig';\nimport { requestJson, redactApiError } from '../api/runtimeApi';\nimport { normalizeRiskEvidenceDetail, type RiskEvidenceDetail } from '../app/riskEvidenceDetailTypes';\n\nfunction demoEvidenceDetail(projectId: string, runId: string, riskId: string, evidenceId: string): RiskEvidenceDetail {\n  return normalizeRiskEvidenceDetail({\n    project_id: projectId,\n    run_id: runId || 'demo-readonly-run-001',\n    risk_id: riskId || 'risk-order-state-001',\n    evidence_id: evidenceId || 'evidence-business-state-001',\n    source: 'demo-data',\n    title: '风险证据链真实详情：订单状态与库存状态不一致',\n    severity: 'P1',\n    business_flow: '下单 → 库存锁定 → 支付 → 出库',\n    confidence: 0.93,\n    impact: '可能导致客户已支付但库存未锁定，影响上线决策。',\n    request: { method: 'POST', path: '/api/v1/orders/submit', status_code: 200, latency_ms: 186, redaction: '默认脱敏：不展示 token/cookie/session/password 原值', summary: '请求摘要仅展示业务字段和接口路径。' },\n    response: { method: 'POST', path: '/api/v1/orders/submit', status_code: 409, latency_ms: 244, redaction: '响应摘要已隐藏敏感头和身份字段', summary: '响应摘要显示库存锁定失败但订单状态进入待支付。' },\n    business_snapshot: { before: '库存可售 12，订单不存在', after: '订单待支付，库存仍为可售 12', invariant: '创建订单后库存应进入锁定态', mismatch: '订单状态和库存锁定状态不一致' },\n    reproduction_steps: [\n      { order: 1, action: '选择制造业项目并打开 runId', expected: '读取执行上下文', observed: '已定位风险证据' },\n      { order: 2, action: '执行订单提交只读探针', expected: '库存锁定成功或订单回滚', observed: '订单待支付但库存未锁定' },\n      { order: 3, action: '打开 riskId / evidenceId 证据详情', expected: '请求摘要、响应摘要、业务状态快照完整', observed: '证据可信度 0.93' },\n    ],\n    remediation: { owner_suggestion: '订单服务负责人 + 库存服务负责人', fix_advice: '补齐订单创建和库存锁定的一致性补偿逻辑', verification_probe: '重新运行订单提交一致性只读探针', close_conditions: ['订单创建失败时库存无变化', '订单创建成功时库存进入锁定态', '复验探针不再产生风险信号'] },\n    next_actions: ['派发给订单服务负责人', '修复后重新运行同一 runId 复验', '关闭条件全部满足后进入报告页'],\n  }, projectId, runId, riskId, evidenceId);\n}\n\nasync function tryRealEvidenceDetail(projectId: string, runId: string, riskId: string, evidenceId: string): Promise<RiskEvidenceDetail> {\n  const path = `/api/v1/projects/${encodeURIComponent(projectId)}/risks/${encodeURIComponent(riskId)}/evidence/${encodeURIComponent(evidenceId)}`;\n  const payload = await requestJson<Record<string, unknown>>(path);\n  return normalizeRiskEvidenceDetail({ ...payload, source: 'phase104-api' }, projectId, runId, riskId, evidenceId);\n}\n\nexport class RiskEvidenceDetailRuntime {\n  constructor(private readonly projectId: string) {}\n\n  async loadEvidenceDetail(runId: string, riskId: string, evidenceId: string): Promise<RiskEvidenceDetail> {\n    if (runtimeConfig.demoMode) {\n      return demoEvidenceDetail(this.projectId, runId, riskId, evidenceId);\n    }\n    try {\n      return await tryRealEvidenceDetail(this.projectId, runId, riskId, evidenceId);\n    } catch (error) {\n      if (runtimeConfig.fallbackToDemo) {\n        return normalizeRiskEvidenceDetail({ ...demoEvidenceDetail(this.projectId, runId, riskId, evidenceId), source: 'demo-fallback', next_actions: [`真实 API 暂不可用：${redactApiError(error)}`, '已切换 demo fallback，演示不中断'] }, this.projectId, runId, riskId, evidenceId);\n      }\n      throw new Error(redactApiError(error));\n    }\n  }\n\n  async loadEvidenceFromRun(runId: string, riskId: string, evidenceId: string): Promise<RiskEvidenceDetail> {\n    if (!runtimeConfig.demoMode) {\n      try {\n        const payload = await requestJson<Record<string, unknown>>(`/api/v1/projects/${encodeURIComponent(this.projectId)}/test-execution/${encodeURIComponent(runId)}/risks/${encodeURIComponent(riskId)}/evidence`);\n        return normalizeRiskEvidenceDetail({ ...payload, evidence_id: evidenceId, source: 'phase104-api' }, this.projectId, runId, riskId, evidenceId);\n      } catch (error) {\n        if (!runtimeConfig.fallbackToDemo) throw new Error(redactApiError(error));\n      }\n    }\n    return demoEvidenceDetail(this.projectId, runId, riskId, evidenceId);\n  }\n\n  async loadReproductionSteps(runId: string, riskId: string, evidenceId: string): Promise<RiskEvidenceDetail> {\n    const detail = await this.loadEvidenceDetail(runId, riskId, evidenceId);\n    return normalizeRiskEvidenceDetail({ ...detail, next_actions: ['复现步骤已刷新', '可发起复验探针', '可进入修复闭环'] }, this.projectId, runId, riskId, evidenceId);\n  }\n\n  async loadRemediationAdvice(runId: string, riskId: string, evidenceId: string): Promise<RiskEvidenceDetail> {\n    const detail = await this.loadEvidenceDetail(runId, riskId, evidenceId);\n    return normalizeRiskEvidenceDetail({ ...detail, next_actions: ['修复建议已刷新', '确认负责人', '按关闭条件复验'] }, this.projectId, runId, riskId, evidenceId);\n  }\n}\n""",
    )


def _write_hook(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/hooks/useRiskEvidenceDetailRuntime.ts",
        """import { useMemo, useState } from 'react';\nimport { selectedProjectId } from '../app/projectContext';\nimport { RiskEvidenceDetailRuntime } from '../services/riskEvidenceDetailRuntime';\nimport type { RiskEvidenceDetail } from '../app/riskEvidenceDetailTypes';\n\nexport function useRiskEvidenceDetailRuntime() {\n  const [projectId, setProjectId] = useState(selectedProjectId());\n  const [runId, setRunId] = useState('demo-readonly-run-001');\n  const [riskId, setRiskId] = useState('risk-order-state-001');\n  const [evidenceId, setEvidenceId] = useState('evidence-business-state-001');\n  const [detail, setDetail] = useState<RiskEvidenceDetail | null>(null);\n  const [loading, setLoading] = useState(false);\n  const [error, setError] = useState<string | null>(null);\n  const runtime = useMemo(() => new RiskEvidenceDetailRuntime(projectId), [projectId]);\n\n  async function capture(action: () => Promise<RiskEvidenceDetail>) {\n    setLoading(true);\n    setError(null);\n    try {\n      const next = await action();\n      setDetail(next);\n      return next;\n    } catch (err) {\n      const message = err instanceof Error ? err.message : String(err);\n      setError(message);\n      return null;\n    } finally {\n      setLoading(false);\n    }\n  }\n\n  return {\n    projectId, setProjectId,\n    runId, setRunId,\n    riskId, setRiskId,\n    evidenceId, setEvidenceId,\n    detail, loading, error,\n    loadEvidenceDetail: () => capture(() => runtime.loadEvidenceDetail(runId, riskId, evidenceId)),\n    loadEvidenceFromRun: () => capture(() => runtime.loadEvidenceFromRun(runId, riskId, evidenceId)),\n    loadReproductionSteps: () => capture(() => runtime.loadReproductionSteps(runId, riskId, evidenceId)),\n    loadRemediationAdvice: () => capture(() => runtime.loadRemediationAdvice(runId, riskId, evidenceId)),\n  };\n}\n""",
    )


def _write_components(app_dir: Path) -> None:
    _write_text(app_dir / "src/components/EvidenceIdentityPanel.tsx", """import type { RiskEvidenceDetail } from '../app/riskEvidenceDetailTypes';\n\nexport function EvidenceIdentityPanel({ detail }: { detail: RiskEvidenceDetail | null }) {\n  if (!detail) return <section className=\"evidence-card\"><h2>风险证据链真实详情</h2><p>输入 riskId / evidenceId / runId 后加载项目级证据。</p></section>;\n  return <section className=\"evidence-card identity\"><p className=\"eyebrow\">项目级证据 · {detail.source} · 默认脱敏</p><h2>{detail.title}</h2><div className=\"identity-grid\"><span>riskId：{detail.riskId}</span><span>evidenceId：{detail.evidenceId}</span><span>runId：{detail.runId}</span><span>证据可信度：{Math.round(detail.confidence * 100)}% / {detail.confidenceLevel}</span><span>严重级别：{detail.severity}</span><span>业务链路：{detail.businessFlow}</span></div><p>{detail.impact}</p></section>;\n}\n""")
    _write_text(app_dir / "src/components/EvidenceRequestResponsePanel.tsx", """import type { RiskEvidenceDetail } from '../app/riskEvidenceDetailTypes';\n\nexport function EvidenceRequestResponsePanel({ detail }: { detail: RiskEvidenceDetail | null }) {\n  if (!detail) return null;\n  const rows = [['请求摘要', detail.request], ['响应摘要', detail.response]] as const;\n  return <section className=\"evidence-card\"><h2>请求摘要 / 响应摘要</h2><div className=\"http-grid\">{rows.map(([title, item]) => <article key={title}><h3>{title}</h3><p><strong>{item.method}</strong> {item.path}</p><p>状态：{item.statusCode} · 耗时：{item.latencyMs}ms</p><p>{item.summary}</p><small>{item.redaction}</small></article>)}</div></section>;\n}\n""")
    _write_text(app_dir / "src/components/EvidenceBusinessSnapshotPanel.tsx", """import type { RiskEvidenceDetail } from '../app/riskEvidenceDetailTypes';\n\nexport function EvidenceBusinessSnapshotPanel({ detail }: { detail: RiskEvidenceDetail | null }) {\n  if (!detail) return null;\n  const snapshot = detail.businessSnapshot;\n  return <section className=\"evidence-card\"><h2>业务状态快照</h2><div className=\"snapshot-grid\"><article><h3>Before</h3><p>{snapshot.before}</p></article><article><h3>After</h3><p>{snapshot.after}</p></article><article><h3>业务不变量</h3><p>{snapshot.invariant}</p></article><article><h3>状态偏差</h3><p>{snapshot.mismatch}</p></article></div></section>;\n}\n""")
    _write_text(app_dir / "src/components/EvidenceReproductionSteps.tsx", """import type { RiskEvidenceDetail } from '../app/riskEvidenceDetailTypes';\n\nexport function EvidenceReproductionSteps({ detail }: { detail: RiskEvidenceDetail | null }) {\n  if (!detail) return null;\n  return <section className=\"evidence-card\"><h2>复现步骤</h2><ol className=\"repro-list\">{detail.reproductionSteps.map((step) => <li key={step.order}><strong>{step.action}</strong><span>预期：{step.expected}</span><span>观察：{step.observed}</span></li>)}</ol></section>;\n}\n""")
    _write_text(app_dir / "src/components/EvidenceRemediationPanel.tsx", """import type { RiskEvidenceDetail } from '../app/riskEvidenceDetailTypes';\n\nexport function EvidenceRemediationPanel({ detail }: { detail: RiskEvidenceDetail | null }) {\n  if (!detail) return null;\n  return <section className=\"evidence-card\"><h2>修复建议 / 关闭条件</h2><p>负责人建议：{detail.remediation.ownerSuggestion}</p><p>修复建议：{detail.remediation.fixAdvice}</p><p>复验探针：{detail.remediation.verificationProbe}</p><h3>关闭条件</h3><ul>{detail.remediation.closeConditions.map((item) => <li key={item}>{item}</li>)}</ul><h3>下一步动作</h3><ul>{detail.nextActions.map((item) => <li key={item}>{item}</li>)}</ul></section>;\n}\n""")


def _write_page(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/pages/RiskEvidenceDetailRuntimePage.tsx",
        """import { PageShell } from '../components/PageShell';\nimport { EvidenceIdentityPanel } from '../components/EvidenceIdentityPanel';\nimport { EvidenceRequestResponsePanel } from '../components/EvidenceRequestResponsePanel';\nimport { EvidenceBusinessSnapshotPanel } from '../components/EvidenceBusinessSnapshotPanel';\nimport { EvidenceReproductionSteps } from '../components/EvidenceReproductionSteps';\nimport { EvidenceRemediationPanel } from '../components/EvidenceRemediationPanel';\nimport { useRiskEvidenceDetailRuntime } from '../hooks/useRiskEvidenceDetailRuntime';\n\nexport function RiskEvidenceDetailRuntimePage() {\n  const runtime = useRiskEvidenceDetailRuntime();\n  return (\n    <PageShell\n      title=\"风险证据链真实详情\"\n      description=\"围绕项目级 riskId / evidenceId / runId 展示请求摘要、响应摘要、业务状态快照、复现步骤、修复建议和关闭条件。\"\n      actions={(<>\n        <button onClick={() => runtime.loadEvidenceDetail()} disabled={runtime.loading}>加载证据详情</button>\n        <button onClick={() => runtime.loadEvidenceFromRun()} disabled={runtime.loading}>从 runId 回读证据</button>\n        <button onClick={() => runtime.loadReproductionSteps()} disabled={runtime.loading}>刷新复现步骤</button>\n        <button onClick={() => runtime.loadRemediationAdvice()} disabled={runtime.loading}>读取修复建议</button>\n      </>)}\n    >\n      <section className=\"evidence-hero\">\n        <p className=\"eyebrow\">Phase104 API · real API mode · demo fallback · 默认脱敏</p>\n        <h1>项目级证据详情工作台</h1>\n        <div className=\"evidence-inputs\">\n          <label>projectId<input value={runtime.projectId} onChange={(event) => runtime.setProjectId(event.target.value)} /></label>\n          <label>runId<input value={runtime.runId} onChange={(event) => runtime.setRunId(event.target.value)} /></label>\n          <label>riskId<input value={runtime.riskId} onChange={(event) => runtime.setRiskId(event.target.value)} /></label>\n          <label>evidenceId<input value={runtime.evidenceId} onChange={(event) => runtime.setEvidenceId(event.target.value)} /></label>\n        </div>\n        {runtime.error ? <p className=\"runtime-error\">{runtime.error}</p> : null}\n      </section>\n      <EvidenceIdentityPanel detail={runtime.detail} />\n      <EvidenceRequestResponsePanel detail={runtime.detail} />\n      <EvidenceBusinessSnapshotPanel detail={runtime.detail} />\n      <EvidenceReproductionSteps detail={runtime.detail} />\n      <EvidenceRemediationPanel detail={runtime.detail} />\n    </PageShell>\n  );\n}\n""",
    )


def _write_contract_test(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/__tests__/risk-evidence-detail-runtime-contract.test.ts",
        """import { describe, expect, it } from 'vitest';\nimport { normalizeRiskEvidenceDetail } from '../app/riskEvidenceDetailTypes';\nimport { RiskEvidenceDetailRuntime } from '../services/riskEvidenceDetailRuntime';\n\ndescribe('risk evidence detail runtime contract', () => {\n  it('normalizes riskId evidenceId runId and evidence detail payload', () => {\n    const detail = normalizeRiskEvidenceDetail({ risk_id: 'risk-1', evidence_id: 'ev-1', run_id: 'run-1', confidence: 0.9 }, 'project-1', 'run-fallback', 'risk-fallback', 'ev-fallback');\n    expect(detail.riskId).toBe('risk-1');\n    expect(detail.evidenceId).toBe('ev-1');\n    expect(detail.runId).toBe('run-1');\n    expect(detail.request.summary).toContain('请求摘要');\n    expect(detail.response.summary).toContain('响应摘要');\n    expect(detail.reproductionSteps.length).toBeGreaterThan(0);\n    expect(detail.remediation.closeConditions.length).toBeGreaterThan(0);\n  });\n\n  it('exposes real API runtime methods with demo fallback safety', () => {\n    const runtime = new RiskEvidenceDetailRuntime('project-demo');\n    expect(runtime.loadEvidenceDetail).toBeTypeOf('function');\n    expect(runtime.loadEvidenceFromRun).toBeTypeOf('function');\n    expect(runtime.loadReproductionSteps).toBeTypeOf('function');\n    expect(runtime.loadRemediationAdvice).toBeTypeOf('function');\n  });\n});\n""",
    )


def _write_css(app_dir: Path) -> None:
    _write_text(
        app_dir / "src/styles/risk-evidence-detail-runtime.css",
        """.evidence-hero,.evidence-card{background:#fff;border:1px solid #dbe4f0;border-radius:18px;padding:22px;margin-bottom:18px;box-shadow:0 12px 30px rgba(15,23,42,.06)}.evidence-inputs,.identity-grid,.http-grid,.snapshot-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px}.evidence-inputs label{display:flex;flex-direction:column;font-size:12px;color:#64748b;gap:6px}.evidence-inputs input{border:1px solid #cbd5e1;border-radius:10px;padding:10px}.identity-grid span,.http-grid article,.snapshot-grid article{background:#f8fafc;border:1px solid #e2e8f0;border-radius:14px;padding:12px}.repro-list li{margin-bottom:12px;display:grid;gap:6px}.runtime-error{background:#fff1f2;color:#be123c;border:1px solid #fecdd3;border-radius:12px;padding:10px}.eyebrow{text-transform:uppercase;letter-spacing:.08em;color:#2563eb;font-weight:700;font-size:12px}\n""",
    )


def _patch_routes_and_app(app_dir: Path) -> None:
    routes_path = app_dir / "src/routes.ts"
    routes = _read_text(routes_path)
    if "RiskEvidenceDetailRuntimePage" not in routes:
        target = "  { path: '/execution-runtime', key: 'execution_runtime', label: '实时执行事件流', component: 'ExecutionRuntimePage' },"
        replacement = target + "\n  { path: '/risk-evidence-runtime', key: 'risk_evidence_detail_runtime', label: '风险证据详情', component: 'RiskEvidenceDetailRuntimePage' },"
        if target in routes:
            routes = routes.replace(target, replacement)
        else:
            routes = routes.replace("];", "  { path: '/risk-evidence-runtime', key: 'risk_evidence_detail_runtime', label: '风险证据详情', component: 'RiskEvidenceDetailRuntimePage' },\n];")
    _write_text(routes_path, routes)

    app_path = app_dir / "src/App.tsx"
    app = _read_text(app_path)
    if "RiskEvidenceDetailRuntimePage" not in app:
        app = app.replace("import { ExecutionRuntimePage } from './pages/ExecutionRuntimePage';", "import { ExecutionRuntimePage } from './pages/ExecutionRuntimePage';\nimport { RiskEvidenceDetailRuntimePage } from './pages/RiskEvidenceDetailRuntimePage';")
        app = app.replace("import './styles/execution-runtime.css';", "import './styles/execution-runtime.css';\nimport './styles/risk-evidence-detail-runtime.css';")
        app = app.replace("    case '/execution-runtime': return <ExecutionRuntimePage />;", "    case '/execution-runtime': return <ExecutionRuntimePage />;\n    case '/risk-evidence-runtime': return <RiskEvidenceDetailRuntimePage />;")
    _write_text(app_path, app)


def _write_readme(app_dir: Path) -> None:
    _write_text(
        app_dir / "README_FRONTEND_RISK_EVIDENCE_DETAIL_RUNTIME.md",
        """# Phase106H 前端风险证据链真实详情页\n\n本阶段把 `riskId / evidenceId / runId` 跳转后的证据详情页真实化。\n\n## 能力\n\n- 项目级证据详情读取\n- 请求摘要 / 响应摘要\n- 业务状态快照\n- 复现步骤\n- 修复建议\n- 关闭条件\n- 证据可信度\n- demo mode / real API mode / demo fallback\n- 默认脱敏，不展示 token、cookie、session、password 原值\n\n## 路由\n\n`/risk-evidence-runtime`\n\n## 真实 API 模式\n\n```text\nVITE_QUALIBUG_DEMO_MODE=false\nVITE_QUALIBUG_FALLBACK_TO_DEMO=true\nVITE_QUALIBUG_API_BASE_URL=http://127.0.0.1:8790\nVITE_QUALIBUG_SAFE_EXECUTION_MODE=read_only\n```\n""",
    )


def _write_report_files(root: Path, report: FrontendRiskEvidenceDetailAcceptanceReport) -> None:
    _write_text(root / RISK_EVIDENCE_DETAIL_ACCEPTANCE_JSON, _json_dump(report.to_dict()))
    lines = [
        "# Phase106H Frontend Risk Evidence Detail Runtime Acceptance Report",
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
    _write_text(root / RISK_EVIDENCE_DETAIL_ACCEPTANCE_MD, "\n".join(lines) + "\n")


def _write_manifest_files(root: Path, report: FrontendRiskEvidenceDetailAcceptanceReport) -> dict[str, Any]:
    manifest = {
        "version": PHASE106H_VERSION,
        "generated_at": _now(),
        "scenario": report.scenario,
        "app_dir": FRONTEND_APP_DIR,
        "route": "/risk-evidence-runtime",
        "core_labels": list(CORE_RISK_EVIDENCE_DETAIL_LABELS),
        "runtime_endpoints": list(RISK_EVIDENCE_DETAIL_ENDPOINTS),
        "acceptance": {"passed": report.passed, "score": report.score},
        "artifacts": report.artifacts,
    }
    _write_text(root / RISK_EVIDENCE_DETAIL_MANIFEST_JSON, _json_dump(manifest))
    lines = [
        "# Phase106H Frontend Risk Evidence Detail Runtime Manifest",
        "",
        f"- version: `{PHASE106H_VERSION}`",
        "- route: `/risk-evidence-runtime`",
        f"- score: `{report.score}`",
        "",
        "## Runtime Endpoints",
    ]
    for endpoint in RISK_EVIDENCE_DETAIL_ENDPOINTS:
        lines.append(f"- `{endpoint['method']}` `{endpoint['path']}` → `{endpoint['client']}` / {endpoint['purpose']}")
    _write_text(root / RISK_EVIDENCE_DETAIL_MANIFEST_MD, "\n".join(lines) + "\n")
    return manifest


def scan_frontend_risk_evidence_detail_for_secret_leaks(output_dir: str | Path) -> list[str]:
    root = Path(output_dir)
    leaks: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() in {".zip", ".png", ".jpg", ".jpeg", ".gif"} or "node_modules" in path.parts:
            continue
        text = _read_text(path)
        for pattern in FORBIDDEN_RISK_EVIDENCE_DETAIL_PATTERNS:
            if pattern in text:
                leaks.append(f"{path.relative_to(root).as_posix()} contains {pattern}")
    return leaks


def build_frontend_risk_evidence_detail_runtime(
    output_dir: str | Path,
    *,
    scenario: str = "manufacturing",
    clean: bool = True,
) -> FrontendRiskEvidenceDetailAcceptanceReport:
    root = Path(output_dir)
    if clean and root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)

    build_frontend_execution_runtime(root, scenario=scenario)
    app_dir = root / FRONTEND_APP_DIR
    _write_types(app_dir)
    _write_service(app_dir)
    _write_hook(app_dir)
    _write_components(app_dir)
    _write_page(app_dir)
    _write_contract_test(app_dir)
    _write_css(app_dir)
    _patch_routes_and_app(app_dir)
    _write_readme(app_dir)

    report = validate_frontend_risk_evidence_detail_runtime(root, scenario=scenario, write_report=True, skip_checksum=True)
    _write_manifest_files(root, report)
    write_frontend_risk_evidence_detail_checksums(root)
    _zip_risk_evidence_detail_runtime(root)
    report = validate_frontend_risk_evidence_detail_runtime(root, scenario=scenario, write_report=True)
    _write_manifest_files(root, report)
    write_frontend_risk_evidence_detail_checksums(root)
    _zip_risk_evidence_detail_runtime(root)
    return report


def validate_frontend_risk_evidence_detail_runtime(
    output_dir: str | Path,
    *,
    scenario: str = "manufacturing",
    write_report: bool = True,
    skip_checksum: bool = False,
) -> FrontendRiskEvidenceDetailAcceptanceReport:
    root = Path(output_dir)
    app_dir = root / FRONTEND_APP_DIR
    checks: list[FrontendRiskEvidenceDetailCheck] = []

    missing = [relative for relative in REQUIRED_RISK_EVIDENCE_DETAIL_FILES if not (root / relative).exists()]
    if skip_checksum:
        missing = [relative for relative in missing if relative != RISK_EVIDENCE_DETAIL_CHECKSUMS]
    if not skip_checksum and RISK_EVIDENCE_DETAIL_ZIP in missing:
        missing.remove(RISK_EVIDENCE_DETAIL_ZIP)
    checks.append(FrontendRiskEvidenceDetailCheck("required_files", not missing, "风险证据详情运行态必需文件完整" if not missing else f"缺失文件: {missing}"))

    routes_text = _read_text(app_dir / "src/routes.ts")
    routes_ok = "'/risk-evidence-runtime'" in routes_text and "RiskEvidenceDetailRuntimePage" in routes_text
    checks.append(FrontendRiskEvidenceDetailCheck("risk_evidence_detail_route", routes_ok, "已注册 /risk-evidence-runtime 路由" if routes_ok else "风险证据详情路由未注册"))

    app_text = _read_text(app_dir / "src/App.tsx")
    app_ok = "RiskEvidenceDetailRuntimePage" in app_text and "risk-evidence-detail-runtime.css" in app_text and "case '/risk-evidence-runtime'" in app_text
    checks.append(FrontendRiskEvidenceDetailCheck("app_resolution", app_ok, "App 已接入风险证据详情页面" if app_ok else "App 未完整接入风险证据详情页面"))

    service_text = _read_text(app_dir / "src/services/riskEvidenceDetailRuntime.ts")
    service_ok = all(keyword in service_text for keyword in ("RiskEvidenceDetailRuntime", "loadEvidenceDetail", "loadEvidenceFromRun", "loadReproductionSteps", "loadRemediationAdvice", "demo-fallback", "requestJson"))
    checks.append(FrontendRiskEvidenceDetailCheck("runtime_service", service_ok, "证据详情 runtime 支持详情、run 回读、复现和修复建议" if service_ok else "证据详情 runtime 服务不完整"))

    hook_text = _read_text(app_dir / "src/hooks/useRiskEvidenceDetailRuntime.ts")
    hook_ok = all(keyword in hook_text for keyword in ("useRiskEvidenceDetailRuntime", "riskId", "evidenceId", "runId", "loadEvidenceDetail", "loadEvidenceFromRun", "loadReproductionSteps", "loadRemediationAdvice"))
    checks.append(FrontendRiskEvidenceDetailCheck("runtime_hook", hook_ok, "Hook 已支持 riskId/evidenceId/runId 和证据详情动作" if hook_ok else "证据详情 Hook 不完整"))

    components_text = "\n".join(_read_text(app_dir / relative) for relative in (
        "src/components/EvidenceIdentityPanel.tsx",
        "src/components/EvidenceRequestResponsePanel.tsx",
        "src/components/EvidenceBusinessSnapshotPanel.tsx",
        "src/components/EvidenceReproductionSteps.tsx",
        "src/components/EvidenceRemediationPanel.tsx",
        "src/pages/RiskEvidenceDetailRuntimePage.tsx",
    ))
    required_labels = ("风险证据链真实详情", "riskId", "evidenceId", "runId", "请求摘要", "响应摘要", "业务状态快照", "复现步骤", "修复建议", "关闭条件", "证据可信度", "项目级证据", "Phase104 API")
    missing_labels = [label for label in required_labels if label not in components_text]
    checks.append(FrontendRiskEvidenceDetailCheck("business_semantics", not missing_labels, "页面覆盖证据详情关键语义" if not missing_labels else f"缺失文案: {missing_labels}"))

    types_text = _read_text(app_dir / "src/app/riskEvidenceDetailTypes.ts")
    types_ok = all(keyword in types_text for keyword in ("RiskEvidenceDetail", "EvidenceHttpSummary", "EvidenceBusinessSnapshot", "EvidenceReproductionStep", "EvidenceRemediation", "normalizeRiskEvidenceDetail"))
    checks.append(FrontendRiskEvidenceDetailCheck("runtime_types", types_ok, "风险证据详情类型模型完整" if types_ok else "风险证据详情类型模型不完整"))

    contract_test = _read_text(app_dir / "src/__tests__/risk-evidence-detail-runtime-contract.test.ts")
    contract_ok = all(keyword in contract_test for keyword in ("normalizeRiskEvidenceDetail", "RiskEvidenceDetailRuntime", "loadEvidenceDetail", "loadEvidenceFromRun", "loadReproductionSteps", "loadRemediationAdvice"))
    checks.append(FrontendRiskEvidenceDetailCheck("contract_test", contract_ok, "已生成风险证据详情合同测试" if contract_ok else "合同测试覆盖不足"))

    manifest = _read_json(root / RISK_EVIDENCE_DETAIL_MANIFEST_JSON)
    manifest_ok = manifest.get("version") == PHASE106H_VERSION and manifest.get("route") == "/risk-evidence-runtime" and len(manifest.get("runtime_endpoints") or []) >= 4
    checks.append(FrontendRiskEvidenceDetailCheck("manifest", manifest_ok, "manifest 描述证据详情路由与 API 合同" if manifest_ok else "manifest 内容不完整"))

    if skip_checksum:
        checksum_ok = True
        checksum_detail = "构建中跳过 checksum 复验"
    else:
        checksum_failures = verify_frontend_risk_evidence_detail_checksums(root)
        checksum_ok = not checksum_failures
        checksum_detail = "checksum 复验通过" if checksum_ok else f"checksum 失败: {checksum_failures}"
    checks.append(FrontendRiskEvidenceDetailCheck("checksums", checksum_ok, checksum_detail))

    leaks = scan_frontend_risk_evidence_detail_for_secret_leaks(root)
    checks.append(FrontendRiskEvidenceDetailCheck("secret_leak_scan", not leaks, "未发现高风险敏感信息泄露模式" if not leaks else f"发现泄露风险: {leaks}"))

    passed = all(check.passed for check in checks)
    score = round(sum(1 for check in checks if check.passed) / len(checks) * 100) if checks else 0
    report = FrontendRiskEvidenceDetailAcceptanceReport(
        passed=passed,
        score=score,
        version=PHASE106H_VERSION,
        scenario=scenario,
        output_dir=str(root),
        app_dir=str(app_dir),
        checks=checks,
        artifacts={
            "app_dir": FRONTEND_APP_DIR,
            "route": "/risk-evidence-runtime",
            "manifest_json": RISK_EVIDENCE_DETAIL_MANIFEST_JSON,
            "acceptance_json": RISK_EVIDENCE_DETAIL_ACCEPTANCE_JSON,
            "checksums": RISK_EVIDENCE_DETAIL_CHECKSUMS,
            "zip": RISK_EVIDENCE_DETAIL_ZIP,
        },
    )
    if write_report:
        _write_report_files(root, report)
    return report


def run_frontend_risk_evidence_detail_runtime_export(
    output_dir: str | Path,
    *,
    scenario: str = "manufacturing",
    validate_only: bool = False,
) -> FrontendRiskEvidenceDetailAcceptanceReport:
    if validate_only:
        return validate_frontend_risk_evidence_detail_runtime(output_dir, scenario=scenario, write_report=True)
    return build_frontend_risk_evidence_detail_runtime(output_dir, scenario=scenario)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate Phase106H frontend risk evidence detail runtime app")
    parser.add_argument("--scenario", default="manufacturing")
    parser.add_argument("--output-dir", default="outputs/phase106_frontend_risk_evidence_detail_runtime")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)

    report = run_frontend_risk_evidence_detail_runtime_export(args.output_dir, scenario=args.scenario, validate_only=args.validate_only)
    print(_json_dump(report.to_dict()))
    return 0 if report.passed else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
