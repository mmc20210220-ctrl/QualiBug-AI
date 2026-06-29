import "server-only";

import { getCommandCenterSnapshot, getEnvironmentReadiness } from "@/lib/api/command-center";
import { readAuthConfig } from "@/lib/auth/config";
import { redactUnknown } from "@/lib/redact";
import { buildEnvironmentDiagnosticsMockGraph } from "./mock";
import type {
  EnvironmentDiagnosticBlocker,
  EnvironmentDiagnosticEdge,
  EnvironmentDiagnosticGate,
  EnvironmentDiagnosticGraph,
  EnvironmentDiagnosticNode,
  EnvironmentRequiredInput,
  GateStatus,
} from "./model";

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

function containsKeyword(text: string, keywords: readonly string[]): boolean {
  const normalized = text.toLowerCase();
  return keywords.some((keyword) => normalized.includes(keyword.toLowerCase()));
}

function toStatus(value: unknown): GateStatus {
  const raw = safeText(value, "").toLowerCase();
  if (!raw) return "unknown";
  if (raw.includes("pass") || raw.includes("ready") || raw.includes("allow") || raw.includes("success")) return "passed";
  if (raw.includes("warn") || raw.includes("limited") || raw.includes("partial")) return "warning";
  if (
    raw.includes("block") ||
    raw.includes("deny") ||
    raw.includes("forbid") ||
    raw.includes("error") ||
    raw.includes("fail") ||
    raw.includes("need")
  ) {
    return "blocked";
  }
  if (raw.includes("check") || raw.includes("run") || raw.includes("pending")) return "checking";
  return "unknown";
}

function mergeStatus(...statuses: GateStatus[]): GateStatus {
  if (statuses.includes("blocked")) return "blocked";
  if (statuses.includes("warning")) return "warning";
  if (statuses.includes("checking")) return "checking";
  if (statuses.includes("passed")) return "passed";
  return "unknown";
}

function unwrapData(value: unknown): unknown {
  const record = pickRecord(value);
  if (record && "success" in record && "data" in record) return record.data;
  return value;
}

function gateStatusFromAllowed(allowed: boolean, fallback: GateStatus): GateStatus {
  if (!allowed) return fallback === "unknown" ? "blocked" : fallback;
  return fallback === "blocked" ? "warning" : fallback === "unknown" ? "passed" : fallback;
}

function inferSummaryStatus(environment: Record<string, unknown>, blockerCount: number): GateStatus {
  const readiness = pickRecord(environment.readiness_summary) ?? environment;
  const candidate = toStatus(readiness.status ?? environment.status);
  if (candidate !== "unknown") {
    if (candidate === "passed" && blockerCount > 0) return "warning";
    return candidate;
  }
  return blockerCount > 0 ? "warning" : "unknown";
}

function buildRequiredInputs(environment: Record<string, unknown>): EnvironmentRequiredInput[] {
  return pickArray(environment.required_customer_inputs).map((item, index) => {
    const record = pickRecord(item) ?? {};
    const priority = safeText(record.priority, "medium").toLowerCase();
    const status = safeText(record.status, "pending").toLowerCase();
    return {
      inputId: `input-${index + 1}`,
      title: safeText(record.title, `待补充资料 ${index + 1}`),
      priority: priority === "high" ? "high" : priority === "low" ? "low" : "medium",
      status: status === "provided" ? "provided" : status === "optional" ? "optional" : "pending",
      whyNeeded: safeText(record.why_needed, "用于完成环境接入与测试准入。"),
      suggestedInput: safeText(record.suggested_input, "请客户补充对应资料。"),
      affectedFlows: safeList(record.affected_flows),
    };
  });
}

function buildBlockers(
  base: readonly EnvironmentDiagnosticBlocker[],
  environment: Record<string, unknown>,
  requiredInputs: readonly EnvironmentRequiredInput[],
  suggestedActions: readonly string[],
  summaryStatus: GateStatus,
): EnvironmentDiagnosticBlocker[] {
  const rawBlockers = safeList(environment.current_blockers);
  const titles =
    rawBlockers.length > 0
      ? rawBlockers
      : requiredInputs.length > 0
        ? requiredInputs.map((item) => item.title)
        : base.map((item) => item.title);

  const nextActions =
    suggestedActions.length > 0
      ? suggestedActions
      : requiredInputs.length > 0
        ? requiredInputs.map((item) => item.suggestedInput)
        : base.map((item) => item.remediationAction);

  return titles.map((title, index) => {
    const fallback = base[index] ?? base[base.length - 1];
    const reason =
      requiredInputs[index]?.whyNeeded ??
      (rawBlockers[index] ? `当前阻断项会影响环境准入与后续 runtime 可信度。` : fallback.reason);
    return {
      blockerId: fallback ? fallback.blockerId : `blocker-${index + 1}`,
      title,
      summary: rawBlockers[index] ? rawBlockers[index] : fallback?.summary ?? title,
      status: summaryStatus === "passed" ? "warning" : summaryStatus,
      reason,
      remediationAction: nextActions[index] ?? fallback?.remediationAction ?? "补齐资料后重新执行环境预检。",
      nodeIds: fallback?.nodeIds ?? ["runtime"],
      gateIds: fallback?.gateIds ?? ["runtime_start_allowed"],
    };
  });
}

