import { currentToken, getSession } from './client';

type JsonRecord = Record<string, unknown>;

export type KnowledgeConnectorProfile = {
  connector_instance_id: string;
  profile_ref?: string;
  connector_type?: string;
  auth_mode?: 'internal_app' | 'tenant_access_token' | 'user_access_token' | string;
  configured_fields?: Record<string, boolean>;
  credentials_configured: boolean;
  checkpoint_configured: boolean;
  plaintext_returned?: boolean;
};

export type KnowledgeConnectorAutoSync = {
  enabled: boolean;
  state: 'scheduled' | 'running' | 'healthy' | 'retrying' | 'disabled' | string;
  message: string;
  last_attempt_at_utc?: string;
  last_success_at_utc?: string;
  next_attempt_at_utc?: string;
  failure_count: number;
  attention?: string;
  refresh_interval_seconds?: number;
  maintenance_required_by_user: boolean;
  checkpoint_recovery_is_automatic?: boolean;
  stale_writer_fencing_is_automatic?: boolean;
  raw_error_returned?: boolean;
};

export type KnowledgeConnectorUnsupportedResource = {
  remote_resource_id: string;
  resource_kind?: string;
  remote_object_type?: string;
  display_title?: string;
  reason_code?: string;
  retry_trigger?: string;
  content_materialized: false;
  source_occurrence_created: false;
  customer_source_modified: false;
};

export type KnowledgeConnectorRemoteLifecycle = {
  status: string;
  authoritative_snapshot_complete: boolean;
  present_count: number;
  absent_count: number;
  unconfirmed_missing_count: number;
  retirement_eligible_count: number;
  retired_count: number;
  renamed_resource_count: number;
  moved_resource_count: number;
  reappeared_resource_count: number;
  retire_after_complete_snapshots: number;
  requested_deletion_policy?: string;
  effective_deletion_policy?: string;
  absence_interpretation?: string;
  sync_receipt_persisted?: boolean | null;
  evidence_persistence_status?: string;
  remote_deletion_inferred: false;
  permission_loss_inferred: false;
  historical_source_bytes_retained: true;
  customer_material_mutation_executed: false;
  remote_resource_identities_returned: false;
  source_refs_returned: false;
};

export type KnowledgeConnectorCoverage = {
  status: 'COMPLETE' | 'PARTIAL_UNSUPPORTED' | 'NOT_AVAILABLE' | 'UNKNOWN' | string;
  complete: boolean;
  discovered_count: number;
  covered_count: number;
  unsupported_count: number;
  coverage_ratio: number;
  unsupported_resources: KnowledgeConnectorUnsupportedResource[];
  unsupported_resources_truncated?: boolean;
  last_sync_epoch_id?: string;
  last_completed_at_utc?: string;
  remote_lifecycle?: KnowledgeConnectorRemoteLifecycle;
  source_content_returned?: boolean;
  customer_material_mutation_executed?: boolean;
};

export type KnowledgeConnectorRecord = {
  connector_instance_id: string;
  connector_type: string;
  display_name: string;
  resource_scope: string;
  status: 'ACTIVE' | 'PAUSED' | 'DISABLED' | string;
  active_sync_epoch_id?: string;
  last_successful_sync_epoch_id?: string;
  last_successful_sync_at_utc?: string;
  last_failed_sync_epoch_id?: string;
  last_failed_sync_at_utc?: string;
  last_sync_completed_at_utc?: string;
  connection_profile?: KnowledgeConnectorProfile;
  auto_sync?: KnowledgeConnectorAutoSync;
  coverage?: KnowledgeConnectorCoverage;
};

export type KnowledgeConnectorInventory = {
  project_id: string;
  connectors: KnowledgeConnectorRecord[];
  summary: {
    connector_instance_count?: number;
    active_count?: number;
    running_count?: number;
    profile_count?: number;
    credentials_configured_count?: number;
    automatic_refresh_enabled?: boolean;
    partial_coverage_connector_count?: number;
    unsupported_resource_count?: number;
    remote_absent_resource_count?: number;
    remote_unconfirmed_missing_resource_count?: number;
    remote_retired_resource_count?: number;
  };
  governance?: Record<string, unknown>;
};

