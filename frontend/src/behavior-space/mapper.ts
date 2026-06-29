import { redactUnknown } from "@/lib/redact";
import type {
  BehaviorAuditRef,
  BehaviorCoverageStatus,
  BehaviorPath,
  BehaviorReplayRef,
  BehaviorSpaceActionItem,
  BehaviorSpaceDataBundle,
  BehaviorSpaceSceneStatus,
  BehaviorSpaceTone,
  BehaviorSpaceValueField,
  BehaviorSpaceValueSummary,
  BehaviorSpaceVisualization,
  BehaviorSystemNode,
  ProbeExecution,
  ProbeExecutionStatus,
  BehaviorFinding,
  BehaviorEvidenceRef,
} from "./types";

function pickRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

function pickArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function pickString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
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
  if (typeof value === "boolean") return value;
  if (value === "true") return true;
  if (value === "false") return false;
  return null;
}

function unwrapData<T = unknown>(value: unknown): T | unknown {
  const record = pickRecord(value);
  if (record && "success" in record && "data" in record) return record.data as T;
  return value;
}

function asRecordData(value: unknown): Record<string, unknown> {
  return pickRecord(unwrapData(value)) ?? {};
}

function asArrayData(value: unknown): unknown[] {
  const raw = unwrapData(value);
  if (Array.isArray(raw)) return raw;
  const record = pickRecord(raw);
  if (!record) return [];
  if (Array.isArray(record.items)) return record.items;
  if (Array.isArray(record.risks)) return record.risks;
  if (Array.isArray(record.data)) return record.data;
  return [];
}

function safeText(value: unknown, fallback = "—"): string {
  if (typeof value === "number") return String(value);
  const raw = pickString(value);
  if (!raw) return fallback;
  const redacted = redactUnknown(raw);
  return typeof redacted === "string" && redacted.trim() ? redacted : fallback;
}

function safeId(value: unknown, fallback: string): string {
  const raw = pickString(value);
  return raw && raw.trim() ? raw : fallback;
}

function compactStrings(values: unknown[]): string[] {
  const items: string[] = [];
  for (const value of values) {
    const text = safeText(value, "");
    if (text) items.push(text);
  }
  return items;
}

function firstNonEmpty(...values: unknown[]): string | null {
  for (const value of values) {
    const text = safeText(value, "");
    if (text) return text;
  }
  return null;
}

function deriveSceneStatus(input: { environmentStatus: string; blockerCount: number; launchTitle: string; riskCount: number }): BehaviorSpaceSceneStatus {
  const env = input.environmentStatus.toLowerCase();
  const launch = input.launchTitle.toLowerCase();
  if (env.includes("block") || launch.includes("阻断") || launch.includes("不建议") || input.blockerCount > 0) return "blocked";
  if (env.includes("warn") || launch.includes("谨慎") || input.riskCount > 0) return "warning";
  if (env.includes("ready") || env.includes("pass") || launch.includes("可上线") || launch.includes("建议上线")) return "ready";
  return "unknown";
}

function deriveCoverageStatus(input: { status: string; blockerCount: number; riskCount: number; rate: number | null }): BehaviorCoverageStatus {
  const status = input.status.toLowerCase();
  if (input.blockerCount > 0 || status.includes("block")) return "blocked";
  if (status.includes("covered") || status.includes("complete")) return "covered";
  if (status.includes("partial")) return "partial";
  if (status.includes("uncovered") || status.includes("missing")) return "uncovered";
  if (input.rate !== null) {
    if (input.rate >= 0.85) return "covered";
    if (input.rate >= 0.4) return "partial";
    return "uncovered";
  }
  if (input.riskCount > 0) return "partial";
  return "unknown";
}

function deriveProbeStatus(status: string): ProbeExecutionStatus {
  const s = status.toLowerCase();
  if (s.includes("queue")) return "queued";
  if (s.includes("run")) return "running";
  if (s.includes("complete") || s.includes("success") || s.includes("done")) return "completed";
  if (s.includes("block")) return "blocked";
  if (s.includes("fail") || s.includes("error")) return "failed";
  if (s.includes("plan")) return "planned";
  return "unknown";
}

