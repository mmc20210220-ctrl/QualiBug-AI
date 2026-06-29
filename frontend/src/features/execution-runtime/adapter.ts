import type { EvidenceDrawerData } from "@/components/evidence/EvidenceDrawer";
import { maskId, redactUnknown } from "@/lib/redact";
import { buildRuntimeExecutionMockTheater } from "./mock";
import type {
  RuntimeEvent,
  RuntimeEventEvidence,
  RuntimeEventKind,
  RuntimeEventStatus,
  RuntimeExecutionTheater,
  RuntimeLink,
  RuntimeStageEdge,
  RuntimeStageNode,
  RuntimeSummaryCard,
  RuntimeTone,
} from "./model";

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

function unwrapEnvelope(value: unknown): Record<string, unknown> {
  const record = pickRecord(value) ?? {};
  if ("data" in record) {
    const data = pickRecord(record.data);
    if (data) return data;
  }
  return record;
}

function inferSource(snapshotEnvelope: unknown): RuntimeExecutionTheater["source"] {
  const meta = pickRecord(snapshotEnvelope);
  const data = pickRecord(meta?.data);
  const marker = safeText(data?.source, "").toLowerCase();
  if (marker === "demo") return "demo";
  if (marker === "real") return "real";
  return "merged";
}

function containsKeyword(text: string, pattern: RegExp): boolean {
  return pattern.test(text);
}

function inferKind(rawKind: string, text: string): RuntimeEventKind {
  const normalized = `${rawKind} ${text}`.toLowerCase();
  if (containsKeyword(normalized, /(finding|risk|缺陷|风险|双扣|账务|问题)/i)) return "finding";
  if (containsKeyword(normalized, /(block|阻断|403|401|500|denied|forbid)/i)) return "blocker";
  if (containsKeyword(normalized, /(request|请求|path|api)/i)) return "request";
  if (containsKeyword(normalized, /(response|响应|status code|返回)/i)) return "response";
  if (containsKeyword(normalized, /(snapshot|快照|before|after)/i)) return "snapshot";
  if (containsKeyword(normalized, /(probe|回放|链路|dispatch|执行)/i)) return "probe";
  if (containsKeyword(normalized, /(summary|completed|结束|完成|归档)/i)) return "summary";
  return "lifecycle";
}

function normalizeStatus(rawStatus: string, text: string, kind: RuntimeEventKind): RuntimeEventStatus {
  const normalized = `${rawStatus} ${text}`.toLowerCase();
  if (containsKeyword(normalized, /(block|阻断|forbid|deny)/i)) return "blocked";
  if (containsKeyword(normalized, /(failed|error|异常|失败)/i)) return "failed";
  if (containsKeyword(normalized, /(warning|风险|finding|发现)/i)) return kind === "finding" ? "warning" : "warning";
  if (containsKeyword(normalized, /(success|passed|healthy|通过)/i)) return "success";
  if (containsKeyword(normalized, /(completed|done|finished|accepted|结束|完成)/i)) return "completed";
  if (containsKeyword(normalized, /(running|progress|进行中|执行中)/i)) return "running";
  return "pending";
}

function toneFromStatus(status: RuntimeEventStatus, kind: RuntimeEventKind): RuntimeTone {
  if (status === "blocked" || status === "failed") return "critical";
  if (status === "warning") return kind === "finding" ? "critical" : "warning";
  if (status === "success" || status === "completed") return "success";
  if (status === "running") return "info";
  return "neutral";
}

function inferNodeId(kind: RuntimeEventKind, text: string): string {
  const normalized = text.toLowerCase();
  if (kind === "finding" || kind === "blocker") return "stage-finding";
  if (kind === "summary") return "stage-summary";
  if (kind === "snapshot" || containsKeyword(normalized, /(snapshot|快照)/i)) return "stage-snapshot";
  if (kind === "request" || kind === "response" || containsKeyword(normalized, /(request|response|请求|响应|api)/i)) return "stage-traffic";
  if (kind === "probe" || containsKeyword(normalized, /(probe|回放|链路|dispatch|注入)/i)) return "stage-probe";
  return "stage-entry";
}

