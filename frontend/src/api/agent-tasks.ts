import { asArray, asRecord, asString } from '../lib/value-guards';
import { API_V1_BASE, fetchJSON } from './session';

export type AgentTaskIntent =
  | 'release_readiness'
  | 'find_blockers'
  | 'verify_changes'
  | 'analyze_requirements';

export type AgentTaskStatus =
  | 'CREATED'
  | 'UNDERSTANDING'
  | 'PLANNING'
  | 'BLOCKED'
  | 'READY'
  | 'RUNNING'
  | 'EVALUATING'
  | 'COMPLETED'
  | 'FAILED'
  | 'CANCELLED';

export type AgentTaskEvent = {
  schemaVersion: string;
  eventId: string;
  taskId: string;
  eventType: string;
  occurredAt: string;
  correlationId: string;
  detail: Record<string, unknown>;
};

export type AgentGroundingBlocker = {
  code: string;
  message: string;
  source: string;
};

export type AgentGroundingSummary = {
  selectedTargetCount: number;
  runtimeBoundTargetCount: number;
  preflightReady: boolean;
};

export type AgentTask = {
  schemaVersion: string;
  taskId: string;
  projectId: string;
  goal: string;
  intent: AgentTaskIntent;
  status: AgentTaskStatus;
  sourceSnapshotStatus: string;
  sourceSnapshotRef: string;
  sourceRevisionState: string;
  selectedTestTargets: string[];
  executionRunId: string;
  runtimeGroundingStatus: string;
  runtimeContext: Record<string, unknown>;
  groundingBlockers: AgentGroundingBlocker[];
  groundingSummary: AgentGroundingSummary;
  groundingEvaluatedAt: string;
  createdAt: string;
  updatedAt: string;
  completedAt: string;
  cancelledAt: string;
  eventCount: number;
  latestEvent: AgentTaskEvent | null;
};

const INTENTS = new Set<AgentTaskIntent>([
  'release_readiness',
  'find_blockers',
  'verify_changes',
  'analyze_requirements',
]);

const STATUSES = new Set<AgentTaskStatus>([
  'CREATED',
  'UNDERSTANDING',
  'PLANNING',
  'BLOCKED',
  'READY',
  'RUNNING',
  'EVALUATING',
  'COMPLETED',
  'FAILED',
  'CANCELLED',
]);

function parseEvent(value: unknown): AgentTaskEvent | null {
  const record = asRecord(value);
  const eventId = asString(record.event_id);
  const taskId = asString(record.task_id);
  const eventType = asString(record.event_type);
  if (!eventId || !taskId || !eventType) return null;
  return {
    schemaVersion: asString(record.schema_version),
    eventId,
    taskId,
    eventType,
    occurredAt: asString(record.occurred_at),
    correlationId: asString(record.correlation_id),
    detail: asRecord(record.detail),
  };
}

function parseBlockers(value: unknown): AgentGroundingBlocker[] {
  return asArray(value).map(asRecord).map((record) => ({
    code: asString(record.code),
    message: asString(record.message),
    source: asString(record.source),
  })).filter((blocker) => Boolean(blocker.code));
}

function parseTask(value: unknown): AgentTask {
  const record = asRecord(value);
  const sourceSnapshot = asRecord(record.source_snapshot);
  const groundingSummary = asRecord(record.grounding_summary);
  const taskId = asString(record.task_id);
  const projectId = asString(record.project_id);
  const goal = asString(record.goal);
  const intentValue = asString(record.intent) as AgentTaskIntent;
  const statusValue = asString(record.status).toUpperCase() as AgentTaskStatus;
  if (!taskId || !projectId || !goal || !INTENTS.has(intentValue) || !STATUSES.has(statusValue)) {
    throw new Error('Agent Task 响应不符合产品契约。');
  }
  return {
    schemaVersion: asString(record.schema_version),
    taskId,
    projectId,
    goal,
    intent: intentValue,
    status: statusValue,
    sourceSnapshotStatus: asString(sourceSnapshot.status),
    sourceSnapshotRef: asString(sourceSnapshot.snapshot_ref),
    sourceRevisionState: asString(sourceSnapshot.source_revision_state),
    selectedTestTargets: asArray(record.selected_test_targets).map(asString).filter(Boolean),
    executionRunId: asString(record.execution_run_id),
    runtimeGroundingStatus: asString(record.runtime_grounding_status),
    runtimeContext: asRecord(record.runtime_context),
    groundingBlockers: parseBlockers(record.grounding_blockers),
    groundingSummary: {
      selectedTargetCount: Number(groundingSummary.selected_target_count || 0),
      runtimeBoundTargetCount: Number(groundingSummary.runtime_bound_target_count || 0),
      preflightReady: groundingSummary.preflight_ready === true,
    },
    groundingEvaluatedAt: asString(record.grounding_evaluated_at),
    createdAt: asString(record.created_at),
    updatedAt: asString(record.updated_at),
    completedAt: asString(record.completed_at),
    cancelledAt: asString(record.cancelled_at),
    eventCount: Number(record.event_count || 0),
    latestEvent: parseEvent(record.latest_event),
  };
}

function basePath(project: string): string {
  return `${API_V1_BASE}/projects/${encodeURIComponent(project)}/agent-tasks`;
}

export async function createAgentTask(
  project: string,
  input: { goal: string; intent: AgentTaskIntent },
): Promise<AgentTask> {
  const payload = asRecord(await fetchJSON<unknown>(basePath(project), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  }));
  return parseTask(payload.data);
}

export async function groundAgentTask(
  project: string,
  taskId: string,
  input?: { testTargetIds?: string[]; preflight?: Record<string, unknown> },
): Promise<AgentTask> {
  const payload = asRecord(await fetchJSON<unknown>(
    `${basePath(project)}/${encodeURIComponent(taskId)}/ground`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        test_target_ids: input?.testTargetIds || [],
        preflight: input?.preflight || {},
      }),
    },
  ));
  return parseTask(payload.data);
}

export async function getAgentTask(project: string, taskId: string): Promise<AgentTask> {
  const payload = asRecord(await fetchJSON<unknown>(
    `${basePath(project)}/${encodeURIComponent(taskId)}`,
  ));
  return parseTask(payload.data);
}

export async function getAgentTaskEvents(
  project: string,
  taskId: string,
): Promise<AgentTaskEvent[]> {
  const payload = asRecord(await fetchJSON<unknown>(
    `${basePath(project)}/${encodeURIComponent(taskId)}/events`,
  ));
  return asArray(payload.items).map(parseEvent).filter((event): event is AgentTaskEvent => event !== null);
}

export async function getAgentTaskBundle(
  project: string,
  taskId: string,
): Promise<{ task: AgentTask; events: AgentTaskEvent[] }> {
  const [task, events] = await Promise.all([
    getAgentTask(project, taskId),
    getAgentTaskEvents(project, taskId),
  ]);
  return { task, events };
}

export async function cancelAgentTask(project: string, taskId: string): Promise<AgentTask> {
  const payload = asRecord(await fetchJSON<unknown>(
    `${basePath(project)}/${encodeURIComponent(taskId)}/cancel`,
    { method: 'POST' },
  ));
  return parseTask(payload.data);
}