function deriveTone(status: BehaviorSpaceSceneStatus | BehaviorCoverageStatus, hasPositiveFallback = false): BehaviorSpaceTone {
  if (status === "blocked" || status === "uncovered") return "critical";
  if (status === "warning" || status === "partial") return "warning";
  if (status === "ready" || status === "covered") return "positive";
  return hasPositiveFallback ? "positive" : "neutral";
}

function formatPercent(value: number | null): string {
  if (value === null) return "—";
  const normalized = value <= 1 ? value * 100 : value;
  return `${Math.round(normalized)}%`;
}

function formatHours(value: number | null): string {
  if (value === null) return "—";
  return `${value} h`;
}

function formatCurrencyRange(input: { min: number | null; max: number | null; currency: string | null }): string {
  const min = input.min;
  const max = input.max;
  const currency = input.currency ?? "CNY";
  if (min === null && max === null) return "—";
  if (min !== null && max !== null) return `${min.toLocaleString()} - ${max.toLocaleString()} ${currency}`;
  return `${(min ?? max ?? 0).toLocaleString()} ${currency}`;
}

function buildProjectHref(projectId: string, path: string): string {
  return `/projects/${encodeURIComponent(projectId)}${path}`;
}

function buildBehaviorSpaceAnchorHref(projectId: string, anchor: string): string {
  return `${buildProjectHref(projectId, "/behavior-space")}#${anchor}`;
}

function summarizeRequestSummary(request: Record<string, unknown>): string {
  return `${safeText(request.method, "GET")} ${safeText(request.path)} · 身份 ${safeText(request.auth_context, "待补充")}`;
}

function summarizeResponseSummary(response: Record<string, unknown>): string {
  return `状态 ${safeText(response.status_code)} · ${safeText(response.observed_issue, "响应摘要待补充")}`;
}

function summarizeDiscoveryPath(items: unknown[]): string {
  const summary = items
    .slice(0, 3)
    .map((item, index) => {
      const step = pickRecord(item);
      if (step) {
        return `${index + 1}. ${safeText(step.name, `步骤 ${index + 1}`)} · ${safeText(step.status, "passed")}`;
      }
      return `${index + 1}. ${safeText(item, `步骤 ${index + 1}`)}`;
    })
    .join(" / ");
  return summary || "发现路径待补充";
}

function actionToHref(projectId: string, label: string): { href?: string; routeId?: string } {
  const normalized = label.toLowerCase();
  if (normalized.includes("环境")) return { href: buildProjectHref(projectId, "/environment"), routeId: "environment_diagnosis" };
  if (normalized.includes("链路") || normalized.includes("覆盖")) return { href: buildProjectHref(projectId, "/capabilities"), routeId: "capability_center" };
  if (normalized.includes("风险") || normalized.includes("阻断")) return { href: buildProjectHref(projectId, "/risks?launch_blocking=true"), routeId: "risk_evidence_list" };
  if (normalized.includes("报告")) return { href: buildProjectHref(projectId, "/reports/executive"), routeId: "executive_report" };
  if (normalized.includes("roi") || normalized.includes("价值")) return { href: buildProjectHref(projectId, "/roi"), routeId: "roi_value" };
  if (normalized.includes("执行") || normalized.includes("run")) return { href: buildProjectHref(projectId, "/execution"), routeId: "execution" };
  if (normalized.includes("测试")) return { href: buildProjectHref(projectId, "/test-plan"), routeId: "test_plan" };
  return { href: buildProjectHref(projectId, "/command-center"), routeId: "command_center" };
}

function collectNextActions(projectId: string, launch: Record<string, unknown>, report: Record<string, unknown>, onboarding: Record<string, unknown>): BehaviorSpaceActionItem[] {
  const launchActions = compactStrings(pickArray(launch.required_actions));
  if (launchActions.length) return launchActions.map((label) => ({ label, ...actionToHref(projectId, label) }));

  const reportActions = pickArray(report.next_actions).map((item) => {
    const action = pickRecord(item);
    const title = action ? safeText(action.title, "") : safeText(item, "");
    const reason = action ? safeText(action.reason, "") : "";
    const label = reason ? `${title} - ${reason}` : title;
    return label;
  });
  const normalizedReportActions = compactStrings(reportActions);
  if (normalizedReportActions.length) {
    return normalizedReportActions.map((label) => ({ label, ...actionToHref(projectId, label) }));
  }

  const currentStep = firstNonEmpty(onboarding.current_step);
  const steps = compactStrings(pickArray(onboarding.steps).slice(0, 4));
  const fallback = currentStep ? [currentStep, ...steps] : steps;
  if (fallback.length) return fallback.map((label) => ({ label, ...actionToHref(projectId, label) }));

  return [{ label: "进入质量驾驶舱确认上线建议", href: buildProjectHref(projectId, "/command-center"), routeId: "command_center" }];
}

