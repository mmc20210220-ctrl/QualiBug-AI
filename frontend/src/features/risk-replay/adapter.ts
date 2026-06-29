import { redactUnknown } from "@/lib/redact";
import type { RiskReplayField, RiskReplayJumpTarget, RiskReplayStep, RiskReplayStepKind, RiskReplayStepStatus, RiskReplayTimeline } from "./model";

function pickRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

function pickArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function pickString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function pickNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return null;
}

function pickBoolean(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

function safeText(value: unknown, fallback = "—"): string {
  if (typeof value === "number") return String(value);
  const raw = pickString(value);
  if (!raw) return fallback;
  const redacted = redactUnknown(raw);
  return typeof redacted === "string" && redacted.trim() ? redacted : fallback;
}

function safeList(value: unknown): string[] {
  return pickArray(value)
    .map((item) => safeText(item, ""))
    .filter(Boolean);
}

function normalizeStepStatus(value: unknown): RiskReplayStepStatus {
  const text = safeText(value, "").toLowerCase();
  if (/(block|阻断|403|401|forbid|deny)/i.test(text)) return "blocked";
  if (/(fail|error|异常|失败|500)/i.test(text)) return "failed";
  if (/(warn|finding|risk|风险|发现)/i.test(text)) return "warning";
  if (/(run|进行中|执行中)/i.test(text)) return "running";
  if (/(done|finish|complete|success|passed|ok|通过)/i.test(text)) return "completed";
  return "pending";
}

function normalizeStepKind(value: unknown): RiskReplayStepKind {
  const text = safeText(value, "").toLowerCase();
  if (/(discover|probe|path|发现|链路)/i.test(text)) return "discovery";
  if (/(request|请求)/i.test(text)) return "request";
  if (/(response|响应)/i.test(text)) return "response";
  if (/(snapshot|快照)/i.test(text)) return "snapshot";
  if (/(finding|risk|风险)/i.test(text)) return "finding";
  if (/(repro|replay|复现|回放)/i.test(text)) return "reproduction";
  if (/(fix|remedi|repair|修复)/i.test(text)) return "remediation";
  return "entry";
}

function buildFields(value: unknown): RiskReplayField[] {
  return pickArray(value)
    .map((item) => {
      const record = pickRecord(item) ?? {};
      const label = safeText(record.label, "");
      const fieldValue = safeText(record.value, "");
      if (!label || !fieldValue) return null;
      return { label, value: fieldValue };
    })
    .filter((item): item is RiskReplayField => Boolean(item));
}

function isFailureStatus(status: RiskReplayStepStatus): boolean {
  return status === "warning" || status === "failed" || status === "blocked";
}

function statusFromResponseCode(value: unknown): RiskReplayStepStatus {
  const code = pickNumber(value);
  if (code === null) return "completed";
  if (code >= 500) return "failed";
  if (code >= 400) return "blocked";
  if (code >= 300) return "warning";
  return "completed";
}

function rankStatus(status: RiskReplayStepStatus): number {
  if (status === "blocked") return 5;
  if (status === "failed") return 4;
  if (status === "warning") return 3;
  if (status === "running") return 2;
  if (status === "completed") return 1;
  return 0;
}

function mergeTimelineStatus(steps: readonly RiskReplayStep[]): RiskReplayStepStatus {
  if (!steps.length) return "pending";
  return [...steps].sort((left, right) => rankStatus(right.status) - rankStatus(left.status))[0]?.status ?? "pending";
}

function buildCue(title: string, summary: string): string | undefined {
  const cue = [title, summary].filter(Boolean).join(" · ");
  return cue ? cue : undefined;
}

function makeStep(input: {
  stepId: string;
  index: number;
  kind: RiskReplayStepKind;
  title: string;
  summary: string;
  status: RiskReplayStepStatus;
  timestamp?: string;
  cue?: string;
  failurePoint?: boolean;
  fields?: readonly RiskReplayField[];
}): RiskReplayStep {
  return {
    stepId: input.stepId,
    index: input.index,
    kind: input.kind,
    title: input.title,
    summary: input.summary,
    status: input.status,
    timestamp: input.timestamp,
    cue: input.cue,
    failurePoint: input.failurePoint ?? isFailureStatus(input.status),
    fields: input.fields ?? [],
  };
}

function eventMatchesRisk(event: Record<string, unknown>, detail: Record<string, unknown>, riskId: string): boolean {
  const finding = pickRecord(event.finding) ?? {};
  if (pickString(finding.risk_id) === riskId || pickString(event.risk_id) === riskId) return true;

  const risk = pickRecord(detail.risk) ?? detail;
  const flow = pickRecord(risk.affected_business_flow) ?? {};
  const title = safeText(risk.title, "");
  const flowName = safeText(flow.name, "");
  const titleHint = title.length >= 4 ? title.slice(0, 4) : title;
  const flowHint = flowName.length >= 4 ? flowName.slice(0, 4) : flowName;
  const text = `${safeText(event.title, "")} ${safeText(event.message, "")} ${safeText(event.phase_label, "")}`;

  return [title, titleHint, flowName, flowHint].filter(Boolean).some((token) => text.includes(token));
}

function stepsFromExplicitTimeline(detail: Record<string, unknown>): RiskReplayStep[] {
  const evidence = pickRecord(detail.evidence_bundle) ?? {};
  const raw = pickArray(evidence.replay_timeline);
  return raw
    .map((item, index) => {
      const record = pickRecord(item) ?? {};
      const title = safeText(record.title, `回放步骤 ${index + 1}`);
      const summary = safeText(record.summary, title);
      const status = normalizeStepStatus(record.status);
      return makeStep({
        stepId: safeText(record.step_id, `timeline-step-${index + 1}`),
        index,
        kind: normalizeStepKind(record.kind),
        title,
        summary,
        status,
        timestamp: pickString(record.timestamp) ?? undefined,
        cue: pickString(record.cue) ?? buildCue(title, summary),
        failurePoint: pickBoolean(record.failure_point) ?? undefined,
        fields: buildFields(record.fields),
      });
    })
    .filter((item) => item.title !== "—");
}

function stepsFromEvents(detail: Record<string, unknown>, snapshot: Record<string, unknown>, riskId: string): RiskReplayStep[] {
  const events = pickArray(snapshot.recent_events)
    .map((item) => pickRecord(item))
    .filter((item): item is Record<string, unknown> => Boolean(item))
    .filter((item) => eventMatchesRisk(item, detail, riskId));

  return events.map((event, index) => {
    const title = safeText(event.title, `运行步骤 ${index + 1}`);
    const summary = safeText(event.message, title);
    const kind = normalizeStepKind(event.kind);
    const fields: RiskReplayField[] = [];
    const request = pickRecord(event.request) ?? {};
    const response = pickRecord(event.response) ?? {};

    const method = pickString(request.method);
    const path = pickString(request.path);
    if (method || path) fields.push({ label: "请求", value: `${method ?? "GET"} ${path ?? "/"}` });

    const statusCode = pickNumber(response.status_code ?? response.status);
    if (statusCode !== null) fields.push({ label: "响应", value: `HTTP ${statusCode}` });

    return makeStep({
      stepId: safeText(event.event_id, `runtime-step-${index + 1}`),
      index,
      kind,
      title,
      summary,
      status: normalizeStepStatus(event.status),
      timestamp: pickString(event.timestamp) ?? undefined,
      cue: pickString(event.phase_label) ?? buildCue(title, summary),
      fields,
    });
  });
}

function stepsFromEvidence(detail: Record<string, unknown>, riskId: string): RiskReplayStep[] {
  const risk = pickRecord(detail.risk) ?? detail;
  const evidence = pickRecord(detail.evidence_bundle) ?? {};
  const request = pickRecord(evidence.request_summary) ?? {};
  const response = pickRecord(evidence.response_summary) ?? {};
  const discoveryPath = safeList(evidence.discovery_path);
  const reproduction = safeList(evidence.reproduction_steps);
  const steps: RiskReplayStep[] = [];
  let index = 0;

  steps.push(
    makeStep({
      stepId: `${riskId}-entry`,
      index: index++,
      kind: "entry",
      title: "进入证据回放",
      summary: safeText(evidence.summary, safeText(risk.summary, safeText(risk.business_impact, "已进入风险复核流程。"))),
      status: "completed",
      cue: "风险详情已切换为 replay 视图",
      fields: [
        { label: "风险", value: safeText(risk.title, riskId) },
        { label: "严重度", value: safeText(risk.severity, "unknown") },
      ],
    }),
  );

  for (const item of discoveryPath) {
    steps.push(
      makeStep({
        stepId: `${riskId}-discovery-${index}`,
        index,
        kind: "discovery",
        title: `发现路径 ${index}`,
        summary: item,
        status: "completed",
        cue: item,
      }),
    );
    index += 1;
  }

  if (Object.keys(request).length) {
    const method = safeText(request.method, "GET");
    const path = safeText(request.path, "/");
    steps.push(
      makeStep({
        stepId: `${riskId}-request`,
        index: index++,
        kind: "request",
        title: "发送关键请求",
        summary: `${method} ${path}`,
        status: "completed",
        cue: safeText(request.key_signal, "请求已进入回放序列"),
        fields: [{ label: "请求", value: `${method} ${path}` }],
      }),
    );
  }

  if (Object.keys(response).length) {
    const responseValue = response.status_code ?? response.status;
    const responseStatus = statusFromResponseCode(responseValue);
    steps.push(
      makeStep({
        stepId: `${riskId}-response`,
        index: index++,
        kind: "response",
        title: "观察关键响应",
        summary: `HTTP ${safeText(responseValue, "—")} · ${safeText(response.observed_issue ?? response.key_signal, "等待人工确认")}`,
        status: responseStatus,
        cue: safeText(response.key_signal ?? response.observed_issue, "响应已产生差异"),
        fields: [{ label: "响应", value: `HTTP ${safeText(responseValue, "—")}` }],
      }),
    );
  }

  if (reproduction.length) {
    steps.push(
      makeStep({
        stepId: `${riskId}-reproduction`,
        index: index++,
        kind: "reproduction",
        title: "形成复现包",
        summary: `已整理 ${reproduction.length} 条复现步骤，可直接复制给测试或交付同学。`,
        status: "warning",
        cue: reproduction[0],
        fields: [{ label: "步骤数", value: String(reproduction.length) }],
      }),
    );
  }

  steps.push(
    makeStep({
      stepId: `${riskId}-finding`,
      index,
      kind: "finding",
      title: "沉淀为 Finding / 风险结论",
      summary: safeText(risk.business_impact, safeText(evidence.summary, "该异常已形成风险结论。")),
      status: pickBoolean(risk.launch_blocking) ? "failed" : "warning",
      cue: safeText(risk.suggested_action, "需要安排修复与回归"),
      fields: [{ label: "上线阻断", value: pickBoolean(risk.launch_blocking) ? "是" : "否" }],
    }),
  );

  return steps;
}

function buildJumpTargets(steps: readonly RiskReplayStep[], detail: Record<string, unknown>): RiskReplayJumpTarget[] {
  const evidence = pickRecord(detail.evidence_bundle) ?? {};
  const explicit = pickArray(evidence.failure_points)
    .map((item) => {
      const record = pickRecord(item) ?? {};
      const stepId = pickString(record.step_id);
      if (!stepId) return null;
      return {
        stepId,
        label: safeText(record.label, "失败点"),
        reason: safeText(record.reason, "该步骤存在失败或风险信号。"),
      };
    })
    .filter((item): item is RiskReplayJumpTarget => Boolean(item));

  if (explicit.length) return explicit;

  return steps
    .filter((step) => step.failurePoint)
    .map((step) => ({
      stepId: step.stepId,
      label: step.title,
      reason: step.cue ?? step.summary,
    }));
}

function buildCopyText(detail: Record<string, unknown>, steps: readonly string[]): string {
  const evidence = pickRecord(detail.evidence_bundle) ?? {};
  const header = safeText(evidence.summary, "风险复现步骤");
  if (!steps.length) return header;
  return [header, "", ...steps.map((step, index) => `${index + 1}. ${step}`)].join("\n");
}

export function buildRiskReplayTimeline(input: {
  projectId: string;
  riskId: string;
  detail: unknown;
  snapshot?: unknown;
}): RiskReplayTimeline {
  const detail = pickRecord(input.detail) ?? {};
  const snapshot = pickRecord(input.snapshot) ?? {};
  const risk = pickRecord(detail.risk) ?? detail;
  const evidence = pickRecord(detail.evidence_bundle) ?? {};
  const reproductionSteps = safeList(evidence.reproduction_steps);

  const explicitSteps = stepsFromExplicitTimeline(detail);
  const eventSteps = explicitSteps.length ? [] : stepsFromEvents(detail, snapshot, input.riskId);
  const synthesizedSteps = explicitSteps.length || eventSteps.length ? [] : stepsFromEvidence(detail, input.riskId);
  const steps = [...explicitSteps, ...eventSteps, ...synthesizedSteps].map((step, index) => ({ ...step, index }));
  const jumpTargets = buildJumpTargets(steps, detail);

  return {
    projectId: input.projectId,
    riskId: input.riskId,
    title: safeText(risk.title, input.riskId),
    summary: safeText(evidence.summary, safeText(risk.business_impact, safeText(risk.summary, "暂无 replay 摘要。"))),
    status: mergeTimelineStatus(steps),
    updatedAt: pickString(detail.updated_at) ?? pickString(detail.generated_at) ?? pickString(snapshot.updated_at) ?? undefined,
    reproductionSteps,
    copyText: buildCopyText(detail, reproductionSteps),
    steps,
    jumpTargets,
    metrics: {
      totalSteps: steps.length,
      failureSteps: steps.filter((step) => step.failurePoint).length,
      replayReady: reproductionSteps.length > 0 && steps.length > 0,
    },
  };
}