function inferEdgeId(nodeId: string): string {
  if (nodeId === "stage-entry") return "edge-entry-probe";
  if (nodeId === "stage-probe") return "edge-probe-traffic";
  if (nodeId === "stage-traffic") return "edge-traffic-snapshot";
  if (nodeId === "stage-snapshot") return "edge-snapshot-finding";
  if (nodeId === "stage-finding") return "edge-finding-summary";
  return "edge-finding-summary";
}

function linkFromHref(href: string | null, label: string, kind: RuntimeLink["kind"]): RuntimeLink | null {
  if (!href) return null;
  return { href, label, kind };
}

function buildEventEvidence(
  item: Record<string, unknown>,
  projectId: string,
  runId: string | null,
  timestamp: string | undefined,
): RuntimeEventEvidence | undefined {
  const evidence = pickRecord(item.evidence) ?? {};
  const request = pickRecord(item.request) ?? {};
  const response = pickRecord(item.response) ?? {};
  const finding = pickRecord(item.finding) ?? {};
  const fields = [
    { label: "时间", value: timestamp ?? "—" },
    { label: "runId", value: runId ? maskId(runId, 6, 4) : "—" },
  ];

  const method = pickString(request.method);
  const path = pickString(request.path);
  if (method || path) fields.push({ label: "请求", value: `${method ?? "GET"} ${path ?? "/"}` });

  const statusCode = pickNumber(response.status_code ?? response.status);
  if (statusCode !== null) fields.push({ label: "响应", value: `HTTP ${statusCode}` });

  const findingId = pickString(finding.risk_id ?? finding.finding_id ?? item.risk_id);
  if (findingId) fields.push({ label: "关联风险", value: maskId(findingId, 8, 4) });

  const sections = [];
  const discoveryPath = safeList(evidence.discovery_path);
  if (discoveryPath.length) sections.push({ title: "发现路径", items: discoveryPath });

  const reproductionSteps = safeList(evidence.reproduction_steps);
  if (reproductionSteps.length) sections.push({ title: "复现建议", items: reproductionSteps });

  const rawSections = pickArray(evidence.sections).map((section) => {
    const record = pickRecord(section) ?? {};
    return {
      title: safeText(record.title, "更多信息"),
      items: safeList(record.items),
    };
  });

  for (const section of rawSections) {
    if (section.items.length) sections.push(section);
  }

  const projectHref = `/projects/${encodeURIComponent(projectId)}/execution`;
  const riskHref = findingId ? `/projects/${encodeURIComponent(projectId)}/risks/${encodeURIComponent(findingId)}` : null;
  const artifactHref = runId
    ? `/api/ui/projects/${encodeURIComponent(projectId)}/test-runs/${encodeURIComponent(runId)}`
    : null;

  const links = [
    linkFromHref(projectHref, "执行页", "page"),
    linkFromHref(riskHref, "关联风险", "page"),
    linkFromHref(artifactHref, "运行 Artifact", "artifact"),
    ...pickArray(evidence.links).map((link) => {
      const record = pickRecord(link) ?? {};
      const href = pickString(record.href);
      if (!href) return null;
      return {
        label: safeText(record.label, "附加入口"),
        href,
        kind: safeText(record.kind, "page") === "artifact" ? "artifact" : "page",
      } as RuntimeLink;
    }),
  ].filter((item): item is RuntimeLink => Boolean(item));

  const summary =
    pickString(evidence.summary) ??
    pickString(item.summary) ??
    pickString(item.message) ??
    pickString(item.title) ??
    undefined;
  const reason =
    pickString(evidence.reason) ??
    pickString(response.key_signal) ??
    pickString(request.key_signal) ??
    pickString(item.reason) ??
    undefined;
  const remediationAction = pickString(evidence.remediation_action ?? item.next_action ?? item.remediation_action) ?? undefined;

  if (!summary && !reason && !remediationAction && fields.length <= 2 && !sections.length && !links.length) {
    return undefined;
  }

  return {
    summary,
    reason,
    remediationAction,
    fields,
    sections,
    links,
  };
}