function buildGates(
  base: readonly EnvironmentDiagnosticGate[],
  input: {
    summaryStatus: GateStatus;
    blockerCount: number;
    safeExecutionMode: string;
    launchDecision: Record<string, unknown>;
    riskSummary: Record<string, unknown>;
  },
): EnvironmentDiagnosticGate[] {
  const safeMode = input.safeExecutionMode.toLowerCase();
  const launchTitle = safeText(input.launchDecision.title, "");
  const blockingRiskCount = pickNumber(input.riskSummary.launch_blocking) ?? pickNumber(input.riskSummary.launch_blocking_risks) ?? 0;
  const readonlyAllowed = input.summaryStatus !== "blocked" && input.summaryStatus !== "unknown";
  const runtimeAllowed = input.summaryStatus === "passed" && input.blockerCount === 0;
  const writeAllowed = runtimeAllowed && !safeMode.includes("read_only") && !safeMode.includes("readonly");
  const p0p1Allowed = input.summaryStatus !== "blocked" && blockingRiskCount === 0;

  const nextById: Record<string, Partial<EnvironmentDiagnosticGate>> = {
    runtime_start_allowed: {
      allowed: runtimeAllowed,
      status: runtimeAllowed ? "passed" : input.blockerCount > 0 ? "blocked" : input.summaryStatus,
      reason: runtimeAllowed
        ? "环境预检与阻断项均已闭环，可以进入正式 runtime。"
        : input.blockerCount > 0
          ? `当前存在 ${input.blockerCount} 个环境阻断项，正式 runtime 暂不开放。`
          : safeText(input.launchDecision.summary, "环境状态仍需进一步确认。"),
      remediationAction: runtimeAllowed ? "继续进入执行剧场或 AI 测试计划。" : "先关闭环境阻断项并重新执行预检。",
    },
    readonly_probe_allowed: {
      allowed: readonlyAllowed,
      status: gateStatusFromAllowed(readonlyAllowed, input.summaryStatus),
      reason: readonlyAllowed
        ? "建议先以只读或沙箱模式验证认证、路径和最小业务链路。"
        : "基础访问条件尚未准备好，连只读探针都不应直接运行。",
      remediationAction: readonlyAllowed ? "保持只读模式继续验证基础连通性。" : "先补齐入口、网络和认证信息。",
    },
    write_probe_allowed: {
      allowed: writeAllowed,
      status: writeAllowed ? "passed" : safeMode.includes("read") ? "blocked" : input.summaryStatus,
      reason: writeAllowed
        ? "已具备安全边界与回滚条件，可以在受控窗口执行写入探针。"
        : "当前安全模式或客户边界不支持写入型探针。",
      remediationAction: writeAllowed ? "在受控窗口执行写入探针。" : "补充回滚策略、测试租户和数据隔离说明。",
    },
    p0p1_validation_allowed: {
      allowed: p0p1Allowed,
      status: p0p1Allowed ? (input.summaryStatus === "passed" ? "passed" : "warning") : blockingRiskCount > 0 ? "blocked" : input.summaryStatus,
      reason: p0p1Allowed
        ? "高优先级问题可在当前环境继续验证。"
        : launchTitle
          ? `当前上线建议为“${launchTitle}”，高优先级验证需保持收敛。`
          : "存在上线阻断风险，不宜直接推进 P0/P1 放量验证。",
      remediationAction: p0p1Allowed ? "继续聚焦关键风险验证。" : "先关闭阻断风险，再扩大验证范围。",
    },
  };

  return base.map((gate) => ({
    ...gate,
    ...nextById[gate.gateId],
  }));
}