export function buildBehaviorSpaceValueSummary(bundle: BehaviorSpaceDataBundle): BehaviorSpaceValueSummary {
  const commandCenter = asRecordData(bundle.commandCenter);
  const valueMetrics = Object.keys(asRecordData(bundle.valueMetrics)).length ? asRecordData(bundle.valueMetrics) : pickRecord(commandCenter.value_metrics) ?? {};
  const businessModel = asRecordData(bundle.businessModel);
  const environment = asRecordData(bundle.environmentReadiness);
  const executiveReport = asRecordData(bundle.executiveReport);
  const onboarding = asRecordData(bundle.onboarding);
  const risks = asArrayData(bundle.risks);
  const riskSummary = pickRecord(commandCenter.risk_summary) ?? {};
  const businessSummary = pickRecord(commandCenter.business_flow_summary) ?? {};
  const launch = pickRecord(commandCenter.launch_decision) ?? {};

  const blockerCount =
    pickNumber(riskSummary.launch_blocking) ??
    pickNumber(riskSummary.launch_blocking_risks) ??
    risks.filter((risk) => pickBoolean((pickRecord(risk) ?? {}).launch_blocking) === true).length;
  const riskCount = risks.length;
  const environmentStatusText = safeText(environment.status, "unknown");
  const launchTitle = firstNonEmpty(launch.title, executiveReport.launch_recommendation, executiveReport.title, "暂无上线建议") ?? "暂无上线建议";
  const launchSummary =
    firstNonEmpty(
      launch.summary,
      executiveReport.executive_summary,
      environment.current_blockers ? `当前存在 ${pickArray(environment.current_blockers).length} 个环境阻断项。` : null,
      "当前没有足够结果支撑上线建议。",
    ) ?? "当前没有足够结果支撑上线建议。";
  const sceneStatus = deriveSceneStatus({
    environmentStatus: environmentStatusText,
    blockerCount: blockerCount ?? 0,
    launchTitle,
    riskCount,
  });

  const coverageRate = pickNumber(valueMetrics.business_flow_coverage_rate) ?? pickNumber(businessSummary.coverage_rate);
  const covered = pickNumber(businessSummary.covered);
  const total = pickNumber(businessSummary.total);
  const coverageStatus = deriveCoverageStatus({
    status: safeText(businessSummary.status, safeText(environment.status, "unknown")),
    blockerCount: blockerCount ?? 0,
    riskCount,
    rate: coverageRate,
  });
  const savedHours = pickNumber(valueMetrics.estimated_hours_saved);
  const impactMin = pickNumber(valueMetrics.estimated_business_impact_min);
  const impactMax = pickNumber(valueMetrics.estimated_business_impact_max);
  const currency = pickString(valueMetrics.currency);
  const nextActions = collectNextActions(bundle.projectId, launch, executiveReport, onboarding);
  const riskCostValue = formatCurrencyRange({ min: impactMin, max: impactMax, currency });
  const riskCostSupport = `上线阻断风险 ${blockerCount ?? 0} 个`;
  const replayCount =
    bundle.riskDetails?.filter((detail) => {
      const record = asRecordData(detail.detail);
      const evidence = pickRecord(record.evidence_bundle) ?? record;
      return pickArray(evidence.reproduction_steps).length > 0;
    }).length ?? 0;
  const auditSignals = [businessModel.approved_at, commandCenter.updated_at, executiveReport.generated_at, executiveReport.updated_at].filter(Boolean).length;

  const launchRecommendation: BehaviorSpaceValueField = {
    fieldId: "launch_recommendation",
    label: "是否可上线",
    value: launchTitle,
    tone: deriveTone(sceneStatus),
    supportingText: launchSummary,
    href: buildProjectHref(bundle.projectId, "/command-center"),
    routeId: "command_center",
    capabilityId: "command_center",
  };
  const riskCost: BehaviorSpaceValueField = {
    fieldId: "risk_cost",
    label: "风险成本",
    value: riskCostValue,
    tone: blockerCount && blockerCount > 0 ? "critical" : "neutral",
    supportingText: riskCostSupport,
    href: buildProjectHref(bundle.projectId, "/risks?launch_blocking=true"),
    routeId: "risk_evidence_list",
    capabilityId: "value_metrics",
  };
  const nextActionsField: BehaviorSpaceValueField = {
    fieldId: "next_actions",
    label: "下一步动作",
    value: nextActions.length ? nextActions.map((item) => item.label).join(" / ") : "—",
    tone: nextActions.length ? "warning" : "neutral",
    actions: nextActions,
    href: nextActions[0]?.href,
    routeId: nextActions[0]?.routeId,
  };
  const coverage: BehaviorSpaceValueField = {
    fieldId: "behavior_coverage",
    label: "行为覆盖",
    value: covered !== null && total !== null ? `${covered}/${total}` : formatPercent(coverageRate),
    tone: deriveTone(coverageStatus),
    supportingText: `覆盖率 ${formatPercent(coverageRate)}`,
    href: buildProjectHref(bundle.projectId, "/capabilities"),
    routeId: "capability_center",
    capabilityId: "value_metrics",
  };
  const efficiencyGain: BehaviorSpaceValueField = {
    fieldId: "efficiency_gain",
    label: "节省工时",
    value: formatHours(savedHours),
    tone: savedHours && savedHours > 0 ? "positive" : "neutral",
    supportingText: "按现有价值指标口径估算",
    href: buildProjectHref(bundle.projectId, "/roi"),
    routeId: "roi_value",
    capabilityId: "value_metrics",
  };
  const environmentStatus: BehaviorSpaceValueField = {
    fieldId: "environment_status",
    label: "环境状态",
    value: environmentStatusText,
    tone: deriveTone(sceneStatus),
    supportingText: `阻断项 ${pickArray(environment.current_blockers).length} 个`,
    href: buildProjectHref(bundle.projectId, "/environment"),
    routeId: "environment_diagnosis",
    capabilityId: "environment_readiness",
  };
  const replayReadiness: BehaviorSpaceValueField = {
    fieldId: "replay_readiness",
    label: "回放就绪度",
    value: replayCount > 0 ? `可回放 ${replayCount} 项` : "暂无回放包",
    tone: replayCount > 0 ? "positive" : "warning",
    supportingText: "已把复现步骤与证据摘要挂到风险点和业务路径",
    href: buildBehaviorSpaceAnchorHref(bundle.projectId, "behavior-space-replay"),
    routeId: "behavior_space_replay",
  };
  const auditReadiness: BehaviorSpaceValueField = {
    fieldId: "audit_readiness",
    label: "审计可追踪",
    value: auditSignals > 0 ? `已关联 ${auditSignals} 条审计信号` : "待补充审计记录",
    tone: auditSignals > 0 ? "positive" : "warning",
    supportingText: "已汇总审批、交付快照、导出与签收准备状态",
    href: buildBehaviorSpaceAnchorHref(bundle.projectId, "behavior-space-audit"),
    routeId: "behavior_space_audit",
  };

  return {
    launchRecommendation,
    riskCost,
    nextActions: nextActionsField,
    coverage,
    efficiencyGain,
    environmentStatus,
    replayReadiness,
    auditReadiness,
    fields: [launchRecommendation, riskCost, nextActionsField, coverage, efficiencyGain, environmentStatus, replayReadiness, auditReadiness],
  };
}

