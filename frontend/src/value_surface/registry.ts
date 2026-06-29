import { routes } from "./routes";
import type { BackendCapability, PageValueDefinition, ValueSurfaceRegistry } from "./types";

export const backendCapabilities: readonly BackendCapability[] = [
  {
    id: "runtime_health",
    label: "可信健康检查（真实在线/离线/未验证）",
    endpoints: [{ method: "GET", pathTemplate: "/api/v1/health" }],
    producedFields: ["data.status", "data.version", "data.redaction_status"],
  },
  {
    id: "industry_templates",
    label: "行业模板清单",
    endpoints: [{ method: "GET", pathTemplate: "/api/v1/industry-templates" }],
  },
  {
    id: "projects",
    label: "项目列表/创建项目",
    endpoints: [
      { method: "GET", pathTemplate: "/api/v1/projects" },
      { method: "POST", pathTemplate: "/api/v1/projects" },
    ],
  },
  {
    id: "project_detail",
    label: "项目详情（基础信息）",
    endpoints: [{ method: "GET", pathTemplate: "/api/v1/projects/{projectId}" }],
  },
  {
    id: "onboarding",
    label: "项目 Onboarding/下一步动作",
    endpoints: [{ method: "GET", pathTemplate: "/api/v1/projects/{projectId}/onboarding" }],
    producedFields: ["data.current_step", "data.steps", "data.completion_rate"],
  },
  {
    id: "business_model",
    label: "业务链路建模（可编辑）",
    endpoints: [
      { method: "GET", pathTemplate: "/api/v1/projects/{projectId}/business-model" },
      { method: "PATCH", pathTemplate: "/api/v1/projects/{projectId}/business-model" },
      { method: "POST", pathTemplate: "/api/v1/projects/{projectId}/business-model/apply-template" },
    ],
  },
  {
    id: "environment_readiness",
    label: "环境诊断与就绪度（只读）",
    endpoints: [{ method: "GET", pathTemplate: "/api/v1/projects/{projectId}/environment/readiness" }],
    producedFields: ["data.status", "data.score", "data.current_blockers"],
  },
  {
    id: "test_plan",
    label: "AI 测试计划（只读）",
    endpoints: [{ method: "GET", pathTemplate: "/api/v1/projects/{projectId}/test-plan" }],
  },
  {
    id: "test_plan_generate",
    label: "生成 AI 测试计划（长链路动作）",
    endpoints: [{ method: "POST", pathTemplate: "/api/v1/projects/{projectId}/test-plan/generate" }],
  },
  {
    id: "test_run_start",
    label: "启动测试运行（runs/jobs）",
    endpoints: [{ method: "POST", pathTemplate: "/api/v1/projects/{projectId}/test-runs" }],
  },
  {
    id: "test_run_detail",
    label: "测试运行详情（阶段/失败原因/可重试）",
    endpoints: [{ method: "GET", pathTemplate: "/api/v1/projects/{projectId}/test-runs/{runId}" }],
  },
  {
    id: "command_center",
    label: "质量驾驶舱快照（管理者一屏决策）",
    endpoints: [{ method: "GET", pathTemplate: "/api/v1/projects/{projectId}/command-center" }],
    producedFields: [
      "data.launch_decision",
      "data.risk_summary",
      "data.value_metrics",
      "data.business_flow_summary",
      "data.executive_summary",
    ],
  },
  {
    id: "live_map",
    label: "实时执行地图/事件流（只读）",
    endpoints: [{ method: "GET", pathTemplate: "/api/v1/projects/{projectId}/live-map" }],
  },
  {
    id: "risk_list",
    label: "风险列表（可筛选）",
    endpoints: [{ method: "GET", pathTemplate: "/api/v1/projects/{projectId}/risks" }],
  },
  {
    id: "risk_detail",
    label: "风险详情 + 证据包（可下钻）",
    endpoints: [{ method: "GET", pathTemplate: "/api/v1/projects/{projectId}/risks/{riskId}" }],
  },
  {
    id: "value_metrics",
    label: "价值指标（节省工时/阻断风险/覆盖/可信度）",
    endpoints: [{ method: "GET", pathTemplate: "/api/v1/projects/{projectId}/value-metrics" }],
    producedFields: [
      "data.estimated_hours_saved",
      "data.launch_blocking_risks",
      "data.business_flow_coverage_rate",
      "data.evidence_trust_score",
      "data.estimated_business_impact_min",
      "data.estimated_business_impact_max",
    ],
  },
  {
    id: "report_generate",
    label: "生成领导层报告（长链路动作）",
    endpoints: [{ method: "POST", pathTemplate: "/api/v1/projects/{projectId}/reports/generate" }],
  },
  {
    id: "report_executive",
    label: "领导层报告（只读）",
    endpoints: [{ method: "GET", pathTemplate: "/api/v1/projects/{projectId}/reports/executive" }],
  },
] as const;