export type ConfigureFeishuConnectorInput = {
  connector_instance_id: string;
  display_name: string;
  resource_scope: string;
  status?: 'ACTIVE' | 'PAUSED' | 'DISABLED';
  connection_profile: {
    auth_mode: 'internal_app' | 'tenant_access_token' | 'user_access_token';
    app_id?: string;
    app_secret?: string;
    tenant_access_token?: string;
    user_access_token?: string;
  };
};

export type KnowledgeConnectorActionResult = {
  status?: string;
  connector_instance_id?: string;
  sync_epoch_id?: string;
  success_count?: number;
  failure_count?: number;
  retired_count?: number;
  discovered_resource_count?: number;
  materialized_resource_count?: number;
  unchanged_resource_count?: number;
  covered_resource_count?: number;
  unsupported_resource_count?: number;
  unknown_gap_count?: number;
  knowledge_coverage_ratio?: number;
  knowledge_coverage_status?: string;
  knowledge_coverage_complete?: boolean;
  unsupported_resources?: KnowledgeConnectorUnsupportedResource[];
  remote_lifecycle?: KnowledgeConnectorRemoteLifecycle;
  remote_lifecycle_status?: string;
  remote_absent_count?: number;
  remote_unconfirmed_missing_count?: number;
  remote_retirement_eligible_count?: number;
  renamed_resource_count?: number;
  moved_resource_count?: number;
  reappeared_resource_count?: number;
  remote_deletion_inferred?: boolean;
  permission_loss_inferred?: boolean;
  degraded_resource_count?: number;
  snapshot_complete?: boolean;
  cursor_checkpoint_committed?: boolean;
  checkpoint_storage?: string;
  next_cursor_returned_to_client?: boolean;
  fencing_token_returned_to_client?: boolean;
  source_content_returned?: boolean;
  customer_material_mutation_executed?: boolean;
  errors?: Array<Record<string, unknown>>;
  [key: string]: unknown;
};

export type ConnectFeishuKnowledgeResult = {
  connector: KnowledgeConnectorRecord;
  connection: KnowledgeConnectorActionResult;
  sync: KnowledgeConnectorActionResult;
};

const asRecord = (value: unknown): JsonRecord => (
  value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as JsonRecord
    : {}
);
const asArray = (value: unknown): unknown[] => Array.isArray(value) ? value : [];
const asString = (value: unknown): string => typeof value === 'string' ? value : '';
const asBoolean = (value: unknown): boolean => value === true;
const asNumber = (value: unknown): number | undefined => typeof value === 'number' ? value : undefined;

function friendlyConnectorError(rawMessage: string, status: number): string {
  const message = rawMessage.toLowerCase();
  if (
    message.includes('already_running')
    || message.includes('lock_held')
    || message.includes('owner_active')
    || message.includes('owner_unverified')
    || message.includes('fence_revoked')
    || message.includes('fence_transaction_busy')
    || message.includes('transaction_busy')
  ) {
    return '资料正在更新，无需重复操作。系统完成后会自动显示最新状态。';
  }
  if (message.includes('checkpoint') || message.includes('cursor_mismatch') || message.includes('previous_cursor_required')) {
    return '上次更新状态不完整，系统已保留原有资料并会自动重试。仍未恢复时，请重新授权。';
  }
  if (message.includes('permission') || message.includes('forbidden') || message.includes('code_1069902') || message.includes('code_99991663')) {
    return '飞书授权范围不足。请在飞书开放平台补充知识库与云文档只读权限，然后重新授权。';
  }
  if (message.includes('credential') || message.includes('profile') || message.includes('app_secret') || message.includes('access_token') || message.includes('auth_mode')) {
    return '飞书连接信息未通过验证。请检查 App ID 与 App Secret，或重新填写访问令牌。';
  }
  if (message.includes('rate') || message.includes('99991400') || status === 429) {
    return '飞书暂时限制了访问频率，原有资料不受影响，系统会自动重试。';
  }
  if (message.includes('transport_failed') || message.includes('api_failed') || message.includes('download_failed') || status >= 500) {
    return '暂时无法读取飞书资料，原有资料不受影响，系统会自动重试。';
  }
  if (message.includes('unsupported') || message.includes('export')) {
    return '部分飞书资料暂时无法读取，系统没有覆盖原有资料。请确认文档权限或格式。';
  }
  return '在线资料操作未完成，原有资料不受影响。系统会自动重试；仍失败时请重新授权。';
}