function normalizeEvent(
  item: unknown,
  index: number,
  input: { projectId: string; runId: string | null; defaultTimestamp?: string },
): RuntimeEvent | null {
  const record = pickRecord(item);
  if (!record) return null;
  const title = safeText(record.title ?? record.message ?? record.summary, "");
  const message = safeText(record.message ?? record.summary ?? record.title, "");
  if (!title && !message) return null;

  const rawKind = safeText(record.kind, "");
  const text = `${title} ${message}`;
  const kind = inferKind(rawKind, text);
  const status = normalizeStatus(safeText(record.status, ""), text, kind);
  const nodeId = pickString(record.node_id) ?? inferNodeId(kind, text);
  const edgeId = pickString(record.edge_id) ?? inferEdgeId(nodeId);
  const timestamp = pickString(record.timestamp) ?? input.defaultTimestamp;
  const phaseLabel = pickString(record.phase_label) ?? undefined;
  const evidence = buildEventEvidence(record, input.projectId, input.runId, timestamp);
  const riskId = pickString((pickRecord(record.finding) ?? {}).risk_id ?? record.risk_id);
  const nextAction =
    pickString(record.next_action ?? record.remediation_action) ??
    evidence?.remediationAction ??
    (status === "blocked" || status === "failed"
      ? "优先回到风险或环境页确认阻断项，再决定是否重试。"
      : kind === "finding"
        ? "下钻到风险详情复核证据并安排修复。"
        : undefined);

  return {
    eventId: pickString(record.event_id) ?? `${nodeId}-${kind}-${index + 1}`,
    kind,
    status,
    tone: toneFromStatus(status, kind),
    title: title || message || `执行事件 ${index + 1}`,
    message: message || title || `执行事件 ${index + 1}`,
    timestamp,
    runId: input.runId,
    nodeId,
    edgeId,
    actor: pickString(record.actor) ?? undefined,
    phaseLabel,
    tags: [
      kind,
      status,
      riskId ? `risk:${maskId(riskId, 8, 4)}` : "",
      phaseLabel ?? "",
    ].filter(Boolean),
    nextAction,
    evidence,
  };
}

