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
      suggested_action: "为支付回调与补偿任务增加统一幂等键、状态机保护和重复写入拦截。",
      evidence_score: 0.96,
      reproducibility_score: 0.93,
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
      suggested_action: "将退款状态推进与账务落账纳入同一事务边界，并为补偿任务增加幂等校验。",
      evidence_score: 0.88,
      reproducibility_score: 0.82,
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
      suggested_action: "为通知补偿引入 replay marker，并对消息消费幂等键做审计。",
      evidence_score: 0.74,
      reproducibility_score: 0.78,
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

function runtimeRecentEvents(projectId: string) {
  const [paymentRisk, refundRisk] = riskCatalog(projectId);

  return [
    {
      event_id: `${projectId}-runtime-started`,
      kind: "lifecycle",
      status: "completed",
      title: "受控 runtime 已建立",
      message: "执行上下文、safe mode 和基线快照已经装配完成。",
      timestamp: isoAt(2),
      node_id: "stage-entry",
      edge_id: "edge-entry-probe",
      phase_label: "setup",
      evidence: {
        summary: "本轮执行以 tiered 模式运行，先沿支付与退款主链路派发高价值探针。",
        sections: [
          {
            title: "执行边界",
            items: ["safe mode = tiered", "先执行支付与退款主链路", "敏感字段继续使用脱敏输出"],
          },
        ],
      },
    },
    {
      event_id: `${projectId}-probe-checkout`,
      kind: "probe",
      status: "completed",
      title: "支付主链路探针回放完成",
      message: "订单、支付网关和回调节点已被串联命中，准备落请求与快照证据。",
      timestamp: isoAt(4),
      node_id: "stage-probe",
      edge_id: "edge-probe-traffic",
      phase_label: "probe_path",
      next_action: "继续检查支付回调与补偿任务在请求响应层的状态分叉。",
      evidence: {
        summary: "支付链路的核心节点均已命中，覆盖订单服务、支付网关和回调路径。",
        discovery_path: ["Web 入口触发支付", "订单服务装配回调", "支付网关注入异步结果"],
      },
    },
    {
      event_id: `${projectId}-traffic-refund-403`,
      kind: "request",
      status: "blocked",
      title: "退款链路出现权限阻断",
      message: "退款提交接口返回 403，当前测试账号仍缺少租户或权限绑定。",
      timestamp: isoAt(6),
      node_id: "stage-traffic",
      edge_id: "edge-traffic-snapshot",
      phase_label: "request_response",
      next_action: "补齐退款链路账号权限后重新执行 probe，并复核租户绑定。",
      request: {
        method: "POST",
        path: "/api/refunds/submit",
        key_signal: "租户绑定缺失，无法进入正式退款执行",
      },
      response: {
        status_code: 403,
        key_signal: "permission_denied",
      },
      evidence: {
        summary: "运行时真实请求已触发，但由于账号权限缺口，退款链路仍停留在受限验证阶段。",
        reason: "环境阻断与业务 Bug 需要区分处理，当前更像权限或租户边界问题。",
        remediation_action: "先补齐退款测试账号矩阵，再回放同一条 API 路径确认阻断是否解除。",
      },
    },
    {
      event_id: `${projectId}-snapshot-payment-divergence`,
      kind: "snapshot",
      status: "warning",
      title: "支付前后态出现幂等分叉",
      message: "before / after snapshot 显示支付回调与补偿任务都尝试推进订单状态。",
      timestamp: isoAt(7),
      node_id: "stage-snapshot",
      edge_id: "edge-snapshot-finding",
      phase_label: "snapshot_diff",
      evidence: {
        summary: "快照差异显示订单状态被两个 actor 并发推进，是重复支付风险的直接证据。",
        discovery_path: ["捕获回调前订单状态", "并发触发补偿任务", "after snapshot 观察到重复推进"],
        reproduction_steps: ["回放支付确认请求", "并发触发补偿任务", "检查订单状态推进是否出现双写"],
      },
    },
    {
      event_id: `${projectId}-finding-payment-risk`,
      kind: "finding",
      status: "warning",
      title: paymentRisk.title,
      message: "支付回调与补偿任务并发时缺少强幂等保护，已落入风险证据链。",
      timestamp: isoAt(8),
      node_id: "stage-finding",
      edge_id: "edge-finding-summary",
      phase_label: "finding_drop",
      finding: {
        risk_id: paymentRisk.risk_id,
      },
      next_action: "优先处理该上线阻断风险，并在修复后执行支付主链路回归。",
      evidence: {
        summary: paymentRisk.summary,
        reason: paymentRisk.business_impact,
        remediation_action: "为支付回调与补偿任务补齐强幂等键和状态推进保护。",
        links: [{ label: "支付风险详情", href: `/projects/${projectId}/risks/${paymentRisk.risk_id}`, kind: "page" }],
      },
    },
    {
      event_id: `${projectId}-runtime-summary`,
      kind: "summary",
      status: "completed",
      title: "本轮执行已归档为可交付总结",
      message: `已产出 ${riskCatalog(projectId).length} 个风险摘要，其中 ${refundRisk.launch_blocking ? "含阻断项" : "暂无阻断项"}。`,
      timestamp: isoAt(9),
      node_id: "stage-summary",
      edge_id: "edge-finding-summary",
      phase_label: "summary",
      evidence: {
        summary: "运行完成后已生成 summary card，可继续下钻风险、ROI 和报告页面。",
        sections: [
          {
            title: "下一步",
            items: ["先关闭上线阻断项", "复核 Artifact 与风险详情", "将结论同步到领导层报告"],
          },
        ],
      },
    },
  ];
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
      status: "completed_with_findings",
      safe_execution_mode: "tiered",
      updated_at: isoAt(8),
    },
    recent_events: runtimeRecentEvents(projectId),
    value_metrics: {
      business_flow_coverage_rate: 0.75,
      estimated_hours_saved: 36,
      estimated_business_impact_min: 180000,
      estimated_business_impact_max: 420000,
      currency: "CNY",
      customer_ready_finding_count: 2,
      deliverable_evidence_pack_count: 3,
      required_customer_input_count: 2,
      redaction_status: "safe",
      calculation_notes: [
        "customer-ready finding 仅统计已具备脱敏摘要、复现步骤与关闭准则的 finding。",
        "证据包数量表示可直接交付给研发或客户复核的 replay / evidence bundle。",
        "不展示未经 benchmark 证明的发现率、召回率或夸大性收益表述。",
      ],
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
    customer_ready_finding_count: 2,
    deliverable_evidence_pack_count: 3,
    required_customer_input_count: 2,
    redaction_status: "safe",
    calculation_notes: [
      "customer-ready finding 仅统计已具备脱敏摘要、复现步骤与关闭准则的 finding。",
      "证据包数量表示可直接交付给研发或客户复核的 replay / evidence bundle。",
      "不展示未经 benchmark 证明的发现率、召回率或夸大性收益表述。",
    ],
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
  return demoEnvelope({
    status: "warning",
    score: 68,
    last_checked_at: isoAt(4),
    safe_execution_mode: "read_only",
    redaction_status: "safe",
    readiness_verdict: "可进入受限测试",
    readiness_reason: "基础连通性已经具备，但账号矩阵、API 权限和测试数据仍需客户补料。",
    current_blockers: ["支付回调幂等未锁定", "退款账务一致性校验待补齐"],
    suggested_actions: [
      "补齐测试账号矩阵并确认 SSO / MFA 测试绕过方式。",
      "修复失败 API 的权限或租户绑定后重新执行预检。",
      "在只读模式下继续推进 API smoke，再决定是否开启写入探针。",
    ],
    required_customer_inputs: [
      {
        title: "测试账号矩阵",
        priority: "high",
        status: "pending",
        why_needed: "用于覆盖支付、退款、运营等关键业务角色。",
        suggested_input: "至少提供管理员、运营、财务各 1 组测试账号。",
        affected_flows: ["下单支付主链路", "退款对账链路"],
      },
      {
        title: "最小业务样例",
        priority: "medium",
        status: "pending",
        why_needed: "用于完成支付、退款关键链路的 API smoke 与回放。",
        suggested_input: "提供 1 组支付 / 退款最小可复现样例。",
        affected_flows: ["下单支付主链路", "退款对账链路"],
      },
    ],
    checks: {
      url: {
        status: "passed",
        valid: true,
        scheme: "https",
        host: `${projectId}.qualibug.demo`,
        port: 443,
        issue: "地址结构已确认。",
      },
      dns: {
        status: "warning",
        latency_ms: 93,
        interpretation: "测试环境可达，但仍需确认白名单与代理路径。",
        issue: "补充 VPN / 代理 / 白名单策略。",
      },
      http: {
        status: "warning",
        status_code: 200,
        interpretation: "HTTP 可达，证书和网关策略仍需客户确认。",
        reachable: true,
      },
      auth: {
        status: "warning",
        auth_type: "OIDC + Session Cookie",
        issue: "测试账号和 MFA 绕过方式尚未完全补齐。",
      },
    },
    api_smoke: {
      status: "warning",
      passed: 2,
      failed: 1,
      total: 3,
      health_note: "最小链路可进入 smoke，但退款接口仍有权限或租户绑定问题。",
      items: [
        { method: "POST", path: "/api/payments/confirm", result: "passed", status_code: 200, affected_flow: "下单支付主链路" },
        { method: "POST", path: "/api/refunds/submit", result: "warning", status_code: 403, affected_flow: "退款对账链路" },
        { method: "GET", path: "/api/orders/detail", result: "passed", status_code: 200, affected_flow: "下单支付主链路" },
      ],
    },
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
    risk,
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
      suggested_fix: risk.suggested_action,
      closure_criteria: [
        "同一业务输入仅允许一次状态推进",
        "回调与补偿任务共享幂等键并落审计日志",
        "修复后重新执行该 replay timeline 并确认失败点消失",
      ],
      replay_timeline: [
        {
          step_id: `${riskId}-entry`,
          kind: "entry",
          title: "装载单条 Finding 证据包",
          summary: "风险详情已切换到可回放模式，并绑定当前业务链路的关键证据。",
          status: "completed",
          timestamp: isoAt(10),
          cue: "Replay 入口已建立",
          fields: [
            { label: "风险", value: risk.title },
            { label: "业务链路", value: risk.affected_business_flow.name },
          ],
        },
        {
          step_id: `${riskId}-discovery`,
          kind: "discovery",
          title: "沿业务链路命中关键探针",
          summary: "执行引擎命中请求、回调与补偿节点，形成基础 replay path。",
          status: "completed",
          timestamp: isoAt(11),
          cue: "链路已可回放",
          fields: [{ label: "发现路径", value: "真实入口 -> 服务节点 -> 回调 / 补偿" }],
        },
        {
          step_id: `${riskId}-request`,
          kind: "request",
          title: "发送关键业务请求",
          summary: `${risk.affected_business_flow.name} 发起关键写请求，等待回调与补偿结果汇合。`,
          status: "completed",
          timestamp: isoAt(12),
          cue: "请求已进入 replay 队列",
          fields: [{ label: "请求", value: `${risk.affected_business_flow.business_flow_id.includes("checkout") ? "POST /api/payments/confirm" : "POST /api/refunds/submit"}` }],
        },
        {
          step_id: `${riskId}-response`,
          kind: "response",
          title: "观察请求返回",
          summary: risk.launch_blocking ? "接口返回失败信号，说明异常已经暴露到可观察层。" : "接口接受请求，但后续补偿路径仍可能重复执行。",
          status: risk.launch_blocking ? "failed" : "warning",
          timestamp: isoAt(13),
          cue: risk.launch_blocking ? "HTTP 500 / 状态推进冲突" : "HTTP 202 / 去重标记缺失",
          fields: [{ label: "响应", value: `${risk.launch_blocking ? "HTTP 500" : "HTTP 202"}` }],
          failure_point: true,
        },
        {
          step_id: `${riskId}-snapshot`,
          kind: "snapshot",
          title: "比对 before / after 快照",
          summary: risk.launch_blocking ? "快照显示回调和补偿任务都推进了同一订单状态，是重复扣款的直接信号。" : "快照显示补偿回放缺少 replay marker，存在重复触发窗口。",
          status: "warning",
          timestamp: isoAt(14),
          cue: "状态差异已落入证据链",
          fields: [{ label: "关键观察", value: risk.launch_blocking ? "同一订单被双重推进" : "补偿消息缺少幂等标记" }],
          failure_point: true,
        },
        {
          step_id: `${riskId}-finding`,
          kind: "finding",
          title: "沉淀为风险结论",
          summary: risk.business_impact,
          status: risk.launch_blocking ? "failed" : "warning",
          timestamp: isoAt(15),
          cue: risk.suggested_action,
          fields: [
            { label: "上线阻断", value: risk.launch_blocking ? "是" : "否" },
            { label: "建议动作", value: risk.suggested_action },
          ],
          failure_point: true,
        },
      ],
      failure_points: [
        {
          step_id: `${riskId}-response`,
          label: "响应异常",
          reason: risk.launch_blocking ? "请求已在返回层暴露失败信号。" : "返回接受但缺少去重保护，需继续复核。",
        },
        {
          step_id: `${riskId}-snapshot`,
          label: "状态分叉",
          reason: "快照差异是当前风险成立的核心证据。",
        },
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
    status: "completed_with_findings",
    progress: 1,
    probe_total: 48,
    probe_completed: 48,
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
    status: "completed_with_findings",
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