async function connectorRequest(path: string, init?: RequestInit): Promise<JsonRecord> {
  const session = await getSession();
  if (!session) throw new Error('未登录或会话已失效，请重新登录。');

  const headers = new Headers(init?.headers);
  if (init?.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  const token = currentToken();
  if (token) headers.set('Authorization', `Bearer ${token}`);

  const response = await fetch(path, {
    ...init,
    headers,
    credentials: 'include',
    cache: 'no-store',
  });
  const raw = await response.text();
  let payload: JsonRecord = {};
  if (raw.trim()) {
    try {
      payload = asRecord(JSON.parse(raw));
    } catch {
      if (!response.ok) throw new Error(friendlyConnectorError(raw.slice(0, 200), response.status));
      throw new Error('在线资料服务返回异常，请刷新页面后重试。');
    }
  }
  if (!response.ok) {
    const message = asString(payload.message) || asString(payload.error) || `API ${response.status}`;
    throw new Error(friendlyConnectorError(message, response.status));
  }
  return payload;
}

function projectConnectorPath(projectId: string, suffix = ''): string {
  const project = projectId.trim();
  if (!project) throw new Error('未选择有效项目。');
  return `/api/v1/projects/${encodeURIComponent(project)}/knowledge-connectors${suffix}`;
}

function toProfile(value: unknown): KnowledgeConnectorProfile {
  const row = asRecord(value);
  const configuredFields = asRecord(row.configured_fields);
  return {
    connector_instance_id: asString(row.connector_instance_id),
    profile_ref: asString(row.profile_ref) || undefined,
    connector_type: asString(row.connector_type) || undefined,
    auth_mode: asString(row.auth_mode) || undefined,
    configured_fields: Object.fromEntries(
      Object.entries(configuredFields).map(([key, configured]) => [key, configured === true]),
    ),
    credentials_configured: asBoolean(row.credentials_configured),
    checkpoint_configured: asBoolean(row.checkpoint_configured),
    plaintext_returned: asBoolean(row.plaintext_returned),
  };
}

function toAutoSync(value: unknown): KnowledgeConnectorAutoSync {
  const row = asRecord(value);
  return {
    enabled: asBoolean(row.enabled),
    state: asString(row.state),
    message: asString(row.message),
    last_attempt_at_utc: asString(row.last_attempt_at_utc) || undefined,
    last_success_at_utc: asString(row.last_success_at_utc) || undefined,
    next_attempt_at_utc: asString(row.next_attempt_at_utc) || undefined,
    failure_count: asNumber(row.failure_count) || 0,
    attention: asString(row.attention) || undefined,
    refresh_interval_seconds: asNumber(row.refresh_interval_seconds),
    maintenance_required_by_user: asBoolean(row.maintenance_required_by_user),
    checkpoint_recovery_is_automatic: asBoolean(row.checkpoint_recovery_is_automatic),
    stale_writer_fencing_is_automatic: asBoolean(row.stale_writer_fencing_is_automatic),
    raw_error_returned: asBoolean(row.raw_error_returned),
  };
}

function toUnsupportedResource(value: unknown): KnowledgeConnectorUnsupportedResource {
  const row = asRecord(value);
  return {
    remote_resource_id: asString(row.remote_resource_id),
    resource_kind: asString(row.resource_kind) || undefined,
    remote_object_type: asString(row.remote_object_type) || undefined,
    display_title: asString(row.display_title) || undefined,
    reason_code: asString(row.reason_code) || undefined,
    retry_trigger: asString(row.retry_trigger) || undefined,
    content_materialized: false,
    source_occurrence_created: false,
    customer_source_modified: false,
  };
}

function toRemoteLifecycle(value: unknown): KnowledgeConnectorRemoteLifecycle {
  const row = asRecord(value);
  const receipt = row.sync_receipt_persisted;
  return {
    status: asString(row.status) || 'NOT_AVAILABLE',
    authoritative_snapshot_complete: asBoolean(row.authoritative_snapshot_complete),
    present_count: asNumber(row.present_count) || 0,
    absent_count: asNumber(row.absent_count) || 0,
    unconfirmed_missing_count: asNumber(row.unconfirmed_missing_count) || 0,
    retirement_eligible_count: asNumber(row.retirement_eligible_count) || 0,
    retired_count: asNumber(row.retired_count) || 0,
    renamed_resource_count: asNumber(row.renamed_resource_count) || 0,
    moved_resource_count: asNumber(row.moved_resource_count) || 0,
    reappeared_resource_count: asNumber(row.reappeared_resource_count) || 0,
    retire_after_complete_snapshots: asNumber(row.retire_after_complete_snapshots) || 0,
    requested_deletion_policy: asString(row.requested_deletion_policy) || undefined,
    effective_deletion_policy: asString(row.effective_deletion_policy) || undefined,
    absence_interpretation: asString(row.absence_interpretation) || undefined,
    sync_receipt_persisted: typeof receipt === 'boolean' ? receipt : null,
    evidence_persistence_status: asString(row.evidence_persistence_status) || undefined,
    remote_deletion_inferred: false,
    permission_loss_inferred: false,
    historical_source_bytes_retained: true,
    customer_material_mutation_executed: false,
    remote_resource_identities_returned: false,
    source_refs_returned: false,
  };
}

function toCoverage(value: unknown): KnowledgeConnectorCoverage {
  const row = asRecord(value);
  return {
    status: asString(row.status) || 'NOT_AVAILABLE',
    complete: asBoolean(row.complete),
    discovered_count: asNumber(row.discovered_count) || 0,
    covered_count: asNumber(row.covered_count) || 0,
    unsupported_count: asNumber(row.unsupported_count) || 0,
    coverage_ratio: asNumber(row.coverage_ratio) || 0,
    unsupported_resources: asArray(row.unsupported_resources)
      .map(toUnsupportedResource)
      .filter((item) => Boolean(item.remote_resource_id)),
    unsupported_resources_truncated: asBoolean(row.unsupported_resources_truncated),
    last_sync_epoch_id: asString(row.last_sync_epoch_id) || undefined,
    last_completed_at_utc: asString(row.last_completed_at_utc) || undefined,
    remote_lifecycle: row.remote_lifecycle ? toRemoteLifecycle(row.remote_lifecycle) : undefined,
    source_content_returned: asBoolean(row.source_content_returned),
    customer_material_mutation_executed: asBoolean(row.customer_material_mutation_executed),
  };
}

function toConnector(value: unknown): KnowledgeConnectorRecord {
  const row = asRecord(value);
  return {
    connector_instance_id: asString(row.connector_instance_id),
    connector_type: asString(row.connector_type),
    display_name: asString(row.display_name),
    resource_scope: asString(row.resource_scope),
    status: asString(row.status),
    active_sync_epoch_id: asString(row.active_sync_epoch_id) || undefined,
    last_successful_sync_epoch_id: asString(row.last_successful_sync_epoch_id) || undefined,
    last_successful_sync_at_utc: asString(row.last_successful_sync_at_utc) || undefined,
    last_failed_sync_epoch_id: asString(row.last_failed_sync_epoch_id) || undefined,
    last_failed_sync_at_utc: asString(row.last_failed_sync_at_utc) || undefined,
    last_sync_completed_at_utc: asString(row.last_sync_completed_at_utc) || undefined,
    connection_profile: row.connection_profile ? toProfile(row.connection_profile) : undefined,
    auto_sync: row.auto_sync ? toAutoSync(row.auto_sync) : undefined,
    coverage: row.coverage ? toCoverage(row.coverage) : undefined,
  };
}

export async function listKnowledgeConnectors(projectId: string): Promise<KnowledgeConnectorInventory> {
  const payload = await connectorRequest(projectConnectorPath(projectId));
  const data = asRecord(payload.data);
  const summary = asRecord(data.summary);
  return {
    project_id: asString(data.project_id) || projectId,
    connectors: asArray(data.connectors).map(toConnector).filter((item) => Boolean(item.connector_instance_id)),
    summary: {
      connector_instance_count: asNumber(summary.connector_instance_count),
      active_count: asNumber(summary.active_count),
      running_count: asNumber(summary.running_count),
      profile_count: asNumber(summary.profile_count),
      credentials_configured_count: asNumber(summary.credentials_configured_count),
      automatic_refresh_enabled: asBoolean(summary.automatic_refresh_enabled),
      partial_coverage_connector_count: asNumber(summary.partial_coverage_connector_count),
      unsupported_resource_count: asNumber(summary.unsupported_resource_count),
      remote_absent_resource_count: asNumber(summary.remote_absent_resource_count),
      remote_unconfirmed_missing_resource_count: asNumber(summary.remote_unconfirmed_missing_resource_count),
      remote_retired_resource_count: asNumber(summary.remote_retired_resource_count),
    },
    governance: asRecord(data.governance),
  };
}

export async function configureFeishuConnector(
  projectId: string,
  input: ConfigureFeishuConnectorInput,
): Promise<KnowledgeConnectorRecord> {
  const payload = await connectorRequest(projectConnectorPath(projectId), {
    method: 'POST',
    body: JSON.stringify(input),
  });
  const data = asRecord(payload.data);
  return toConnector({
    ...asRecord(data.connector_instance),
    connection_profile: data.connection_profile,
  });
}

async function connectorAction(
  projectId: string,
  connectorId: string,
  action: 'test' | 'sync',
  body: JsonRecord = {},
): Promise<KnowledgeConnectorActionResult> {
  const connector = connectorId.trim();
  if (!connector) throw new Error('缺少在线资料源标识。');
  const payload = await connectorRequest(
    projectConnectorPath(projectId, `/${encodeURIComponent(connector)}/${action}`),
    { method: 'POST', body: JSON.stringify(body) },
  );
  return asRecord(payload.data) as KnowledgeConnectorActionResult;
}

export function testKnowledgeConnector(projectId: string, connectorId: string): Promise<KnowledgeConnectorActionResult> {
  return connectorAction(projectId, connectorId, 'test');
}

export function syncKnowledgeConnector(projectId: string, connectorId: string): Promise<KnowledgeConnectorActionResult> {
  return connectorAction(projectId, connectorId, 'sync', {
    deletion_policy: 'RETAIN',
    allow_raw_text_fallback: false,
  });
}

export async function connectFeishuKnowledge(
  projectId: string,
  input: ConfigureFeishuConnectorInput,
): Promise<ConnectFeishuKnowledgeResult> {
  const connector = await configureFeishuConnector(projectId, input);
  const connection = await testKnowledgeConnector(projectId, input.connector_instance_id);
  const sync = await syncKnowledgeConnector(projectId, input.connector_instance_id);
  return { connector, connection, sync };
}

export function refreshKnowledgeConnector(
  projectId: string,
  connectorId: string,
): Promise<KnowledgeConnectorActionResult> {
  return syncKnowledgeConnector(projectId, connectorId);
}
