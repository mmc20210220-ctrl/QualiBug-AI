export type RiskReplayStepKind =
  | "entry"
  | "discovery"
  | "request"
  | "response"
  | "snapshot"
  | "finding"
  | "reproduction"
  | "remediation";

export type RiskReplayStepStatus = "pending" | "running" | "completed" | "warning" | "failed" | "blocked";

export interface RiskReplayField {
  label: string;
  value: string;
}

export interface RiskReplayStep {
  stepId: string;
  index: number;
  kind: RiskReplayStepKind;
  title: string;
  summary: string;
  status: RiskReplayStepStatus;
  timestamp?: string;
  cue?: string;
  failurePoint: boolean;
  fields: readonly RiskReplayField[];
}

export interface RiskReplayJumpTarget {
  stepId: string;
  label: string;
  reason: string;
}

export interface RiskReplayMetrics {
  totalSteps: number;
  failureSteps: number;
  replayReady: boolean;
}

export interface RiskReplayTimeline {
  projectId: string;
  riskId: string;
  title: string;
  summary: string;
  status: RiskReplayStepStatus;
  updatedAt?: string;
  reproductionSteps: readonly string[];
  copyText: string;
  steps: readonly RiskReplayStep[];
  jumpTargets: readonly RiskReplayJumpTarget[];
  metrics: RiskReplayMetrics;
}
