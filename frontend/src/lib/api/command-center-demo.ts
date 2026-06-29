function isoAt(offsetMinutes: number): string {
  return new Date(Date.UTC(2026, 5, 29, 9, 0 + offsetMinutes, 0)).toISOString();
}

function flowCatalog(projectId: string) {
  return [
    {
      business_flow_id: `${projectId}-flow-checkout`,
      name: "下单支付主链路",
      domain: "交易域",
      status: "covered",
      nodes: [
        { node_id: `${projectId}-web`, name: "Web 入口", node_type: "frontend", domain: "交易域" },
        { node_id: `${projectId}-order-service`, name: "订单服务", node_type: "service", domain: "交易域" },
        { node_id: `${projectId}-payment-api`, name: "支付网关", node_type: "external_api", domain: "交易域" },
        { node_id: `${projectId}-order-db`, name: "订单库", node_type: "database", domain: "交易域" },
      ],
    },
    {
      business_flow_id: `${projectId}-flow-refund`,
      name: "退款对账链路",
      domain: "资金域",
      status: "partial",
      nodes: [
        { node_id: `${projectId}-ops-console`, name: "运营后台", node_type: "frontend", domain: "资金域" },
        { node_id: `${projectId}-refund-service`, name: "退款服务", node_type: "service", domain: "资金域" },
        { node_id: `${projectId}-ledger-worker`, name: "对账任务", node_type: "worker", domain: "资金域" },
        { node_id: `${projectId}-ledger-db`, name: "账务库", node_type: "database", domain: "资金域" },
      ],
    },
    {
      business_flow_id: `${projectId}-flow-notify`,
      name: "通知补偿链路",
      domain: "消息域",
      status: "covered",
      nodes: [
        { node_id: `${projectId}-notify-service`, name: "通知服务", node_type: "service", domain: "消息域" },
        { node_id: `${projectId}-notify-queue`, name: "通知队列", node_type: "queue", domain: "消息域" },
        { node_id: `${projectId}-sms-gateway`, name: "短信通道", node_type: "external_api", domain: "消息域" },
      ],
    },
  ];
}

function riskCatalog(projectId: string) {
  return [
    {
      risk_id: `${projectId}-risk-double-charge`,
      title: "重复支付导致双扣风险",
      severity: "critical",
      launch_blocking: true,
      status: "open",
      summary: "支付回调与补偿任务并发时，缺少强幂等保护。",
      business_impact: "真实支付路径中可能出现重复扣款，直接影响上线。",
      affected_business_flow: {
        business_flow_id: `${projectId}-flow-checkout`,
        name: "下单支付主链路",
      },
    },
    {
      risk_id: `${projectId}-risk-refund-gap`,
      title: "退款后台状态与账务不一致",
      severity: "high",
      launch_blocking: true,
      status: "open",
      summary: "退款状态推进与账务落账不是同一提交边界。",
      business_impact: "财务对账会出现差额，需要人工干预。",
      affected_business_flow: {
        business_flow_id: `${projectId}-flow-refund`,
        name: "退款对账链路",
      },
    },
    {
      risk_id: `${projectId}-risk-notify-replay`,
      title: "通知补偿缺少回放幂等标记",
      severity: "medium",
      launch_blocking: false,
      status: "open",
      summary: "失败补偿回放路径存在重复推送窗口。",
      business_impact: "会造成重复通知和用户投诉。",
      affected_business_flow: {
        business_flow_id: `${projectId}-flow-notify`,
        name: "通知补偿链路",
      },
    },
  ];
}

function pickRisk(projectId: string, riskId: string) {
  return riskCatalog(projectId).find((item) => item.risk_id === riskId) ?? null;
}

function demoEnvelope<T>(data: T) {
  return {
    success: true,
    data,
    error: null,
  };
}

export function getDemoCommandCenterSnapshot(projectId: string) {
  return demoEnvelope({
    project_id: projectId,
    updated_at: isoAt(9),
    launch_decision: {
      title: "谨慎上线，先关闭 2 个阻断风险",
      summary: "核心支付与退款链路已建模，但仍存在真实业务路径中的上线阻断问题。",
      required_actions: ["先处理上线阻断风险", "验证支付与退款复现包", "完成一次高价值回归"],
    },
    risk_summary: {
      launch_blocking: 2,
      total: 3,
      open: 3,
    },
    business_flow_summary: {
      covered: 9,
      total: 12,
      coverage_rate: 0.75,
      status: "warning",
    },
    live_map: {
      run_id: `${projectId}-run-001`,
      status: "running",
      safe_execution_mode: "tiered",
      updated_at: isoAt(8),
    },
    recent_events: [
      { message: "支付主链路回放完成，发现重复支付风险。", timestamp: isoAt(5) },
      { message: "退款对账链路完成诊断，账务一致性待修复。", timestamp: isoAt(6) },
      { message: "通知补偿链路完成覆盖，补偿幂等需补强。", timestamp: isoAt(7) },
    ],
    value_metrics: {
      business_flow_coverage_rate: 0.75,
      estimated_hours_saved: 36,
      estimated_business_impact_min: 180000,
      estimated_business_impact_max: 420000,
      currency: "CNY",
    },
  });
}

