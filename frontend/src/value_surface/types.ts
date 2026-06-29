export type HttpMethod = "GET" | "POST" | "PATCH";

export type BackendCapabilityId =
  | "runtime_health"
  | "industry_templates"
  | "projects"
  | "project_detail"
  | "onboarding"
  | "business_model"
  | "environment_readiness"
  | "test_plan"
  | "test_plan_generate"
  | "test_run_start"
  | "test_run_detail"
  | "command_center"
  | "live_map"
  | "risk_list"
  | "risk_detail"
  | "value_metrics"
  | "report_generate"
  | "report_executive";

export type ValueFieldId =
  | "launch_recommendation"
  | "launch_blockers"
  | "estimated_hours_saved"
  | "coverage"
  | "next_actions"
  | "evidence_entry";

export type PageId =
  | "login"
  | "project_list"
  | "project_workspace"
  | "command_center"
  | "capability_center"
  | "business_flow_map"
  | "environment_diagnosis"
  | "test_plan"
  | "execution"
  | "live_map"
  | "risk_evidence_list"
  | "risk_evidence_detail"
  | "executive_report"
  | "roi_value";

export interface BackendEndpoint {
  method: HttpMethod;
  pathTemplate: string;
}

export interface BackendCapability {
  id: BackendCapabilityId;
  label: string;
  endpoints: BackendEndpoint[];
  producedFields?: readonly string[];
}

export type RouteParamKey = "projectId" | "riskId" | "runId";

export interface RouteDefinition {
  routeId: string;
  pageId: PageId;
  pathTemplate: string;
  navLabel: string;
  requiresAuth: boolean;
  requiresProject: boolean;
  params?: readonly RouteParamKey[];
}

export type DrilldownKind = "route" | "api";

export interface Drilldown {
  kind: DrilldownKind;
  label: string;
  routeId?: string;
  capabilityId?: BackendCapabilityId;
  params?: Partial<Record<RouteParamKey, string>>;
}

export interface MetricDefinition {
  metricId: string;
  field: ValueFieldId;
  label: string;
  unit?: string;
  capabilityId: BackendCapabilityId;
  jsonPath?: string;
  drilldowns?: readonly Drilldown[];
}

export type EvidenceKind = "risk" | "run" | "report" | "audit" | "api";

export interface EvidenceEntryPoint {
  evidenceId: string;
  field: ValueFieldId;
  label: string;
  kind: EvidenceKind;
  routeId?: string;
  capabilityId?: BackendCapabilityId;
}

export interface AcceptancePoint {
  acceptanceId: string;
  field: ValueFieldId;
  statement: string;
  must: boolean;
  drilldowns?: readonly Drilldown[];
}

export interface PageValueDefinition {
  pageId: PageId;
  routeIds: readonly string[];
  capabilities: readonly BackendCapabilityId[];
  metrics: readonly MetricDefinition[];
  evidenceEntrypoints: readonly EvidenceEntryPoint[];
  acceptance: readonly AcceptancePoint[];
}

export interface ValueSurfaceRegistry {
  backendCapabilities: readonly BackendCapability[];
  routes: readonly RouteDefinition[];
  pages: readonly PageValueDefinition[];
}

