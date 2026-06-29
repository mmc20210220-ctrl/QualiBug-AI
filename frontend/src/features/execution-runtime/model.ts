export type RuntimeTone = "neutral" | "info" | "success" | "warning" | "critical";

export type RuntimeEventKind =
  | "lifecycle"
  | "probe"
  | "request"
  | "response"
  | "snapshot"
  | "finding"
  | "blocker"
  | "summary";

export type RuntimeEventStatus = "pending" | "running" | "completed" | "success" | "warning" | "failed" | "blocked";

export type RuntimeStageNodeKind = "entry" | "probe" | "traffic" | "snapshot" | "finding" | "summary";

export interface RuntimeField {
  label: string;
  value: string;
}

export interface RuntimeSection {
  title: string;
  items: readonly string[];
}

export interface RuntimeLink {
  label: string;
  href: string;
  kind: "page" | "artifact";
}

export interface RuntimeEventEvidence {
  summary?: string;
  reason?: string;
  remediationAction?: string;
  fields?: readonly RuntimeField[];
  sections?: readonly RuntimeSection[];
  links?: readonly RuntimeLink[];
}

export interface RuntimeEvent {
  eventId: string;
  kind: RuntimeEventKind;
  status: RuntimeEventStatus;
  tone: RuntimeTone;
  title: string;
  message: string;
  timestamp?: string;
  runId?: string | null;
  nodeId?: string;
  edgeId?: string;
  actor?: string;
  phaseLabel?: string;
  tags: readonly string[];
  nextAction?: string;
  evidence?: RuntimeEventEvidence;
}

export interface RuntimeStageNode {
  nodeId: string;
  label: string;
  kind: RuntimeStageNodeKind;
  position: { x: number; y: number };
  status: RuntimeEventStatus;
  headline: string;
  detail: string;
  badges: readonly string[];
  eventIds: readonly string[];
  emphasis: boolean;
}

export interface RuntimeStageEdge {
  edgeId: string;
  sourceNodeId: string;
  targetNodeId: string;
  label: string;
  status: RuntimeEventStatus;
  eventIds: readonly string[];
  emphasis: boolean;
}

export interface RuntimeSummaryMetric {
  metricId: string;
  label: string;
  value: string;
  tone: RuntimeTone;
}

export interface RuntimeSummaryCard {
  title: string;
  outcome: "success" | "warning" | "failed";
  summary: string;
  completedAt?: string;
  highlights: readonly string[];
  nextActions: readonly string[];
  metrics: readonly RuntimeSummaryMetric[];
}

export interface RuntimeExecutionMetrics {
  progress: number | null;
  probeTotal: number | null;
  probeCompleted: number | null;
  probeFailed: number | null;
  riskFound: number | null;
}

export interface RuntimeExecutionTheater {
  projectId: string;
  source: "mock" | "demo" | "real" | "merged";
  runId: string | null;
  runStatus: string;
  updatedAt?: string;
  metrics: RuntimeExecutionMetrics;
  nodes: readonly RuntimeStageNode[];
  edges: readonly RuntimeStageEdge[];
  events: readonly RuntimeEvent[];
  summaryCard: RuntimeSummaryCard | null;
}
