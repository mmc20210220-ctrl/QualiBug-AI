export type GateStatus = "passed" | "warning" | "blocked" | "checking" | "unknown";

export type EnvironmentDiagnosticNodeKind =
  | "entrypoint"
  | "network"
  | "security"
  | "auth"
  | "account"
  | "api"
  | "data"
  | "snapshot"
  | "cleanup"
  | "runtime";

export interface EnvironmentDiagnosticGate {
  gateId: string;
  label: string;
  status: GateStatus;
  allowed: boolean;
  summary: string;
  reason: string;
  remediationAction?: string;
}

export interface EnvironmentDiagnosticNode {
  nodeId: string;
  label: string;
  kind: EnvironmentDiagnosticNodeKind;
  stageId: string;
  status: GateStatus;
  headline: string;
  detail: string;
  businessExplanation: string;
  nextAction: string;
  gateIds: readonly string[];
  blockerIds: readonly string[];
}

export interface EnvironmentDiagnosticEdge {
  edgeId: string;
  sourceNodeId: string;
  targetNodeId: string;
  label: string;
  status: GateStatus;
}

export interface EnvironmentDiagnosticBlocker {
  blockerId: string;
  title: string;
  summary: string;
  status: GateStatus;
  reason: string;
  remediationAction: string;
  nodeIds: readonly string[];
  gateIds: readonly string[];
}

export interface EnvironmentRequiredInput {
  inputId: string;
  title: string;
  priority: "high" | "medium" | "low";
  status: "pending" | "provided" | "optional";
  whyNeeded: string;
  suggestedInput: string;
  affectedFlows: readonly string[];
}

export interface EnvironmentDiagnosticSummary {
  status: GateStatus;
  score: number | null;
  verdict: string;
  reason: string;
  safeExecutionMode: string;
  checkedAt?: string;
  redactionStatus?: string;
}

export interface EnvironmentDiagnosticGraph {
  projectId: string;
  source: "mock" | "demo" | "real" | "merged";
  summary: EnvironmentDiagnosticSummary;
  gates: readonly EnvironmentDiagnosticGate[];
  nodes: readonly EnvironmentDiagnosticNode[];
  edges: readonly EnvironmentDiagnosticEdge[];
  blockers: readonly EnvironmentDiagnosticBlocker[];
  requiredCustomerInputs: readonly EnvironmentRequiredInput[];
  suggestedActions: readonly string[];
}