function buildSynthesizedEvents(
  projectId: string,
  input: {
    runId: string | null;
    runStatus: string;
    updatedAt?: string;
    progress: number | null;
    probeTotal: number | null;
    probeCompleted: number | null;
    probeFailed: number | null;
    riskFound: number | null;
  },
): RuntimeEvent[] {
  const events: RuntimeEvent[] = [];
  const normalizedStatus = normalizeStatus(input.runStatus, input.runStatus, "lifecycle");

  if (input.runId || input.runStatus !== "idle") {
    events.push({
      eventId: `lifecycle-${input.runId ?? "current"}`,
      kind: "lifecycle",
      status: normalizedStatus === "pending" ? "running" : normalizedStatus,
      tone: toneFromStatus(normalizedStatus === "pending" ? "running" : normalizedStatus, "lifecycle"),
      title: `运行状态：${input.runStatus || "idle"}`,
      message:
        input.progress === null
          ? "运行已建立，可继续观察后续 probe 和 finding 落点。"
          : `当前进度 ${Math.round(Math.max(0, Math.min(1, input.progress)) * 100)}%，事件正在持续汇入执行剧场。`,
      timestamp: input.updatedAt,
      runId: input.runId,
      nodeId: "stage-entry",
      edgeId: "edge-entry-probe",
      tags: ["lifecycle", input.runStatus || "idle"],
      evidence: {
        fields: [
          { label: "runId", value: input.runId ? maskId(input.runId, 6, 4) : "—" },
          { label: "状态", value: input.runStatus || "idle" },
          { label: "进度", value: input.progress === null ? "—" : `${Math.round(input.progress * 100)}%` },
        ],
        links: [{ label: "执行页", href: `/projects/${encodeURIComponent(projectId)}/execution`, kind: "page" }],
      },
    });
  }

  if (input.probeTotal !== null || input.probeCompleted !== null) {
    const status =
      (input.probeFailed ?? 0) > 0 ? "warning" : input.probeTotal !== null && input.probeCompleted === input.probeTotal ? "success" : "running";
    events.push({
      eventId: `probe-${input.runId ?? "current"}`,
      kind: "probe",
      status,
      tone: toneFromStatus(status, "probe"),
      title: "Probe 路径推进",
      message: `已完成 ${input.probeCompleted ?? 0}/${input.probeTotal ?? 0}，失败 ${(input.probeFailed ?? 0)}。`,
      timestamp: input.updatedAt,
      runId: input.runId,
      nodeId: "stage-probe",
      edgeId: "edge-probe-traffic",
      tags: ["probe", status],
      nextAction:
        (input.probeFailed ?? 0) > 0 ? "检查失败 probe 对应的请求、快照与权限边界。" : "继续观察请求响应与快照差异。",
    });
  }

  if ((input.riskFound ?? 0) > 0) {
    events.push({
      eventId: `finding-${input.runId ?? "current"}`,
      kind: "finding",
      status: "warning",
      tone: "critical",
      title: "运行产出 Finding",
      message: `当前运行累计发现 ${input.riskFound} 个风险或异常落点。`,
      timestamp: input.updatedAt,
      runId: input.runId,
      nodeId: "stage-finding",
      edgeId: "edge-snapshot-finding",
      tags: ["finding", "warning"],
      nextAction: "优先查看上线阻断风险与对应证据包，确认是否需要停止放量。",
      evidence: {
        links: [{ label: "风险列表", href: `/projects/${encodeURIComponent(projectId)}/risks`, kind: "page" }],
      },
    });
  }

  return events;
}

function eventRank(status: RuntimeEventStatus): number {
  if (status === "blocked") return 6;
  if (status === "failed") return 5;
  if (status === "warning") return 4;
  if (status === "running") return 3;
  if (status === "completed") return 2;
  if (status === "success") return 1;
  return 0;
}

function mergeStatuses(statuses: readonly RuntimeEventStatus[]): RuntimeEventStatus {
  if (!statuses.length) return "pending";
  return [...statuses].sort((left, right) => eventRank(right) - eventRank(left))[0] ?? "pending";
}

function compareTimestampDesc(left: RuntimeEvent, right: RuntimeEvent): number {
  const l = left.timestamp ? Date.parse(left.timestamp) : 0;
  const r = right.timestamp ? Date.parse(right.timestamp) : 0;
  return r - l;
}

function updateNodes(baseNodes: readonly RuntimeStageNode[], events: readonly RuntimeEvent[], runStatus: string): RuntimeStageNode[] {
  return baseNodes.map((node) => {
    const nodeEvents = events.filter((event) => event.nodeId === node.nodeId);
    const latest = nodeEvents[0];
    const status = mergeStatuses(nodeEvents.map((event) => event.status));
    const headline =
      latest?.title ??
      (node.nodeId === "stage-summary" && isTerminalRuntimeStatus(runStatus) ? "已生成执行总结" : node.headline);
    const detail = latest?.message ?? node.detail;
    const emphasis = nodeEvents.some((event) => event.tone === "critical" || event.status === "blocked" || event.status === "failed");

    return {
      ...node,
      status: nodeEvents.length ? status : node.status,
      headline,
      detail,
      badges: latest ? latest.tags.slice(0, 3) : node.badges,
      eventIds: nodeEvents.map((event) => event.eventId),
      emphasis,
    };
  });
}

