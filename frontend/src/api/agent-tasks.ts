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

export type AgentTask = {
  schemaVersion: string;
  taskId: string;
  projectId: string;
  goal: string;
  intent: AgentTaskIntent;
  status: AgentTaskStatus;
  sourceSnapshotStatus: string;
  sourceSnapshotRef: string;
  selectedTestTargets: string[];
  executionRunId: string;
  runtimeGroundingStatus: string;
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

function parseTask(value: unknown): AgentTask {
  const record = asRecord(value);
  const sourceSnapshot = asRecord(record.source_snapshot);
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
    selectedTestTargets: asArray(record.selected_test_targets).map(asString).filter(Boolean),
    executionRunId: asString(record.execution_run_id),
    runtimeGroundingStatus: asString(record.runtime_grounding_status),
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