function resolveNodeStatus(nodeId: string, environment: Record<string, unknown>, blockers: readonly EnvironmentDiagnosticBlocker[], gates: readonly EnvironmentDiagnosticGate[], summaryStatus: GateStatus): GateStatus {
  const checks = pickRecord(environment.checks) ?? {};
  const url = pickRecord(checks.url) ?? {};
  const dns = pickRecord(checks.dns) ?? {};
  const httpCheck = pickRecord(checks.http) ?? {};
  const auth = pickRecord(checks.auth) ?? {};
  const apiSmoke = pickRecord(environment.api_smoke) ?? {};
  const requiredInputs = buildRequiredInputs(environment);

  if (nodeId === "entrypoint") return summaryStatus === "passed" ? "passed" : mergeStatus(summaryStatus, "warning");
  if (nodeId === "url") return pickBoolean(url.valid) === true ? "passed" : mergeStatus(toStatus(url.status), summaryStatus === "blocked" ? "warning" : "unknown");
  if (nodeId === "dns") return mergeStatus(toStatus(dns.status ?? dns.result), blockers.some((item) => item.blockerId === "blocker-network") ? "warning" : "unknown");
  if (nodeId === "http") return mergeStatus(toStatus(httpCheck.status), safeText(httpCheck.status_code, "") === "200" ? "passed" : "warning");
  if (nodeId === "auth") return mergeStatus(toStatus(auth.status), blockers.some((item) => item.blockerId === "blocker-auth") ? "warning" : "unknown");
  if (nodeId === "account-matrix") {
    return requiredInputs.some((item) => containsKeyword(item.title, ["账号", "角色", "权限", "auth", "sso"])) ? "blocked" : "passed";
  }
  if (nodeId === "api-smoke") {
    const passed = pickNumber(apiSmoke.passed);
    const failed = pickNumber(apiSmoke.failed);
    if (passed !== null || failed !== null) {
      if ((failed ?? 0) > 0) return "warning";
      if ((passed ?? 0) > 0) return "passed";
    }
    return mergeStatus(toStatus(apiSmoke.status), blockers.some((item) => item.blockerId === "blocker-api") ? "warning" : "unknown");
  }
  if (nodeId === "test-data") {
    return requiredInputs.some((item) => containsKeyword(`${item.title} ${item.whyNeeded}`, ["数据", "样例", "fixture", "sample"]))
      ? "warning"
      : "passed";
  }
  if (nodeId === "snapshot") {
    return safeText(environment.redaction_status ?? pickRecord(environment.readiness_summary)?.redaction_status, "safe") === "safe" ? "passed" : "warning";
  }
  if (nodeId === "cleanup") {
    const writeGate = gates.find((gate) => gate.gateId === "write_probe_allowed");
    return writeGate?.allowed ? "passed" : "warning";
  }
  if (nodeId === "runtime") return gates.find((gate) => gate.gateId === "runtime_start_allowed")?.status ?? summaryStatus;
  return summaryStatus;
}

function updateNodes(
  base: readonly EnvironmentDiagnosticNode[],
  environment: Record<string, unknown>,
  blockers: readonly EnvironmentDiagnosticBlocker[],
  gates: readonly EnvironmentDiagnosticGate[],
  summary: EnvironmentDiagnosticGraph["summary"],
): EnvironmentDiagnosticNode[] {
  const checks = pickRecord(environment.checks) ?? {};
  const url = pickRecord(checks.url) ?? {};
  const dns = pickRecord(checks.dns) ?? {};
  const httpCheck = pickRecord(checks.http) ?? {};
  const auth = pickRecord(checks.auth) ?? {};
  const apiSmoke = pickRecord(environment.api_smoke) ?? {};
  const blockerIds = new Set(blockers.map((item) => item.blockerId));

  return base.map((node) => {
    const status = resolveNodeStatus(node.nodeId, environment, blockers, gates, summary.status);
    let headline = node.headline;
    let detail = node.detail;
    let businessExplanation = node.businessExplanation;
    let nextAction = node.nextAction;

    if (node.nodeId === "url") {
      headline = safeText(url.host, node.headline);
      detail = `${safeText(url.scheme, "https")}://${safeText(url.host, "目标 Host")} : ${safeText(url.port, "443")}`;
      businessExplanation = pickBoolean(url.valid) === true ? "目标地址格式正确，可继续进入连通性验证。" : node.businessExplanation;
      nextAction = safeText(url.issue, node.nextAction);
    } else if (node.nodeId === "dns") {
      headline = `${safeText(dns.latency_ms, "—")} ms`;
      detail = safeText(dns.interpretation, node.detail);
      nextAction = safeText(dns.issue, node.nextAction);
    } else if (node.nodeId === "http") {
      headline = `HTTP ${safeText(httpCheck.status_code, "—")}`;
      detail = safeText(httpCheck.interpretation, node.detail);
    } else if (node.nodeId === "auth") {
      headline = safeText(auth.auth_type, node.headline);
      detail = safeText(auth.issue, node.detail);
      nextAction = safeText(auth.issue, node.nextAction);
    } else if (node.nodeId === "api-smoke") {
      const passed = pickNumber(apiSmoke.passed) ?? 0;
      const total = pickNumber(apiSmoke.total) ?? passed + (pickNumber(apiSmoke.failed) ?? 0);
      headline = total > 0 ? `${passed} / ${total} 通过` : node.headline;
      detail = safeText(apiSmoke.health_note, node.detail);
      nextAction = (pickNumber(apiSmoke.failed) ?? 0) > 0 ? "修复失败 API 的权限、路径或租户绑定。" : node.nextAction;
    } else if (node.nodeId === "runtime") {
      headline = summary.verdict;
      detail = summary.reason;
      businessExplanation = "Runtime 节点汇总前置门禁是否已经闭环。";
      nextAction = gates.find((gate) => gate.gateId === "runtime_start_allowed")?.remediationAction ?? node.nextAction;
    } else if (node.nodeId === "snapshot") {
      detail = summary.redactionStatus ? `脱敏状态：${summary.redactionStatus}` : node.detail;
    } else if (node.nodeId === "cleanup") {
      detail = `安全模式：${summary.safeExecutionMode}`;
    }

    return {
      ...node,
      status,
      headline,
      detail,
      businessExplanation,
      nextAction,
      blockerIds: node.blockerIds.filter((blockerId) => blockerIds.has(blockerId)),
    };
  });
}

