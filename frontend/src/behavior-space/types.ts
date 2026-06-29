export type BehaviorSpaceSceneStatus = "ready" | "warning" | "blocked" | "unknown";

export type BehaviorSpaceNodeKind =
  | "frontend"
  | "service"
  | "database"
  | "queue"
  | "external_api"
  | "worker"
  | "environment"
  | "other";

export type BehaviorCoverageStatus = "covered" | "partial" | "uncovered" | "blocked" | "unknown";

export type ProbeExecutionStatus = "planned" | "queued" | "running" | "completed" | "blocked" | "failed" | "unknown";

export type BehaviorSpaceTone = "positive" | "warning" | "critical" | "neutral";

export type BehaviorSpaceValueFieldId =
  | "launch_recommendation"
  | "risk_cost"
  | "next_actions"
  | "behavior_coverage"
  | "efficiency_gain"
  | "environment_status"
  | "replay_readiness"
  | "audit_readiness";

export interface BehaviorSpaceActionItem {
  label: string;
  href?: string;
  routeId?: string;
}

export interface BehaviorSpaceValueField {
  fieldId: BehaviorSpaceValueFieldId;
  label: string;
  value: string;
  tone: BehaviorSpaceTone;
  supportingText?: string;
  href?: string;
  routeId?: string;
  capabilityId?: string;
  actions?: readonly BehaviorSpaceActionItem[];
}

export interface BehaviorSpaceValueSummary {
  launchRecommendation: BehaviorSpaceValueField;
  riskCost: BehaviorSpaceValueField;
  nextActions: BehaviorSpaceValueField;
  coverage: BehaviorSpaceValueField;
  efficiencyGain: BehaviorSpaceValueField;
  environmentStatus: BehaviorSpaceValueField;
  replayReadiness: BehaviorSpaceValueField;
  auditReadiness: BehaviorSpaceValueField;
  fields: readonly BehaviorSpaceValueField[];
}

export interface BehaviorScene {
  sceneId: string;
  projectId: string;
  title: string;
  summary: string;
  status: BehaviorSpaceSceneStatus;
  updatedAt?: string;
  environmentStatus: BehaviorSpaceSceneStatus;
  coverageStatus: BehaviorCoverageStatus;
  sourceCapabilities: readonly string[];
}

export interface BehaviorSystemNode {
  nodeId: string;
  label: string;
  kind: BehaviorSpaceNodeKind;
  domain?: string;
  flowIds: readonly string[];
  status: BehaviorSpaceSceneStatus;
  riskCount: number;
  evidenceRefIds: readonly string[];
}

export interface BehaviorPath {
  pathId: string;
  label: string;
  sourceNodeId?: string;
  targetNodeId?: string;
  nodeIds: readonly string[];
  coverageStatus: BehaviorCoverageStatus;
  riskCount: number;
  blockerCount: number;
  evidenceRefIds: readonly string[];
  findingIds: readonly string[];
  replayRefIds: readonly string[];
}

export interface ProbeExecution {
  executionId: string;
  label: string;
  runId?: string;
  status: ProbeExecutionStatus;
  executionMode?: string;
  total?: number;
  completed?: number;
  failed?: number;
  updatedAt?: string;
  evidenceRefIds: readonly string[];
}

export interface BehaviorFinding {
  findingId: string;
  title: string;
  severity: string;
  summary: string;
  businessImpact?: string;
  launchBlocking: boolean;
  pathIds: readonly string[];
  evidenceRefIds: readonly string[];
  replayRefId?: string;
}

export type BehaviorEvidenceKind = "risk" | "environment" | "run" | "report" | "api" | "audit";

export interface BehaviorEvidenceRef {
  evidenceRefId: string;
  label: string;
  kind: BehaviorEvidenceKind;
  summary: string;
  href?: string;
  routeId?: string;
  capabilityId?: string;
  findingId?: string;
  pathId?: string;
}

export interface BehaviorReplayRef {
  replayRefId: string;
  label: string;
  riskId: string;
  pathId?: string;
  href?: string;
  routeId?: string;
  summary?: string;
  updatedAt?: string;
  steps: readonly string[];
  evidenceRefIds: readonly string[];
}

export type BehaviorAuditKind = "snapshot" | "report" | "delivery" | "approval" | "export" | "execution" | "signoff";

export interface BehaviorAuditRef {
  auditRefId: string;
  label: string;
  kind: BehaviorAuditKind;
  actor: string;
  timestamp?: string;
  summary: string;
  href?: string;
  routeId?: string;
}

export interface BehaviorSpaceVisualization {
  scene: BehaviorScene;
  systemNodes: readonly BehaviorSystemNode[];
  behaviorPaths: readonly BehaviorPath[];
  probeExecutions: readonly ProbeExecution[];
  findings: readonly BehaviorFinding[];
  evidenceRefs: readonly BehaviorEvidenceRef[];
  replayRefs: readonly BehaviorReplayRef[];
  auditRefs: readonly BehaviorAuditRef[];
  valueSummary: BehaviorSpaceValueSummary;
}

export interface BehaviorSpaceDataBundle {
  projectId: string;
  commandCenter?: unknown;
  valueMetrics?: unknown;
  businessModel?: unknown;
  environmentReadiness?: unknown;
  risks?: unknown;
  executiveReport?: unknown;
  liveMap?: unknown;
  onboarding?: unknown;
  testRun?: unknown;
  riskDetails?: readonly { riskId: string; detail: unknown }[];
}