function updateEdges(baseEdges: readonly RuntimeStageEdge[], events: readonly RuntimeEvent[]): RuntimeStageEdge[] {
  return baseEdges.map((edge) => {
    const edgeEvents = events.filter((event) => event.edgeId === edge.edgeId);
    return {
      ...edge,
      status: edgeEvents.length ? mergeStatuses(edgeEvents.map((event) => event.status)) : edge.status,
      eventIds: edgeEvents.map((event) => event.eventId),
      emphasis: edgeEvents.some((event) => event.tone === "critical" || event.status === "warning"),
    };
  });
}

function createSummaryCard(
  runStatus: string,
  updatedAt: string | undefined,
  events: readonly RuntimeEvent[],
  input: {
    progress: number | null;
    probeTotal: number | null;
    probeCompleted: number | null;
    probeFailed: number | null;
    riskFound: number | null;
  },
): RuntimeSummaryCard | null {
  if (!isTerminalRuntimeStatus(runStatus)) return null;

  const failedCount = events.filter((event) => event.status === "failed").length;
  const blockedCount = events.filter((event) => event.status === "blocked").length;
  const findingCount = events.filter((event) => event.kind === "finding" || event.kind === "blocker").length;
  const latestHighlight = events.find((event) => event.tone === "critical" || event.status === "warning");
  const outcome = failedCount > 0 || blockedCount > 0 ? "failed" : findingCount > 0 ? "warning" : "success";

  return {
    title: outcome === "success" ? "执行完成，可进入交付复核" : outcome === "warning" ? "执行完成，但需先处理关键发现" : "执行结束，存在失败或阻断",
    outcome,
    completedAt: updatedAt,
    summary:
      outcome === "success"
        ? "运行主链已完成，未观察到阻断级异常，可转入证据整理与交付审计。"
        : outcome === "warning"
          ? "运行已结束并暴露出高价值 finding，建议先完成证据复核和优先级收敛。"
          : "运行已结束，但事件流中存在失败或阻断，需要先定位根因再决定是否重试。",
    highlights: [
      input.probeTotal !== null ? `Probe 完成 ${input.probeCompleted ?? 0}/${input.probeTotal}` : "",
      input.probeFailed !== null ? `失败 Probe ${input.probeFailed}` : "",
      input.riskFound !== null ? `风险 / Finding ${input.riskFound}` : "",
      latestHighlight ? `最新关键事件：${latestHighlight.title}` : "",
    ].filter(Boolean),
    nextActions: [
      failedCount > 0 || blockedCount > 0 ? "优先打开关键失败/阻断事件，复核请求、响应与快照证据。" : "",
      findingCount > 0 ? "对发现的风险逐条确认上线影响，并安排修复与回归。" : "",
      "整理 Artifact、风险页与领导层报告之间的跳转关系，形成可交付闭环。",
    ].filter(Boolean),
    metrics: [
      {
        metricId: "progress",
        label: "完成度",
        value: input.progress === null ? "—" : `${Math.round(Math.max(0, Math.min(1, input.progress)) * 100)}%`,
        tone: outcome === "failed" ? "warning" : "success",
      },
      {
        metricId: "probe",
        label: "Probe",
        value:
          input.probeTotal === null
            ? "—"
            : `${input.probeCompleted ?? 0}/${input.probeTotal} · 失败 ${input.probeFailed ?? 0}`,
        tone: (input.probeFailed ?? 0) > 0 ? "warning" : "success",
      },
      {
        metricId: "finding",
        label: "Finding",
        value: `${input.riskFound ?? findingCount}`,
        tone: (input.riskFound ?? findingCount) > 0 ? "critical" : "success",
      },
    ],
  };
}

export function extractRunIdFromSnapshotEnvelope(snapshotEnvelope: unknown): string | null {
  const snapshot = unwrapEnvelope(snapshotEnvelope);
  const liveMap = pickRecord(snapshot.live_map) ?? {};
  return pickString(liveMap.run_id) ?? null;
}

export function isTerminalRuntimeStatus(status: string): boolean {
  const normalized = status.toLowerCase();
  return /(success|completed|done|finished|failed|error|canceled|cancelled)/i.test(normalized);
}