function updateEdges(base: readonly EnvironmentDiagnosticEdge[], nodes: readonly EnvironmentDiagnosticNode[]): EnvironmentDiagnosticEdge[] {
  const statusByNodeId = new Map(nodes.map((node) => [node.nodeId, node.status]));
  return base.map((edge) => ({
    ...edge,
    status: mergeStatus(statusByNodeId.get(edge.sourceNodeId) ?? "unknown", statusByNodeId.get(edge.targetNodeId) ?? "unknown"),
  }));
}

function inferGraphSource(): EnvironmentDiagnosticGraph["source"] {
  return readAuthConfig().mode === "demo" ? "demo" : "real";
}

export async function getEnvironmentDiagnosticGraph(projectId: string): Promise<EnvironmentDiagnosticGraph> {
  const base = buildEnvironmentDiagnosticsMockGraph(projectId);
  const [commandCenterEnvelope, readinessEnvelope] = await Promise.all([
    getCommandCenterSnapshot(projectId),
    getEnvironmentReadiness(projectId),
  ]);

  const commandCenter = pickRecord(unwrapData(commandCenterEnvelope.data)) ?? pickRecord(commandCenterEnvelope.data) ?? {};
  const environment = pickRecord(unwrapData(readinessEnvelope.data)) ?? pickRecord(readinessEnvelope.data) ?? {};
  const readiness = pickRecord(environment.readiness_summary) ?? environment;
  const requiredInputs = buildRequiredInputs(environment);
  const suggestedActions = safeList(environment.suggested_actions);
  const summaryStatus = inferSummaryStatus(environment, safeList(environment.current_blockers).length || requiredInputs.length);

  const summary: EnvironmentDiagnosticGraph["summary"] = {
    status: summaryStatus,
    score: pickNumber(readiness.score),
    verdict:
      safeText(readiness.readiness_verdict, "") ||
      (summaryStatus === "passed" ? "可进入正式 runtime" : summaryStatus === "warning" ? "可进入受限测试" : "当前不建议启动 runtime"),
    reason:
      safeText(readiness.readiness_reason, "") ||
      (safeList(environment.current_blockers).length
        ? `当前存在 ${safeList(environment.current_blockers).length} 个环境阻断项。`
        : safeText(pickRecord(commandCenter.launch_decision)?.summary, "当前环境状态仍需进一步确认。")),
    safeExecutionMode: safeText(readiness.safe_execution_mode ?? environment.safe_execution_mode, "read_only"),
    checkedAt: pickString(readiness.last_checked_at ?? environment.last_checked_at) ?? undefined,
    redactionStatus: pickString(readiness.redaction_status ?? environment.redaction_status) ?? undefined,
  };

  const blockers = buildBlockers(
    base.blockers,
    environment,
    requiredInputs,
    suggestedActions.length ? suggestedActions : base.suggestedActions,
    summary.status,
  );
  const gates = buildGates(base.gates, {
    summaryStatus: summary.status,
    blockerCount: blockers.length,
    safeExecutionMode: summary.safeExecutionMode,
    launchDecision: pickRecord(commandCenter.launch_decision) ?? {},
    riskSummary: pickRecord(commandCenter.risk_summary) ?? {},
  });
  const nodes = updateNodes(base.nodes, environment, blockers, gates, summary);
  const edges = updateEdges(base.edges, nodes);

  return {
    projectId,
    source: inferGraphSource(),
    summary,
    gates,
    nodes,
    edges,
    blockers,
    requiredCustomerInputs: requiredInputs.length ? requiredInputs : base.requiredCustomerInputs,
    suggestedActions: suggestedActions.length ? suggestedActions : base.suggestedActions,
  };
}
