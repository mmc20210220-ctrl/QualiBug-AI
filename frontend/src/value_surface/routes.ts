import type { RouteDefinition } from "./types";

export const routes: readonly RouteDefinition[] = [
  {
    routeId: "login",
    pageId: "login",
    pathTemplate: "/login",
    navLabel: "登录",
    requiresAuth: false,
    requiresProject: false,
  },
  {
    routeId: "project_list",
    pageId: "project_list",
    pathTemplate: "/projects",
    navLabel: "项目列表",
    requiresAuth: true,
    requiresProject: false,
  },
  {
    routeId: "project_workspace",
    pageId: "project_workspace",
    pathTemplate: "/projects/{projectId}",
    navLabel: "项目工作区",
    requiresAuth: true,
    requiresProject: true,
    params: ["projectId"],
  },
  {
    routeId: "command_center",
    pageId: "command_center",
    pathTemplate: "/projects/{projectId}/command-center",
    navLabel: "质量驾驶舱",
    requiresAuth: true,
    requiresProject: true,
    params: ["projectId"],
  },
  {
    routeId: "capability_center",
    pageId: "capability_center",
    pathTemplate: "/projects/{projectId}/capabilities",
    navLabel: "能力中心/覆盖度",
    requiresAuth: true,
    requiresProject: true,
    params: ["projectId"],
  },
  {
    routeId: "business_flow_map",
    pageId: "business_flow_map",
    pathTemplate: "/projects/{projectId}/business-model",
    navLabel: "业务流程地图",
    requiresAuth: true,
    requiresProject: true,
    params: ["projectId"],
  },
  {
    routeId: "environment_diagnosis",
    pageId: "environment_diagnosis",
    pathTemplate: "/projects/{projectId}/environment",
    navLabel: "环境诊断",
    requiresAuth: true,
    requiresProject: true,
    params: ["projectId"],
  },
  {
    routeId: "test_plan",
    pageId: "test_plan",
    pathTemplate: "/projects/{projectId}/test-plan",
    navLabel: "AI 测试计划",
    requiresAuth: true,
    requiresProject: true,
    params: ["projectId"],
  },
  {
    routeId: "execution",
    pageId: "execution",
    pathTemplate: "/projects/{projectId}/execution",
    navLabel: "执行与任务生命周期",
    requiresAuth: true,
    requiresProject: true,
    params: ["projectId"],
  },
  {
    routeId: "live_map",
    pageId: "live_map",
    pathTemplate: "/projects/{projectId}/live-map",
    navLabel: "实时执行地图",
    requiresAuth: true,
    requiresProject: true,
    params: ["projectId"],
  },
  {
    routeId: "risk_evidence_list",
    pageId: "risk_evidence_list",
    pathTemplate: "/projects/{projectId}/risks",
    navLabel: "风险证据链",
    requiresAuth: true,
    requiresProject: true,
    params: ["projectId"],
  },
  {
    routeId: "risk_evidence_detail",
    pageId: "risk_evidence_detail",
    pathTemplate: "/projects/{projectId}/risks/{riskId}",
    navLabel: "风险详情",
    requiresAuth: true,
    requiresProject: true,
    params: ["projectId", "riskId"],
  },
  {
    routeId: "executive_report",
    pageId: "executive_report",
    pathTemplate: "/projects/{projectId}/reports/executive",
    navLabel: "领导层报告",
    requiresAuth: true,
    requiresProject: true,
    params: ["projectId"],
  },
  {
    routeId: "roi_value",
    pageId: "roi_value",
    pathTemplate: "/projects/{projectId}/roi",
    navLabel: "ROI/价值",
    requiresAuth: true,
    requiresProject: true,
    params: ["projectId"],
  },
] as const;

export function fillPathTemplate(pathTemplate: string, params: Partial<Record<string, string>>): string {
  return pathTemplate.replace(/\{([a-zA-Z0-9_]+)\}/g, (match, key: string) => {
    const value = params[key];
    if (typeof value !== "string" || !value) return match;
    return encodeURIComponent(value);
  });
}