export function buildRuntimeExecutionTheater(input: {
  projectId: string;
  snapshotEnvelope?: unknown | null;
  runEnvelope?: unknown | null;
  overrideRunId?: string | null;
}): RuntimeExecutionTheater {
  const base = buildRuntimeExecutionMockTheater(input.projectId);
  const snapshot = unwrapEnvelope(input.snapshotEnvelope);
  const liveMap = pickRecord(snapshot.live_map) ?? {};
  const run = unwrapEnvelope(input.runEnvelope);
  const runId = input.overrideRunId ?? pickString(run.run_id) ?? pickString(liveMap.run_id) ?? null;
  const updatedAt = pickString(run.updated_at) ?? pickString(liveMap.updated_at) ?? pickString(snapshot.updated_at) ?? undefined;
  const runStatus = safeText(run.status ?? liveMap.status, "idle");
  const progress = pickNumber(run.progress);
  const probeTotal = pickNumber(run.probe_total);
  const probeCompleted = pickNumber(run.probe_completed);
  const probeFailed = pickNumber(run.probe_failed);
  const riskFound = pickNumber(run.risk_found);
  const source = input.snapshotEnvelope ? inferSource(input.snapshotEnvelope) : base.source;

  const normalizedEvents = pickArray(snapshot.recent_events)
    .map((item, index) => normalizeEvent(item, index, { projectId: input.projectId, runId, defaultTimestamp: updatedAt }))
    .filter((item): item is RuntimeEvent => Boolean(item));
  const synthesizedEvents = buildSynthesizedEvents(input.projectId, {
    runId,
    runStatus,
    updatedAt,
    progress,
    probeTotal,
    probeCompleted,
    probeFailed,
    riskFound,
  });

  const deduped = new Map<string, RuntimeEvent>();
  for (const event of [...normalizedEvents, ...synthesizedEvents]) {
    if (!deduped.has(event.eventId)) deduped.set(event.eventId, event);
  }
  const events = [...deduped.values()].sort(compareTimestampDesc);
  const nodes = updateNodes(base.nodes, events, runStatus);
  const edges = updateEdges(base.edges, events);
  const summaryCard = createSummaryCard(runStatus, updatedAt, events, {
    progress,
    probeTotal,
    probeCompleted,
    probeFailed,
    riskFound,
  });

  return {
    ...base,
    source,
    runId,
    runStatus,
    updatedAt,
    metrics: {
      progress,
      probeTotal,
      probeCompleted,
      probeFailed,
      riskFound,
    },
    nodes,
    edges,
    events,
    summaryCard,
  };
}

function toneToDrawerTone(tone: RuntimeTone): EvidenceDrawerData["tone"] {
  if (tone === "critical") return "critical";
  if (tone === "warning") return "warning";
  if (tone === "success") return "success";
  if (tone === "info") return "info";
  return "neutral";
}

function statusLabel(event: RuntimeEvent): string {
  if (event.status === "blocked") return "执行阻断";
  if (event.status === "failed") return "执行失败";
  if (event.kind === "finding") return "Finding";
  if (event.kind === "blocker") return "Blocker";
  if (event.status === "success" || event.status === "completed") return "已完成";
  if (event.status === "running") return "执行中";
  return "执行事件";
}

export function runtimeEventToEvidenceDrawerData(event: RuntimeEvent): EvidenceDrawerData {
  return {
    id: event.eventId,
    typeLabel: `Runtime ${event.kind}`,
    title: event.title,
    summary: event.evidence?.summary ?? event.message,
    reason: event.evidence?.reason,
    remediationAction: event.nextAction ?? event.evidence?.remediationAction,
    tone: toneToDrawerTone(event.tone),
    statusLabel: statusLabel(event),
    fields: [
      ...(event.evidence?.fields ?? []),
      ...(event.tags.length ? [{ label: "标签", value: event.tags.join(" · ") }] : []),
    ],
    sections: event.evidence?.sections,
    links: event.evidence?.links,
  };
}