export function getDemoValueMetrics(projectId: string) {
  void projectId;
  return demoEnvelope({
    business_flow_coverage_rate: 0.75,
    estimated_hours_saved: 36,
    estimated_business_impact_min: 180000,
    estimated_business_impact_max: 420000,
    currency: "CNY",
  });
}

export function getDemoBusinessModel(projectId: string) {
  return demoEnvelope({
    approved_by: "delivery.owner@qualibug.local",
    approved_at: isoAt(12),
    confirmed_business_flows: flowCatalog(projectId),
  });
}

export function getDemoEnvironmentReadiness(projectId: string) {
  void projectId;
  return demoEnvelope({
    status: "warning",
    current_blockers: ["支付回调幂等未锁定", "退款账务一致性校验待补齐"],
  });
}

export function getDemoRisks(
  projectId: string,
  input: { severity?: string; status?: string; launch_blocking?: boolean } = {},
) {
  const items = riskCatalog(projectId).filter((item) => {
    if (input.severity && item.severity !== input.severity) return false;
    if (input.status && item.status !== input.status) return false;
    if (typeof input.launch_blocking === "boolean" && item.launch_blocking !== input.launch_blocking) return false;
    return true;
  });
  return demoEnvelope(items);
}

export function getDemoRiskDetail(projectId: string, riskId: string) {
  const risk = pickRisk(projectId, riskId);
  if (!risk) {
    return {
      success: false,
      data: null,
      error: { message: "demo risk not found", status: 404 },
    };
  }
  return demoEnvelope({
    risk_id: riskId,
    updated_at: isoAt(14),
    evidence_bundle: {
      summary: risk.summary,
      request_summary: {
        method: "POST",
        path: risk.affected_business_flow.business_flow_id.includes("checkout") ? "/api/payments/confirm" : "/api/refunds/submit",
        key_signal: "trace 与幂等键存在冲突窗口",
      },
      response_summary: {
        status: risk.launch_blocking ? 500 : 202,
        key_signal: risk.launch_blocking ? "回调重入触发重复状态推进" : "补偿接受但缺少去重标记",
      },
      discovery_path: [
        "用户触发真实业务动作",
        "执行引擎沿建模链路注入探针",
        "在回调/补偿节点捕获状态分叉",
      ],
      reproduction_steps: [
        "打开对应业务路径的 replay pack",
        "按步骤回放请求与回调顺序",
        "观察状态推进与证据摘要中的冲突点",
      ],
    },
  });
}

export function getDemoOnboarding(projectId: string) {
  void projectId;
  return demoEnvelope({
    current_step: "evidence_review",
    steps: [
      { label: "客户系统建模", status: "completed" },
      { label: "环境诊断", status: "completed" },
      { label: "真实路径执行", status: "completed" },
      { label: "证据复核", status: "in_progress" },
      { label: "交付审计", status: "pending" },
    ],
  });
}

export function getDemoTestRun(projectId: string, runId: string) {
  return demoEnvelope({
    project_id: projectId,
    run_id: runId,
    status: "running",
    progress: 0.72,
    probe_total: 48,
    probe_completed: 35,
    probe_failed: 2,
    risk_found: 3,
    updated_at: isoAt(10),
    safe_execution_mode: "tiered",
  });
}

export function getDemoLiveMap(projectId: string) {
  return demoEnvelope({
    project_id: projectId,
    run_id: `${projectId}-run-001`,
    status: "running",
    safe_execution_mode: "tiered",
    updated_at: isoAt(8),
  });
}

export function getDemoExecutiveReport(projectId: string) {
  const risks = riskCatalog(projectId);
  return demoEnvelope({
    title: "领导层报告：上线前仍需关闭关键阻断",
    launch_recommendation: "谨慎上线，先关闭 2 个阻断风险",
    executive_summary: "客户系统已被建模，真实路径已被覆盖，关键风险已在可回放证据中暴露，当前更适合先整改再上线。",
    generated_at: isoAt(11),
    updated_at: isoAt(12),
    coverage_summary: {
      covered: 9,
      total: 12,
      coverage_rate: "75%",
    },
    risk_summary: {
      launch_blocking: 2,
    },
    top_risks: risks.slice(0, 2),
    next_actions: [
      { title: "关闭支付幂等缺口", priority: "P0", reason: "直接影响真实支付路径上线" },
      { title: "修复退款账务一致性", priority: "P0", reason: "涉及财务对账与审计" },
      { title: "完成一次补偿链路回放复核", priority: "P1", reason: "降低重复通知投诉风险" },
    ],
    evidence_trust_summary: {
      evidence_trust_score: "高",
      statement: "报告仅展示脱敏后的证据摘要，完整回放与审计入口已保留。",
    },
  });
}

export function startDemoTestRun(projectId: string) {
  return demoEnvelope({
    project_id: projectId,
    run_id: `${projectId}-run-002`,
    status: "accepted",
  });
}