function deriveNodeKind(input: Record<string, unknown>): BehaviorSystemNode["kind"] {
  const raw = firstNonEmpty(input.node_type, input.type, input.kind, input.category, input.component_type, input.name) ?? "other";
  const normalized = raw.toLowerCase();
  if (normalized.includes("front")) return "frontend";
  if (normalized.includes("db") || normalized.includes("database")) return "database";
  if (normalized.includes("queue") || normalized.includes("mq")) return "queue";
  if (normalized.includes("api") || normalized.includes("external")) return "external_api";
  if (normalized.includes("worker") || normalized.includes("job")) return "worker";
  if (normalized.includes("service") || normalized.includes("app")) return "service";
  return "other";
}

export function mapBehaviorSpaceVisualization(bundle: BehaviorSpaceDataBundle): BehaviorSpaceVisualization {
  const commandCenter = asRecordData(bundle.commandCenter);
  const businessModel = asRecordData(bundle.businessModel);
  const environment = asRecordData(bundle.environmentReadiness);
  const executiveReport = asRecordData(bundle.executiveReport);
  const liveMap = Object.keys(asRecordData(bundle.liveMap)).length ? asRecordData(bundle.liveMap) : pickRecord(commandCenter.live_map) ?? {};
  const risks = asArrayData(bundle.risks);
  const riskDetails = bundle.riskDetails ?? [];
  const valueSummary = buildBehaviorSpaceValueSummary(bundle);
  const flowItems = pickArray(businessModel.confirmed_business_flows).map((item) => pickRecord(item)).filter(Boolean) as Record<string, unknown>[];
  const riskDetailById = new Map(riskDetails.map((item) => [item.riskId, item.detail]));

  const findingById = new Map<string, BehaviorFinding>();
  const evidenceRefs: BehaviorEvidenceRef[] = [];
  const replayRefs: BehaviorReplayRef[] = [];
  const behaviorPaths: BehaviorPath[] = [];
  const systemNodeMap = new Map<string, BehaviorSystemNode>();
  const riskCountByFlow = new Map<string, { total: number; blockers: number }>();

  for (const riskItem of risks) {
    const risk = pickRecord(riskItem) ?? {};
    const flow = pickRecord(risk.affected_business_flow) ?? {};
    const flowId = safeText(flow.business_flow_id, "");
    if (!flowId) continue;
    const stats = riskCountByFlow.get(flowId) ?? { total: 0, blockers: 0 };
    stats.total += 1;
    if (pickBoolean(risk.launch_blocking) === true) stats.blockers += 1;
    riskCountByFlow.set(flowId, stats);
  }

  for (let flowIndex = 0; flowIndex < flowItems.length; flowIndex += 1) {
    const flow = flowItems[flowIndex];
    const flowId = safeId(flow.business_flow_id, `flow-${flowIndex + 1}`);
    const flowName = safeText(flow.name, `业务链路 ${flowIndex + 1}`);
    const nodeItems = pickArray(flow.nodes).map((item) => pickRecord(item)).filter(Boolean) as Record<string, unknown>[];
    const nodeIds: string[] = [];

    for (let nodeIndex = 0; nodeIndex < nodeItems.length; nodeIndex += 1) {
      const node = nodeItems[nodeIndex];
      const nodeId = safeId(node.node_id ?? node.id, `${flowId}:node-${nodeIndex + 1}`);
      nodeIds.push(nodeId);
      if (!systemNodeMap.has(nodeId)) {
        systemNodeMap.set(nodeId, {
          nodeId,
          label: safeText(node.name, `节点 ${nodeIndex + 1}`),
          kind: deriveNodeKind(node),
          domain: firstNonEmpty(node.domain, flow.domain) ?? undefined,
          flowIds: [flowId],
          status: valueSummary.environmentStatus.tone === "critical" ? "blocked" : "ready",
          riskCount: 0,
          evidenceRefIds: [],
        });
      } else {
        const current = systemNodeMap.get(nodeId);
        if (current && !current.flowIds.includes(flowId)) {
          systemNodeMap.set(nodeId, { ...current, flowIds: [...current.flowIds, flowId] });
        }
      }
    }

    const stats = riskCountByFlow.get(flowId) ?? { total: 0, blockers: 0 };
    const pathId = flowId;
    const pathCoverage = deriveCoverageStatus({
      status: safeText(flow.status, "unknown"),
      blockerCount: stats.blockers,
      riskCount: stats.total,
      rate: null,
    });
    behaviorPaths.push({
      pathId,
      label: flowName,
      sourceNodeId: nodeIds[0],
      targetNodeId: nodeIds[nodeIds.length - 1],
      nodeIds,
      coverageStatus: pathCoverage,
      riskCount: stats.total,
      blockerCount: stats.blockers,
      evidenceRefIds: [],
      findingIds: [],
      replayRefIds: [],
    });
  }

  for (let riskIndex = 0; riskIndex < risks.length; riskIndex += 1) {
    const risk = pickRecord(risks[riskIndex]) ?? {};
    const riskId = safeId(risk.risk_id, `risk-${riskIndex + 1}`);
    const flow = pickRecord(risk.affected_business_flow) ?? {};
    const flowId = firstNonEmpty(flow.business_flow_id) ?? undefined;
    const detailHref = buildProjectHref(bundle.projectId, `/risks/${encodeURIComponent(riskId)}`);
    const detail = riskDetailById.get(riskId);
    const detailRecord = detail ? asRecordData(detail) : {};
    const evidence = pickRecord(detailRecord.evidence_bundle) ?? detailRecord;
    const requestSummary = pickRecord(evidence.request_summary) ?? {};
    const responseSummary = pickRecord(evidence.response_summary) ?? {};
    const discoveryPath = pickArray(evidence.discovery_path);
    const summary = firstNonEmpty(evidence.summary, risk.summary, risk.business_impact, risk.title, "风险详情待补充") ?? "风险详情待补充";
    const evidenceRefIds: string[] = [];
    const evidenceRefId = `evidence:risk:${riskId}`;
    evidenceRefs.push({
      evidenceRefId,
      label: safeText(risk.title, riskId),
      kind: "risk",
      summary,
      href: detailHref,
      routeId: "risk_evidence_detail",
      capabilityId: "risk_detail",
      findingId: riskId,
      pathId: flowId,
    });
    evidenceRefIds.push(evidenceRefId);
    if (Object.keys(requestSummary).length) {
      const requestEvidenceRefId = `evidence:request:${riskId}`;
      evidenceRefs.push({
        evidenceRefId: requestEvidenceRefId,
        label: `请求摘要 · ${safeText(risk.title, riskId)}`,
        kind: "api",
        summary: summarizeRequestSummary(requestSummary),
        href: `${detailHref}#replay`,
        routeId: "risk_evidence_detail",
        capabilityId: "risk_detail",
        findingId: riskId,
        pathId: flowId,
      });
      evidenceRefIds.push(requestEvidenceRefId);
    }
    if (Object.keys(responseSummary).length) {
      const responseEvidenceRefId = `evidence:response:${riskId}`;
      evidenceRefs.push({
        evidenceRefId: responseEvidenceRefId,
        label: `响应摘要 · ${safeText(risk.title, riskId)}`,
        kind: "risk",
        summary: summarizeResponseSummary(responseSummary),
        href: detailHref,
        routeId: "risk_evidence_detail",
        capabilityId: "risk_detail",
        findingId: riskId,
        pathId: flowId,
      });
      evidenceRefIds.push(responseEvidenceRefId);
    }
    if (discoveryPath.length) {
      const discoveryEvidenceRefId = `evidence:discovery:${riskId}`;
      evidenceRefs.push({
        evidenceRefId: discoveryEvidenceRefId,
        label: `发现路径 · ${safeText(risk.title, riskId)}`,
        kind: "run",
        summary: summarizeDiscoveryPath(discoveryPath),
        href: detailHref,
        routeId: "risk_evidence_detail",
        capabilityId: "risk_detail",
        findingId: riskId,
        pathId: flowId,
      });
      evidenceRefIds.push(discoveryEvidenceRefId);
    }

    let replayRefId: string | undefined;
    if (detail) {
      const steps = compactStrings(pickArray(evidence.reproduction_steps));
      if (steps.length) {
        replayRefId = `replay:${riskId}`;
        replayRefs.push({
          replayRefId,
          label: `复现包 · ${safeText(risk.title, riskId)}`,
          riskId,
          pathId: flowId,
          href: detailHref,
          routeId: "risk_evidence_detail",
          summary,
          updatedAt: firstNonEmpty(detailRecord.updated_at, detailRecord.generated_at) ?? undefined,
          steps,
          evidenceRefIds,
        });
      }
    }

    const finding: BehaviorFinding = {
      findingId: riskId,
      title: safeText(risk.title, riskId),
      severity: safeText(risk.severity, "unknown"),
      summary,
      businessImpact: firstNonEmpty(risk.business_impact) ?? undefined,
      launchBlocking: pickBoolean(risk.launch_blocking) === true,
      pathIds: flowId ? [flowId] : [],
      evidenceRefIds,
      replayRefId,
    };
    findingById.set(riskId, finding);
  }

  for (let index = 0; index < behaviorPaths.length; index += 1) {
    const path = behaviorPaths[index];
    const relatedFindings = Array.from(findingById.values()).filter((finding) => finding.pathIds.includes(path.pathId));
    behaviorPaths[index] = {
      ...path,
      findingIds: relatedFindings.map((finding) => finding.findingId),
      evidenceRefIds: relatedFindings.flatMap((finding) => finding.evidenceRefIds),
      replayRefIds: relatedFindings.flatMap((finding) => (finding.replayRefId ? [finding.replayRefId] : [])),
    };
  }

  for (const node of systemNodeMap.values()) {
    const relatedFindings = Array.from(findingById.values()).filter((finding) =>
      finding.pathIds.some((pathId) => node.flowIds.includes(pathId)),
    );
    systemNodeMap.set(node.nodeId, {
      ...node,
      riskCount: relatedFindings.length,
      evidenceRefIds: relatedFindings.flatMap((finding) => finding.evidenceRefIds),
      status: relatedFindings.some((finding) => finding.launchBlocking) ? "warning" : node.status,
    });
  }

  const blockerItems = pickArray(environment.current_blockers);
  for (let index = 0; index < blockerItems.length; index += 1) {
    const blocker = blockerItems[index];
    evidenceRefs.push({
      evidenceRefId: `evidence:environment:${index + 1}`,
      label: `环境阻断 ${index + 1}`,
      kind: "environment",
      summary: safeText(blocker, "环境阻断项"),
      href: buildProjectHref(bundle.projectId, "/environment"),
      routeId: "environment_diagnosis",
      capabilityId: "environment_readiness",
    });
  }

  const runId = firstNonEmpty(liveMap.run_id, commandCenter.run_id, asRecordData(bundle.testRun).run_id) ?? undefined;
  const testRun = asRecordData(bundle.testRun);
  const probeExecutions: ProbeExecution[] = [];
  if (runId) {
    const evidenceRefId = `evidence:run:${runId}`;
    evidenceRefs.push({
      evidenceRefId,
      label: `执行运行 ${runId}`,
      kind: "run",
      summary: firstNonEmpty(testRun.status, liveMap.status, "运行状态未知") ?? "运行状态未知",
      href: buildProjectHref(bundle.projectId, "/execution"),
      routeId: "execution",
      capabilityId: "test_run_detail",
    });
    probeExecutions.push({
      executionId: `probe-execution:${runId}`,
      label: "运行中的探针执行",
      runId,
      status: deriveProbeStatus(safeText(testRun.status, safeText(liveMap.status, "unknown"))),
      executionMode: firstNonEmpty(testRun.safe_execution_mode, liveMap.safe_execution_mode) ?? undefined,
      total: pickNumber(testRun.probe_total) ?? undefined,
      completed: pickNumber(testRun.probe_completed) ?? undefined,
      failed: pickNumber(testRun.probe_failed) ?? undefined,
      updatedAt: firstNonEmpty(testRun.updated_at, liveMap.updated_at) ?? undefined,
      evidenceRefIds: [evidenceRefId],
    });
  }

  const reportSummary = firstNonEmpty(executiveReport.executive_summary, executiveReport.title);
  if (reportSummary) {
    evidenceRefs.push({
      evidenceRefId: "evidence:report:executive",
      label: safeText(executiveReport.title, "领导层报告"),
      kind: "report",
      summary: reportSummary,
      href: buildProjectHref(bundle.projectId, "/reports/executive"),
      routeId: "executive_report",
      capabilityId: "report_executive",
    });
  }

  const auditRefs: BehaviorAuditRef[] = [];
  const auditPanelHref = buildBehaviorSpaceAnchorHref(bundle.projectId, "behavior-space-audit");
  const approvedBy = firstNonEmpty(businessModel.approved_by);
  const approvedAt = firstNonEmpty(businessModel.approved_at);
  if (approvedBy || approvedAt) {
    auditRefs.push({
      auditRefId: "audit:approval:business-model",
      label: "业务链路建模已审批",
      kind: "approval",
      actor: approvedBy ?? "system",
      timestamp: approvedAt ?? undefined,
      summary: "审批人和审批时间已绑定到当前行为路径建模基线，可追溯建模版本来源。",
      href: auditPanelHref,
      routeId: "behavior_space_audit",
    });
  }
  const reportGeneratedAt = firstNonEmpty(executiveReport.generated_at, executiveReport.updated_at);
  if (reportGeneratedAt) {
    auditRefs.push({
      auditRefId: "audit:export:executive-report",
      label: "领导层报告已导出",
      kind: "export",
      actor: "system",
      timestamp: reportGeneratedAt,
      summary: "导出的领导层报告已与行为空间中的风险、证据摘要和回放入口互相指向。",
      href: auditPanelHref,
      routeId: "behavior_space_audit",
    });
  }
  const snapshotTimestamp = firstNonEmpty(commandCenter.updated_at, commandCenter.generated_at);
  if (snapshotTimestamp) {
    auditRefs.push({
      auditRefId: "audit:delivery:behavior-space-snapshot",
      label: "Behavior Space 交付快照",
      kind: "delivery",
      actor: "system",
      timestamp: snapshotTimestamp,
      summary: "场景快照已汇总最新风险、证据摘要和执行状态，可作为本轮交付基线。",
      href: auditPanelHref,
      routeId: "behavior_space_audit",
    });
  }
  if (runId && firstNonEmpty(testRun.updated_at, liveMap.updated_at)) {
    auditRefs.push({
      auditRefId: `audit:execution:${runId}`,
      label: "执行时间线",
      kind: "execution",
      actor: "system",
      timestamp: firstNonEmpty(testRun.updated_at, liveMap.updated_at) ?? undefined,
      summary: "测试运行时间线已关联到行为空间，可追踪探针执行状态。",
      href: buildProjectHref(bundle.projectId, "/execution"),
      routeId: "execution",
    });
  }
  if ([approvedAt, reportGeneratedAt, snapshotTimestamp].filter(Boolean).length >= 2) {
    auditRefs.push({
      auditRefId: "audit:signoff:behavior-space-packet",
      label: "签收材料已齐备",
      kind: "signoff",
      actor: approvedBy ?? "system",
      timestamp: firstNonEmpty(reportGeneratedAt, snapshotTimestamp, approvedAt) ?? undefined,
      summary: "审批、交付快照与导出时间已汇总到行为空间，可用于客户签收前复核。",
      href: auditPanelHref,
      routeId: "behavior_space_audit",
    });
  }

  const sceneStatus = deriveSceneStatus({
    environmentStatus: valueSummary.environmentStatus.value,
    blockerCount: blockerItems.length,
    launchTitle: valueSummary.launchRecommendation.value,
    riskCount: Array.from(findingById.values()).length,
  });

  return {
    scene: {
      sceneId: `behavior-space:${bundle.projectId}`,
      projectId: bundle.projectId,
      title: `${bundle.projectId} Behavior Space`,
      summary: valueSummary.launchRecommendation.supportingText ?? "行为空间场景已聚合现有运行、风险和报告语义。",
      status: sceneStatus,
      updatedAt: firstNonEmpty(commandCenter.updated_at, executiveReport.updated_at, liveMap.updated_at) ?? undefined,
      environmentStatus: deriveSceneStatus({
        environmentStatus: valueSummary.environmentStatus.value,
        blockerCount: blockerItems.length,
        launchTitle: valueSummary.launchRecommendation.value,
        riskCount: 0,
      }),
      coverageStatus: deriveCoverageStatus({
        status: valueSummary.coverage.value,
        blockerCount: blockerItems.length,
        riskCount: Array.from(findingById.values()).length,
        rate: pickNumber(valueSummary.coverage.supportingText?.replace(/[^\d.]/g, "")),
      }),
      sourceCapabilities: [
        "command_center",
        "value_metrics",
        "business_model",
        "environment_readiness",
        "risk_list",
        "risk_detail",
        "report_executive",
        "live_map",
        "test_run_detail",
      ],
    },
    systemNodes: Array.from(systemNodeMap.values()),
    behaviorPaths,
    probeExecutions,
    findings: Array.from(findingById.values()),
    evidenceRefs,
    replayRefs,
    auditRefs,
    valueSummary,
  };
}