export const pages: readonly PageValueDefinition[] = [
  {
    pageId: "login",
    routeIds: ["login"],
    capabilities: ["runtime_health"],
    metrics: [],
    evidenceEntrypoints: [],
    acceptance: [
      {
        acceptanceId: "login_offline_visible",
        field: "evidence_entry",
        statement: "登录页必须展示“后端状态/未验证/离线”的可观测提示，不允许把仅配置视为在线。",
        must: true,
        drilldowns: [{ kind: "api", label: "健康检查", capabilityId: "runtime_health" }],
      },
    ],
  },
  {
    pageId: "project_list",
    routeIds: ["project_list"],
    capabilities: ["runtime_health", "projects"],
    metrics: [
      {
        metricId: "project_count",
        field: "next_actions",
        label: "项目数量（用于判断是否可进入工作区）",
        capabilityId: "projects",
        jsonPath: "data.length",
        drilldowns: [{ kind: "route", label: "进入项目工作区", routeId: "project_workspace" }],
      },
    ],
    evidenceEntrypoints: [
      {
        evidenceId: "health_badge",
        field: "evidence_entry",
        label: "可信健康检查入口",
        kind: "api",
        capabilityId: "runtime_health",
      },
    ],
    acceptance: [
      {
        acceptanceId: "project_list_decision_ready",
        field: "next_actions",
        statement: "项目列表必须支持“选择当前项目 → 进入项目工作区”的连续动作，不要求用户理解后端路径。",
        must: true,
        drilldowns: [{ kind: "route", label: "项目工作区", routeId: "project_workspace" }],
      },
      {
        acceptanceId: "project_list_state_observable",
        field: "evidence_entry",
        statement: "项目列表必须有统一加载/失败/离线/空态，并显示真实后端状态来源（demo/real/unverified）。",
        must: true,
        drilldowns: [{ kind: "api", label: "健康检查", capabilityId: "runtime_health" }],
      },
    ],
  },
  {
    pageId: "project_workspace",
    routeIds: ["project_workspace"],
    capabilities: ["project_detail", "onboarding", "value_metrics"],
    metrics: [
      {
        metricId: "next_step",
        field: "next_actions",
        label: "下一步动作（Onboarding current_step）",
        capabilityId: "onboarding",
        jsonPath: "data.current_step",
        drilldowns: [
          { kind: "route", label: "业务流程地图", routeId: "business_flow_map" },
          { kind: "route", label: "环境诊断", routeId: "environment_diagnosis" },
          { kind: "route", label: "AI 测试计划", routeId: "test_plan" },
          { kind: "route", label: "执行与任务生命周期", routeId: "execution" },
        ],
      },
      {
        metricId: "hours_saved",
        field: "estimated_hours_saved",
        label: "预计节省工时（小时）",
        unit: "h",
        capabilityId: "value_metrics",
        jsonPath: "data.estimated_hours_saved",
        drilldowns: [{ kind: "route", label: "ROI/价值", routeId: "roi_value" }],
      },
      {
        metricId: "launch_blockers",
        field: "launch_blockers",
        label: "上线阻断风险数",
        unit: "个",
        capabilityId: "value_metrics",
        jsonPath: "data.launch_blocking_risks",
        drilldowns: [{ kind: "route", label: "下钻到风险证据链", routeId: "risk_evidence_list" }],
      },
    ],
    evidenceEntrypoints: [
      {
        evidenceId: "workspace_risk_entry",
        field: "evidence_entry",
        label: "风险证据链入口",
        kind: "risk",
        routeId: "risk_evidence_list",
      },
      {
        evidenceId: "workspace_report_entry",
        field: "evidence_entry",
        label: "领导层报告入口",
        kind: "report",
        routeId: "executive_report",
      },
    ],
    acceptance: [
      {
        acceptanceId: "workspace_value_header",
        field: "launch_recommendation",
        statement: "项目工作区页必须在标题区展示：上线建议、阻断风险数、节省工时，且三者可下钻到证据链。",
        must: true,
        drilldowns: [
          { kind: "route", label: "质量驾驶舱", routeId: "command_center" },
          { kind: "route", label: "风险证据链", routeId: "risk_evidence_list" },
          { kind: "route", label: "ROI/价值", routeId: "roi_value" },
        ],
      },
      {
        acceptanceId: "workspace_next_actions",
        field: "next_actions",
        statement: "项目工作区必须把 onboarding steps 翻译为“下一步动作清单”，每一步都能直接跳转到对应页面。",
        must: true,
        drilldowns: [
          { kind: "route", label: "业务流程地图", routeId: "business_flow_map" },
          { kind: "route", label: "环境诊断", routeId: "environment_diagnosis" },
          { kind: "route", label: "AI 测试计划", routeId: "test_plan" },
          { kind: "route", label: "执行", routeId: "execution" },
        ],
      },
    ],
  },
  {
    pageId: "command_center",
    routeIds: ["command_center"],
    capabilities: ["command_center", "value_metrics", "risk_list", "environment_readiness", "onboarding"],
    metrics: [
      {
        metricId: "launch_title",
        field: "launch_recommendation",
        label: "上线建议（标题）",
        capabilityId: "command_center",
        jsonPath: "data.launch_decision.title",
        drilldowns: [
          { kind: "route", label: "环境诊断", routeId: "environment_diagnosis" },
          { kind: "route", label: "风险证据链", routeId: "risk_evidence_list" },
        ],
      },
      {
        metricId: "launch_blocking_risks",
        field: "launch_blockers",
        label: "上线阻断风险数",
        unit: "个",
        capabilityId: "value_metrics",
        jsonPath: "data.launch_blocking_risks",
        drilldowns: [{ kind: "route", label: "下钻到阻断风险列表", routeId: "risk_evidence_list" }],
      },
      {
        metricId: "estimated_hours_saved",
        field: "estimated_hours_saved",
        label: "预计节省工时",
        unit: "h",
        capabilityId: "value_metrics",
        jsonPath: "data.estimated_hours_saved",
        drilldowns: [{ kind: "route", label: "AI 测试计划", routeId: "test_plan" }],
      },
      {
        metricId: "business_flow_coverage_rate",
        field: "coverage",
        label: "业务链路覆盖率",
        unit: "%",
        capabilityId: "command_center",
        jsonPath: "data.business_flow_summary.coverage_rate",
        drilldowns: [{ kind: "route", label: "能力中心/覆盖度", routeId: "capability_center" }],
      },
      {
        metricId: "next_step",
        field: "next_actions",
        label: "下一步动作（Onboarding）",
        capabilityId: "onboarding",
        jsonPath: "data.current_step",
        drilldowns: [
          { kind: "route", label: "业务流程地图", routeId: "business_flow_map" },
          { kind: "route", label: "环境诊断", routeId: "environment_diagnosis" },
          { kind: "route", label: "AI 测试计划", routeId: "test_plan" },
          { kind: "route", label: "执行与任务生命周期", routeId: "execution" },
        ],
      },
    ],
    evidenceEntrypoints: [
      {
        evidenceId: "cc_blocking_risk_drilldown",
        field: "launch_blockers",
        label: "阻断风险证据链",
        kind: "risk",
        routeId: "risk_evidence_list",
      },
      {
        evidenceId: "cc_env_blockers",
        field: "evidence_entry",
        label: "环境阻断项",
        kind: "api",
        capabilityId: "environment_readiness",
      },
      {
        evidenceId: "cc_report_entry",
        field: "evidence_entry",
        label: "领导层报告",
        kind: "report",
        routeId: "executive_report",
      },
    ],
    acceptance: [
      {
        acceptanceId: "cc_one_screen_decision",
        field: "launch_recommendation",
        statement: "质量驾驶舱必须一屏回答：是否可上线（明确建议+原因摘要）。",
        must: true,
        drilldowns: [
          { kind: "route", label: "风险证据链", routeId: "risk_evidence_list" },
          { kind: "route", label: "环境诊断", routeId: "environment_diagnosis" },
        ],
      },
      {
        acceptanceId: "cc_blockers_drillable",
        field: "launch_blockers",
        statement:
          "阻断风险必须给出数量与一键下钻入口，默认下钻到“上线阻断”过滤视图（由前端实现筛选参数）。",
        must: true,
        drilldowns: [{ kind: "route", label: "风险证据链", routeId: "risk_evidence_list", params: { projectId: "{projectId}" } }],
      },
      {
        acceptanceId: "cc_hours_saved_trustworthy",
        field: "estimated_hours_saved",
        statement: "节省工时必须展示估算口径入口（计算说明/单位/时间点），不可只展示一个数字。",
        must: true,
        drilldowns: [{ kind: "api", label: "价值指标", capabilityId: "value_metrics" }],
      },
      {
        acceptanceId: "cc_next_actions_actionable",
        field: "next_actions",
        statement: "下一步动作必须是可点击的动作清单，点击后进入对应页面并保留项目上下文。",
        must: true,
        drilldowns: [
          { kind: "route", label: "业务流程地图", routeId: "business_flow_map" },
          { kind: "route", label: "环境诊断", routeId: "environment_diagnosis" },
          { kind: "route", label: "AI 测试计划", routeId: "test_plan" },
          { kind: "route", label: "执行", routeId: "execution" },
        ],
      },
    ],
  },
  {
    pageId: "capability_center",
    routeIds: ["capability_center"],
    capabilities: ["command_center", "business_model", "value_metrics", "risk_list"],
    metrics: [
      {
        metricId: "coverage_rate",
        field: "coverage",
        label: "覆盖率（按业务链路）",
        unit: "%",
        capabilityId: "value_metrics",
        jsonPath: "data.business_flow_coverage_rate",
        drilldowns: [{ kind: "route", label: "业务流程地图", routeId: "business_flow_map" }],
      },
      {
        metricId: "covered_total",
        field: "coverage",
        label: "已覆盖/总链路",
        capabilityId: "command_center",
        jsonPath: "data.business_flow_summary.covered",
        drilldowns: [{ kind: "route", label: "业务流程地图", routeId: "business_flow_map" }],
      },
      {
        metricId: "blocked_flows",
        field: "launch_blockers",
        label: "阻断链路/阻断风险入口",
        capabilityId: "command_center",
        jsonPath: "data.business_flow_summary.blocked",
        drilldowns: [{ kind: "route", label: "风险证据链", routeId: "risk_evidence_list" }],
      },
    ],
    evidenceEntrypoints: [
      {
        evidenceId: "cap_business_model",
        field: "evidence_entry",
        label: "业务链路证据入口（模型/节点/覆盖状态）",
        kind: "api",
        capabilityId: "business_model",
      },
      {
        evidenceId: "cap_risk_entry",
        field: "evidence_entry",
        label: "风险证据链",
        kind: "risk",
        routeId: "risk_evidence_list",
      },
    ],
    acceptance: [
      {
        acceptanceId: "cap_explainable_coverage",
        field: "coverage",
        statement: "能力中心必须把“覆盖”解释为业务链路维度，并能下钻到具体链路/节点状态，而不是展示抽象百分比。",
        must: true,
        drilldowns: [{ kind: "route", label: "业务流程地图", routeId: "business_flow_map" }],
      },
      {
        acceptanceId: "cap_gap_action",
        field: "next_actions",
        statement:
          "能力中心必须给出覆盖缺口的下一步动作（例如：启用关键链路/补充角色/补充环境信息），并能直接跳转到执行入口。",
        must: true,
        drilldowns: [
          { kind: "route", label: "业务流程地图", routeId: "business_flow_map" },
          { kind: "route", label: "环境诊断", routeId: "environment_diagnosis" },
          { kind: "route", label: "AI 测试计划", routeId: "test_plan" },
        ],
      },
    ],
  },
  {
    pageId: "business_flow_map",
    routeIds: ["business_flow_map"],
    capabilities: ["business_model", "risk_list"],
    metrics: [
      {
        metricId: "flow_total",
        field: "coverage",
        label: "总业务链路数",
        unit: "条",
        capabilityId: "business_model",
        jsonPath: "data.confirmed_business_flows.length",
        drilldowns: [{ kind: "route", label: "能力中心/覆盖度", routeId: "capability_center" }],
      },
    ],
    evidenceEntrypoints: [
      {
        evidenceId: "flow_risk_entry",
        field: "evidence_entry",
        label: "链路关联风险列表（按 business_flow_id 筛选）",
        kind: "risk",
        routeId: "risk_evidence_list",
      },
    ],
    acceptance: [
      {
        acceptanceId: "flow_map_business_language",
        field: "coverage",
        statement:
          "业务流程地图必须以业务语言展示链路与节点，而不是技术字段堆叠；每条链路都能标记覆盖/风险/阻断状态。",
        must: true,
        drilldowns: [{ kind: "route", label: "风险证据链", routeId: "risk_evidence_list" }],
      },
      {
        acceptanceId: "flow_map_drilldown_risk",
        field: "evidence_entry",
        statement: "从任意链路/节点必须能一键下钻到关联风险与证据链。",
        must: true,
        drilldowns: [{ kind: "route", label: "风险证据链", routeId: "risk_evidence_list" }],
      },
    ],
  },
  {
    pageId: "environment_diagnosis",
    routeIds: ["environment_diagnosis"],
    capabilities: ["environment_readiness"],
    metrics: [
      {
        metricId: "env_status",
        field: "launch_recommendation",
        label: "环境就绪状态",
        capabilityId: "environment_readiness",
        jsonPath: "data.status",
        drilldowns: [{ kind: "route", label: "质量驾驶舱", routeId: "command_center" }],
      },
      {
        metricId: "env_blockers",
        field: "launch_blockers",
        label: "环境阻断项数量",
        unit: "项",
        capabilityId: "environment_readiness",
        jsonPath: "data.current_blockers.length",
      },
    ],
    evidenceEntrypoints: [
      {
        evidenceId: "env_blocker_list",
        field: "evidence_entry",
        label: "环境阻断项（详情/建议）",
        kind: "api",
        capabilityId: "environment_readiness",
      },
    ],
    acceptance: [
      {
        acceptanceId: "env_readiness_decision",
        field: "launch_recommendation",
        statement: "环境诊断必须明确告诉用户：当前环境是否允许安全执行，以及阻断项来自哪里。",
        must: true,
        drilldowns: [{ kind: "api", label: "环境就绪度", capabilityId: "environment_readiness" }],
      },
      {
        acceptanceId: "env_actionable_blockers",
        field: "next_actions",
        statement: "每个环境阻断项必须包含“可执行的下一步动作”（补充信息/修复配置/切换安全模式），不可只报错。",
        must: true,
        drilldowns: [{ kind: "api", label: "环境就绪度", capabilityId: "environment_readiness" }],
      },
    ],
  },
  {
    pageId: "test_plan",
    routeIds: ["test_plan"],
    capabilities: ["test_plan", "test_plan_generate", "value_metrics", "business_model"],
    metrics: [
      {
        metricId: "ai_equivalent_test_points",
        field: "estimated_hours_saved",
        label: "AI 等价测试点",
        unit: "点",
        capabilityId: "value_metrics",
        jsonPath: "data.ai_equivalent_test_points",
        drilldowns: [{ kind: "api", label: "价值指标", capabilityId: "value_metrics" }],
      },
      {
        metricId: "estimated_hours_saved",
        field: "estimated_hours_saved",
        label: "预计节省工时（小时）",
        unit: "h",
        capabilityId: "value_metrics",
        jsonPath: "data.estimated_hours_saved",
        drilldowns: [{ kind: "api", label: "价值指标", capabilityId: "value_metrics" }],
      },
      {
        metricId: "plan_coverage_rate",
        field: "coverage",
        label: "计划覆盖率（业务链路）",
        unit: "%",
        capabilityId: "value_metrics",
        jsonPath: "data.business_flow_coverage_rate",
        drilldowns: [{ kind: "route", label: "业务流程地图", routeId: "business_flow_map" }],
      },
    ],
    evidenceEntrypoints: [
      {
        evidenceId: "plan_source",
        field: "evidence_entry",
        label: "测试计划来源与结构（只读合同）",
        kind: "api",
        capabilityId: "test_plan",
      },
    ],
    acceptance: [
      {
        acceptanceId: "plan_value_first",
        field: "estimated_hours_saved",
        statement: "测试计划页必须先展示价值信号（节省工时/覆盖范围/风险关注点），再展示技术细节。",
        must: true,
        drilldowns: [{ kind: "route", label: "ROI/价值", routeId: "roi_value" }],
      },
      {
        acceptanceId: "plan_generate_observable",
        field: "next_actions",
        statement: "生成测试计划属于长链路动作，必须有可观测进度、失败原因与可重试策略。",
        must: true,
        drilldowns: [{ kind: "api", label: "生成测试计划", capabilityId: "test_plan_generate" }],
      },
    ],
  },
  {
    pageId: "execution",
    routeIds: ["execution", "live_map"],
    capabilities: ["test_run_start", "test_run_detail", "live_map", "risk_list"],
    metrics: [
      {
        metricId: "execution_events",
        field: "evidence_entry",
        label: "事件流/实时状态（最近事件）",
        capabilityId: "live_map",
        jsonPath: "data.events",
        drilldowns: [{ kind: "route", label: "实时执行地图", routeId: "live_map" }],
      },
    ],
    evidenceEntrypoints: [
      {
        evidenceId: "run_detail",
        field: "evidence_entry",
        label: "运行详情（阶段/失败原因/可重试）",
        kind: "run",
        capabilityId: "test_run_detail",
      },
      {
        evidenceId: "live_map",
        field: "evidence_entry",
        label: "实时执行地图与风险叠加",
        kind: "api",
        capabilityId: "live_map",
      },
    ],
    acceptance: [
      {
        acceptanceId: "execution_realtime",
        field: "evidence_entry",
        statement:
          "执行页必须提供实时状态（SSE/WebSocket/轮询兜底策略由后续 Task5 实现），并在 UI 上可观测离线/断连/延迟。",
        must: true,
        drilldowns: [{ kind: "api", label: "实时执行地图", capabilityId: "live_map" }],
      },
      {
        acceptanceId: "execution_failure_action",
        field: "next_actions",
        statement: "任何执行失败必须给出可重试入口与失败原因定位入口，且可回溯到对应证据（事件/风险/报告）。",
        must: true,
        drilldowns: [
          { kind: "api", label: "运行详情", capabilityId: "test_run_detail" },
          { kind: "route", label: "风险证据链", routeId: "risk_evidence_list" },
        ],
      },
    ],
  },
  {
    pageId: "live_map",
    routeIds: ["live_map"],
    capabilities: ["live_map", "risk_list"],
    metrics: [
      {
        metricId: "live_map_updated_at",
        field: "evidence_entry",
        label: "实时地图更新时间",
        capabilityId: "live_map",
        jsonPath: "data.updated_at",
      },
    ],
    evidenceEntrypoints: [
      {
        evidenceId: "live_map_events",
        field: "evidence_entry",
        label: "事件流",
        kind: "api",
        capabilityId: "live_map",
      },
    ],
    acceptance: [
      {
        acceptanceId: "live_map_risk_overlay",
        field: "launch_blockers",
        statement: "实时执行地图必须支持风险叠加（含阻断标记），并能从叠加点下钻到风险详情。",
        must: true,
        drilldowns: [
          { kind: "route", label: "风险证据链", routeId: "risk_evidence_list" },
          { kind: "route", label: "风险详情", routeId: "risk_evidence_detail" },
        ],
      },
    ],
  },
  {
    pageId: "risk_evidence_list",
    routeIds: ["risk_evidence_list"],
    capabilities: ["risk_list", "value_metrics"],
    metrics: [
      {
        metricId: "blocking_risk_count",
        field: "launch_blockers",
        label: "阻断风险数",
        unit: "个",
        capabilityId: "value_metrics",
        jsonPath: "data.launch_blocking_risks",
        drilldowns: [{ kind: "route", label: "质量驾驶舱", routeId: "command_center" }],
      },
    ],
    evidenceEntrypoints: [
      {
        evidenceId: "risk_list_api",
        field: "evidence_entry",
        label: "风险列表（可筛选）",
        kind: "api",
        capabilityId: "risk_list",
      },
    ],
    acceptance: [
      {
        acceptanceId: "risk_list_decision",
        field: "launch_blockers",
        statement:
          "风险证据链列表必须默认突出“上线阻断”与“业务影响”，并支持 severity/status/business_flow/launch_blocking 过滤。",
        must: true,
        drilldowns: [{ kind: "api", label: "风险列表", capabilityId: "risk_list" }],
      },
      {
        acceptanceId: "risk_list_drilldown_detail",
        field: "evidence_entry",
        statement: "列表中每条风险必须能进入详情，且详情必须展示证据包与关闭准则。",
        must: true,
        drilldowns: [{ kind: "route", label: "风险详情", routeId: "risk_evidence_detail" }],
      },
    ],
  },
  {
    pageId: "risk_evidence_detail",
    routeIds: ["risk_evidence_detail"],
    capabilities: ["risk_detail", "risk_list"],
    metrics: [
      {
        metricId: "risk_severity",
        field: "launch_blockers",
        label: "风险严重度",
        capabilityId: "risk_detail",
        jsonPath: "data.risk.severity",
      },
    ],
    evidenceEntrypoints: [
      {
        evidenceId: "risk_evidence_bundle",
        field: "evidence_entry",
        label: "证据包（复现/日志/对比/因果）",
        kind: "api",
        capabilityId: "risk_detail",
      },
    ],
    acceptance: [
      {
        acceptanceId: "risk_detail_business_language",
        field: "evidence_entry",
        statement:
          "风险详情必须以业务语言呈现：业务影响、复现步骤、证据链、修复建议、关闭准则；不允许渲染原始敏感凭证。",
        must: true,
        drilldowns: [{ kind: "api", label: "风险详情", capabilityId: "risk_detail" }],
      },
      {
        acceptanceId: "risk_detail_back_to_decision",
        field: "launch_recommendation",
        statement: "风险详情必须提供“回到决策”入口（质量驾驶舱/领导层报告），避免证据迷航。",
        must: true,
        drilldowns: [
          { kind: "route", label: "质量驾驶舱", routeId: "command_center" },
          { kind: "route", label: "领导层报告", routeId: "executive_report" },
        ],
      },
    ],
  },
  {
    pageId: "executive_report",
    routeIds: ["executive_report"],
    capabilities: ["report_executive", "command_center", "value_metrics", "risk_list", "environment_readiness"],
    metrics: [
      {
        metricId: "report_summary",
        field: "launch_recommendation",
        label: "领导层摘要（含上线建议）",
        capabilityId: "report_executive",
        jsonPath: "data.executive_summary",
        drilldowns: [
          { kind: "route", label: "风险证据链", routeId: "risk_evidence_list" },
          { kind: "route", label: "环境诊断", routeId: "environment_diagnosis" },
        ],
      },
      {
        metricId: "report_hours_saved",
        field: "estimated_hours_saved",
        label: "节省工时（报告口径）",
        unit: "h",
        capabilityId: "value_metrics",
        jsonPath: "data.estimated_hours_saved",
        drilldowns: [{ kind: "route", label: "ROI/价值", routeId: "roi_value" }],
      },
      {
        metricId: "report_blocking_risks",
        field: "launch_blockers",
        label: "阻断风险数（报告口径）",
        unit: "个",
        capabilityId: "value_metrics",
        jsonPath: "data.launch_blocking_risks",
        drilldowns: [{ kind: "route", label: "风险证据链", routeId: "risk_evidence_list" }],
      },
    ],
    evidenceEntrypoints: [
      {
        evidenceId: "report_evidence_drilldown",
        field: "evidence_entry",
        label: "报告证据链下钻入口（风险/环境/执行）",
        kind: "report",
        routeId: "risk_evidence_list",
      },
    ],
    acceptance: [
      {
        acceptanceId: "report_readable",
        field: "launch_recommendation",
        statement: "领导层报告必须可读：先结论后证据，明确是否可上线、风险与成本来自哪里、下一步要做什么。",
        must: true,
        drilldowns: [{ kind: "api", label: "领导层报告", capabilityId: "report_executive" }],
      },
      {
        acceptanceId: "report_drilldown_every_conclusion",
        field: "evidence_entry",
        statement:
          "报告中每个关键结论（上线建议/阻断风险/节省工时/覆盖）都必须有可点击下钻到对应证据链。",
        must: true,
        drilldowns: [
          { kind: "route", label: "风险证据链", routeId: "risk_evidence_list" },
          { kind: "route", label: "环境诊断", routeId: "environment_diagnosis" },
          { kind: "route", label: "执行", routeId: "execution" },
        ],
      },
    ],
  },
  {
    pageId: "roi_value",
    routeIds: ["roi_value"],
    capabilities: ["value_metrics", "command_center"],
    metrics: [
      {
        metricId: "estimated_hours_saved",
        field: "estimated_hours_saved",
        label: "预计节省工时",
        unit: "h",
        capabilityId: "value_metrics",
        jsonPath: "data.estimated_hours_saved",
        drilldowns: [{ kind: "api", label: "价值指标", capabilityId: "value_metrics" }],
      },
      {
        metricId: "estimated_business_impact_min",
        field: "estimated_hours_saved",
        label: "潜在业务影响（下界）",
        unit: "CNY",
        capabilityId: "value_metrics",
        jsonPath: "data.estimated_business_impact_min",
      },
      {
        metricId: "estimated_business_impact_max",
        field: "estimated_hours_saved",
        label: "潜在业务影响（上界）",
        unit: "CNY",
        capabilityId: "value_metrics",
        jsonPath: "data.estimated_business_impact_max",
      },
      {
        metricId: "evidence_trust_score",
        field: "evidence_entry",
        label: "证据可信度（0-1）",
        capabilityId: "value_metrics",
        jsonPath: "data.evidence_trust_score",
        drilldowns: [{ kind: "route", label: "风险证据链", routeId: "risk_evidence_list" }],
      },
    ],
    evidenceEntrypoints: [
      {
        evidenceId: "roi_calc_notes",
        field: "evidence_entry",
        label: "计算说明（口径/单位/风险区间）",
        kind: "api",
        capabilityId: "value_metrics",
      },
    ],
    acceptance: [
      {
        acceptanceId: "roi_explainable",
        field: "estimated_hours_saved",
        statement: "ROI/价值页必须把数字解释为可审计的口径（测试点→分钟→小时、风险区间来源），并提供证据入口。",
        must: true,
        drilldowns: [{ kind: "api", label: "价值指标", capabilityId: "value_metrics" }],
      },
      {
        acceptanceId: "roi_actionable_next",
        field: "next_actions",
        statement:
          "ROI/价值页必须输出“下一步动作”（例如：先清阻断风险再谈上线、补齐覆盖缺口），并一键跳转。",
        must: true,
        drilldowns: [
          { kind: "route", label: "风险证据链", routeId: "risk_evidence_list" },
          { kind: "route", label: "能力中心/覆盖度", routeId: "capability_center" },
          { kind: "route", label: "执行", routeId: "execution" },
        ],
      },
    ],
  },
] as const;

