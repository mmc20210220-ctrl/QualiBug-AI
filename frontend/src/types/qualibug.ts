// Phase104D shared frontend types for QualiBug V1 pages.
// These are intentionally lightweight view-model types. The full route contract
// lives in ../contract/openapi.json and contract/API_CONTRACT.md.

export type ApiEnvelope<T = unknown> = {
  success: boolean;
  data: T | null;
  error: ApiError | null;
  meta: Record<string, unknown>;
};

export type ApiError = {
  code: string;
  message: string;
  status?: number;
  details?: Record<string, unknown>;
};

export type ProjectSummary = {
  project_id: string;
  project_name?: string;
  customer_name?: string;
  system_name?: string;
  industry?: string;
  status?: string;
};

export type DashboardViewModel = {
  qualityHealthScore: number | null;
  launchRecommendation: string;
  launchSummary: string;
  coreCoverageRate: number | null;
  launchBlockingRiskCount: number;
  estimatedHoursSaved: number | null;
  environmentStatus: string;
  topRisks: RiskCardViewModel[];
  executiveSummary: string;
};

export type RiskCardViewModel = {
  riskId: string;
  title: string;
  severity: string;
  businessImpact: string;
  affectedFlowName: string;
  launchBlocking: boolean;
  evidenceScore: number | null;
  reproducibilityScore: number | null;
  status: string;
};

export type EnvironmentViewModel = {
  status: string;
  score: number | null;
  allowFormalTest: boolean;
  safeExecutionMode: string;
  blockers: string[];
  suggestedActions: string[];
  requiredInputs: Array<Record<string, unknown>>;
};

export type LiveMapViewModel = {
  nodeCount: number;
  edgeCount: number;
  riskOverlayCount: number;
  highestRisk: string;
  events: Array<Record<string, unknown>>;
};

export type ExecutiveReportViewModel = {
  title: string;
  launchRecommendation: string;
  executiveSummary: string;
  topRiskCount: number;
  nextActionCount: number;
};