export function buildValueSurfaceRegistry(): ValueSurfaceRegistry {
  return {
    backendCapabilities,
    routes,
    pages,
  };
}

export function assertValueSurfaceRegistry(registry: ValueSurfaceRegistry): void {
  const routeIds = new Set(registry.routes.map((route) => route.routeId));
  const capabilityIds = new Set(registry.backendCapabilities.map((cap) => cap.id));
  const pageIds = new Set(registry.pages.map((page) => page.pageId));

  if (routeIds.size !== registry.routes.length) throw new Error("duplicate routeId in routes");
  if (capabilityIds.size !== registry.backendCapabilities.length) throw new Error("duplicate capabilityId in backendCapabilities");
  if (pageIds.size !== registry.pages.length) throw new Error("duplicate pageId in pages");

  for (const page of registry.pages) {
    for (const routeId of page.routeIds) {
      if (!routeIds.has(routeId)) throw new Error(`page ${page.pageId} references unknown routeId ${routeId}`);
    }
    for (const capId of page.capabilities) {
      if (!capabilityIds.has(capId)) throw new Error(`page ${page.pageId} references unknown capabilityId ${capId}`);
    }
    for (const metric of page.metrics) {
      if (!capabilityIds.has(metric.capabilityId))
        throw new Error(`metric ${metric.metricId} references unknown capabilityId ${metric.capabilityId}`);
      for (const drilldown of metric.drilldowns || []) {
        if (drilldown.routeId && !routeIds.has(drilldown.routeId))
          throw new Error(`metric ${metric.metricId} drilldown references unknown routeId ${drilldown.routeId}`);
        if (drilldown.capabilityId && !capabilityIds.has(drilldown.capabilityId))
          throw new Error(`metric ${metric.metricId} drilldown references unknown capabilityId ${drilldown.capabilityId}`);
      }
    }
    for (const acceptance of page.acceptance) {
      for (const drilldown of acceptance.drilldowns || []) {
        if (drilldown.routeId && !routeIds.has(drilldown.routeId))
          throw new Error(`acceptance ${acceptance.acceptanceId} drilldown references unknown routeId ${drilldown.routeId}`);
        if (drilldown.capabilityId && !capabilityIds.has(drilldown.capabilityId))
          throw new Error(
            `acceptance ${acceptance.acceptanceId} drilldown references unknown capabilityId ${drilldown.capabilityId}`,
          );
      }
    }
  }
}

