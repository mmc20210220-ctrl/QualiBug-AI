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
  credential_status?: string;
  credential_expires_at_utc?: string;
  reauthorization_required?: boolean;
  reauthorization_reason?: string;
  plaintext_returned?: boolean;
};

export type ConnectorCredentialField = {
  name: string;
  display_name?: string;
  field_type: string;
  required: boolean;
  secret: boolean;
  description?: string;
  auth_modes?: string[];
};

export type ConnectorManifest = {
  schema?: string;
  connector_type: string;
  display_name: string;
  category: string;
  version: string;
  auth_modes: string[];
  scope_schema: Record<string, unknown>;
  quick_connect_schema?: {
    input_type?: string;
    scope_field?: string;
    priority?: number;
  };
  entrypoint_evidence?: {
    content_types?: string[];
    document_shapes?: string[];
    host_suffixes?: string[];
    path_suffixes?: string[];
  };
  supported_resource_types: string[];
  sync_modes: string[];
  webhook_supported: boolean;
  local_runner_supported: boolean;
  local_runner_required: boolean;
  read_only: boolean;
  credential_fields: ConnectorCredentialField[];
  capability_contract_version: string;
  webhook_policy_schema?: Record<string, unknown>;
  oauth_schema?: Record<string, unknown>;
};

export type ConnectorPermissionScope = {
  visibility?: string;
  availability?: string;
  evidence_status?: string;
  acl_version?: string;
  complete?: boolean;
  propagation_allowed?: boolean;
  raw_remote_principals_returned: false;
};

export type ConnectorTypeCatalog = {
  schema: string;
  connector_types: ConnectorManifest[];
  governance: Record<string, unknown>;
};

export type ConnectorSourcePreflightCandidate = {
  connector_type: string;
  display_name: string;
  category: string;
  scope_field: string;
  match_status: 'MATCHED' | 'AVAILABLE' | 'REVIEW_REQUIRED' | string;
  reason_code: string;
  evidence: string[];
  priority: number;
  requires_user_confirmation: boolean;
};

export type ConnectorSourcePreflight = {
  schema?: string;
  project_id: string;
  status: 'READY' | 'NEEDS_USER_CONFIRMATION' | 'AUTHORIZATION_REQUIRED' | 'REMOTE_ERROR' | 'NO_QUICK_CONNECTOR' | string;
  recommended_connector_type: string;
  candidates: ConnectorSourcePreflightCandidate[];
  observation: {
    http_status: number;
    content_type: string;
    response_bytes_read: number;
    response_fingerprint: string;
    document_shapes: string[];
    path_suffix_observed: boolean;
    redirected: boolean;
    final_host_fingerprint: string;
  };
  governance: Record<string, unknown>;
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
  last_oauth_refresh?: {
    supported: boolean;
    attempted: boolean;
    refreshed: boolean;
    refresh_status: string;
    credential_status?: string;
    credential_expires_at_utc?: string;
    permission_status?: string;
    credential_values_returned: false;
    source_identity_preserved: boolean;
    checkpoint_preserved: boolean;
    remote_deletion_inferred: false;
  };
  refresh_interval_seconds?: number;
  maintenance_required_by_user: boolean;
  checkpoint_recovery_is_automatic?: boolean;
  stale_writer_fencing_is_automatic?: boolean;
  raw_error_returned?: boolean;
};

export type KnowledgeConnectorHealth = {
  schema?: string;
  status: string;
  recommended_action: string;
  attention_reasons: string[];
  credential_status?: string;
  reauthorization_required: boolean;
  reauthorization_reason?: string;
  freshness: {
    status: string;
    last_successful_sync_at_utc?: string;
    age_seconds?: number;
    refresh_interval_seconds?: number;
    stale_after_seconds?: number;
  };
  webhook?: KnowledgeConnectorWebhook;
  oauth?: KnowledgeConnectorOAuth;
  metrics: {
    last_attempt_at_utc?: string;
    discovered_resource_count: number;
    covered_resource_count: number;
    unsupported_resource_count: number;
    coverage_ratio?: number;
    failure_count: number;
    retry_count: number;
    materialized_resource_count: number;
    unchanged_resource_count: number;
    unchanged_reuse_ratio?: number;
    semantic_refresh_status: string;
    semantic_event_count: number;
    semantic_changed_source_count: number;
    acl_propagation_status: string;
  };
  evidence: {
    source: string;
    measured: boolean;
    coverage_receipt_present: boolean;
    latest_sync_receipt_present: boolean;
    checked_at_utc: string;
  };
  source_content_returned: false;
  credentials_returned: false;
  raw_cursor_returned: false;
  customer_material_mutation_executed: false;
};

export type KnowledgeConnectorWebhook = {
  schema?: string;
  connector_instance_id?: string;
  connector_type?: string;
  connector_status?: string;
  supported: boolean;
  enabled: boolean;
  status: string;
  calibration_required?: boolean;
  policy?: Record<string, unknown>;
  state?: {
    last_sequence?: number;
    last_event_timestamp_utc?: string;
    calibration_required?: boolean;
    last_success_event?: Record<string, unknown> | null;
    last_failure_event?: Record<string, unknown> | null;
  };
  events?: Array<Record<string, unknown>>;
  governance?: Record<string, unknown>;
  error_code?: string;
};

export type KnowledgeConnectorOAuth = {
  schema?: string;
  connector_instance_id?: string;
  connector_type?: string;
  supported: boolean;
  configured?: boolean;
  status: string;
  credential_status?: string;
  required_scopes?: string[];
  granted_scopes?: string[];
  missing_scopes?: string[];
  permission_status?: string;
  automatic_refresh_supported?: boolean;
  automatic_refresh_status?: string;
  last_refresh_at_utc?: string;
  last_refresh_failure?: Record<string, unknown> | null;
  last_authorized_at_utc?: string;
  last_failure?: Record<string, unknown> | null;
  pending_transaction_count?: number;
  source_identity_preserved?: boolean;
  checkpoint_preserved?: boolean;
  remote_deletion_inferred?: false;
  governance?: Record<string, unknown>;
  error_code?: string;
};

export type KnowledgeConnectorUnsupportedResource = {
  remote_resource_id?: string;
  resource_index?: number;
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
  latest_sync?: KnowledgeConnectorSyncImpact;
  source_content_returned?: boolean;
  customer_material_mutation_executed?: boolean;
};

export type KnowledgeConnectorSemanticEvent = {
  event: string;
  reason_code?: string;
  source_label?: string;
  source_identity_fingerprint?: string;
};

export type KnowledgeConnectorSemanticRefresh = {
  status: string;
  sync_epoch_id?: string;
  event_count: number;
  changed_source_count: number;
  unchanged_source_count: number;
  affected_content_blocks: number;
  affected_facts: number;
  affected_entities: number;
  affected_behaviors: number;
  affected_scenarios: number;
  affected_regression_items: number;
  downstream: Array<{ stage: string; status: string; executed: boolean }>;
  events: KnowledgeConnectorSemanticEvent[];
  unchanged_materials_reanalyzed: boolean;
  full_project_recompute_requested: boolean;
  incremental_executor_installed: boolean;
};

export type KnowledgeConnectorSyncImpact = {
  sync_epoch_id?: string;
  status: string;
  completed_at_utc?: string;
  acl_propagation_status: string;
  acl_snapshot_count: number;
  acl_incomplete_count: number;
  semantic_refresh_status: string;
  semantic_event_count: number;
  semantic_changed_source_count: number;
  semantic_refresh?: KnowledgeConnectorSemanticRefresh;
  source_content_returned: false;
  remote_resource_identities_returned: false;
  source_refs_returned: false;
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
  metadata?: Record<string, unknown>;
  connection_profile?: KnowledgeConnectorProfile;
  auto_sync?: KnowledgeConnectorAutoSync;
  coverage?: KnowledgeConnectorCoverage;
  health?: KnowledgeConnectorHealth;
  webhook?: KnowledgeConnectorWebhook;
  oauth?: KnowledgeConnectorOAuth;
  acceptance?: KnowledgeConnectorAcceptance;
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
    health_attention_connector_count?: number;
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

export type ConfigureConnectorInput = {
  connector_type: string;
  connector_instance_id: string;
  display_name: string;
  resource_scope: string;
  status?: 'ACTIVE' | 'PAUSED' | 'DISABLED' | string;
  connection_profile: Record<string, string | undefined>;
  credential_expires_at_utc?: string;
  sync_policy?: Record<string, unknown>;
  webhook_policy?: Record<string, unknown>;
};

export type ConnectorResource = {
  resource_index: number;
  display_title?: string;
  resource_kind?: string;
  remote_object_type?: string;
  state: string;
  reason_code?: string;
  updated_at_utc?: string;
  source_updated_at?: string;
  permission_scope?: ConnectorPermissionScope;
};

export type ConnectorResourceInventory = {
  schema?: string;
  project_id: string;
  connector_instance_id: string;
  status: string;
  discovered_count: number;
  covered_count: number;
  unsupported_count: number;
  materialized_preview_count: number;
  resources: ConnectorResource[];
  preview_truncated: boolean;
  source_content_returned: false;
  raw_cursor_returned: false;
  credential_values_returned: false;
  remote_resource_identities_returned: false;
  source_refs_returned: false;
};

export type ConnectorRunSummary = KnowledgeConnectorActionResult & {
  item_count?: number;
  materialized_success_count?: number;
  unchanged_success_count?: number;
  coverage_observation_count?: number;
  sync_mode?: string;
  started_at_utc?: string;
  completed_at_utc?: string;
  raw_cursor_returned?: false;
  source_content_returned?: false;
};

export type ConnectorRunInventory = {
  project_id: string;
  connector_instance_id: string;
  runs: ConnectorRunSummary[];
  truncated: boolean;
  raw_cursor_returned: false;
  source_content_returned: false;
  credential_values_returned: false;
};

export type KnowledgeConnectorAcceptance = {
  schema?: string;
  status: string;
  acceptance_ready: boolean;
  latest_report?: Record<string, unknown> | null;
  source_content_returned: false;
  raw_cursor_returned: false;
  credential_values_returned: false;
  filesystem_path_returned: false;
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

function toPermissionScope(value: unknown): ConnectorPermissionScope | undefined {
  const row = asRecord(value);
  if (Object.keys(row).length === 0) return undefined;
  if (row.raw_remote_principals_returned !== false) {
    throw new Error('connector_permission_scope_principals_returned');
  }
  return {
    visibility: asString(row.visibility) || undefined,
    availability: asString(row.availability) || undefined,
    evidence_status: asString(row.evidence_status) || undefined,
    acl_version: asString(row.acl_version) || undefined,
    complete: typeof row.complete === 'boolean' ? row.complete : undefined,
    propagation_allowed: typeof row.propagation_allowed === 'boolean'
      ? row.propagation_allowed
      : undefined,
    raw_remote_principals_returned: false,
  };
}

function assertFalseFields(row: JsonRecord, fields: string[], label: string): void {
  if (fields.some((field) => row[field] !== false)) {
    throw new Error(`${label}缂哄皯瀹屾暣鐨勫畨鍏ㄨ瘉鏄庯紝宸叉嫆缁濆湪椤甸潰灞曠ず。`);
  }
}

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
    return '在线资料授权范围不足，请检查连接器声明的只读权限，然后重新授权。';
  }
  if (message.includes('credential') || message.includes('profile') || message.includes('app_secret') || message.includes('access_token') || message.includes('auth_mode')) {
    return '连接器信息未通过验证，请检查 Manifest 声明的授权字段。';
  }
  if (message.includes('rate') || message.includes('99991400') || status === 429) {
    return '在线连接器暂时限制了访问频率，原有资料不受影响，系统会自动重试。';
  }
  if (message.includes('transport_failed') || message.includes('api_failed') || message.includes('download_failed') || status >= 500) {
    return '暂时无法读取在线资料，原有资料不受影响，系统会自动重试。';
  }
  if (message.includes('unsupported') || message.includes('export')) {
    return '部分在线资料暂时无法读取，系统没有覆盖原有资料。请确认连接器权限或格式。';
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
    credential_status: asString(row.credential_status) || undefined,
    credential_expires_at_utc: asString(row.credential_expires_at_utc) || undefined,
    reauthorization_required: asBoolean(row.reauthorization_required),
    reauthorization_reason: asString(row.reauthorization_reason) || undefined,
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
    last_oauth_refresh: row.last_oauth_refresh
      ? {
        supported: asBoolean(asRecord(row.last_oauth_refresh).supported),
        attempted: asBoolean(asRecord(row.last_oauth_refresh).attempted),
        refreshed: asBoolean(asRecord(row.last_oauth_refresh).refreshed),
        refresh_status: asString(asRecord(row.last_oauth_refresh).refresh_status),
        credential_status: asString(asRecord(row.last_oauth_refresh).credential_status) || undefined,
        credential_expires_at_utc: asString(asRecord(row.last_oauth_refresh).credential_expires_at_utc) || undefined,
        permission_status: asString(asRecord(row.last_oauth_refresh).permission_status) || undefined,
        credential_values_returned: false,
        source_identity_preserved: asBoolean(asRecord(row.last_oauth_refresh).source_identity_preserved),
        checkpoint_preserved: asBoolean(asRecord(row.last_oauth_refresh).checkpoint_preserved),
        remote_deletion_inferred: false,
      }
      : undefined,
    refresh_interval_seconds: asNumber(row.refresh_interval_seconds),
    maintenance_required_by_user: asBoolean(row.maintenance_required_by_user),
    checkpoint_recovery_is_automatic: asBoolean(row.checkpoint_recovery_is_automatic),
    stale_writer_fencing_is_automatic: asBoolean(row.stale_writer_fencing_is_automatic),
    raw_error_returned: asBoolean(row.raw_error_returned),
  };
}

function toUnsupportedResource(value: unknown): KnowledgeConnectorUnsupportedResource {
  const row = asRecord(value);
  assertFalseFields(
    row,
    ['content_materialized', 'source_occurrence_created', 'customer_source_modified'],
    '涓嶆敮鎸佺殑璧勬枡鎽樿',
  );
  return {
    remote_resource_id: asString(row.remote_resource_id) || undefined,
    resource_index: asNumber(row.resource_index),
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

function assertRemoteLifecycleSafety(row: JsonRecord): void {
  const requiredFalse = [
    'remote_deletion_inferred',
    'permission_loss_inferred',
    'customer_material_mutation_executed',
    'remote_resource_identities_returned',
    'source_refs_returned',
  ];
  if (requiredFalse.some((field) => row[field] !== false)) {
    throw new Error('远端资料状态缺少完整的只读安全证明，已拒绝在页面展示。');
  }
  if (row.historical_source_bytes_retained !== true) {
    throw new Error('远端资料状态未证明历史内容仍被保留，已拒绝在页面展示。');
  }
}

function toRemoteLifecycle(value: unknown): KnowledgeConnectorRemoteLifecycle {
  const row = asRecord(value);
  assertRemoteLifecycleSafety(row);
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
  assertFalseFields(
    row,
    ['source_content_returned', 'customer_material_mutation_executed'],
    '璧勬枡瑕嗙洊鎶ュ憡',
  );
  return {
    status: asString(row.status) || 'NOT_AVAILABLE',
    complete: asBoolean(row.complete),
    discovered_count: asNumber(row.discovered_count) || 0,
    covered_count: asNumber(row.covered_count) || 0,
    unsupported_count: asNumber(row.unsupported_count) || 0,
    coverage_ratio: asNumber(row.coverage_ratio) || 0,
    unsupported_resources: asArray(row.unsupported_resources)
      .map(toUnsupportedResource)
      .filter((item) => Boolean(
        item.resource_index !== undefined || item.display_title || item.resource_kind,
      )),
    unsupported_resources_truncated: asBoolean(row.unsupported_resources_truncated),
    last_sync_epoch_id: asString(row.last_sync_epoch_id) || undefined,
    last_completed_at_utc: asString(row.last_completed_at_utc) || undefined,
    remote_lifecycle: row.remote_lifecycle ? toRemoteLifecycle(row.remote_lifecycle) : undefined,
    latest_sync: row.latest_sync ? toSyncImpact(row.latest_sync) : undefined,
    source_content_returned: asBoolean(row.source_content_returned),
    customer_material_mutation_executed: asBoolean(row.customer_material_mutation_executed),
  };
}

function toConnectorHealth(value: unknown): KnowledgeConnectorHealth {
  const row = asRecord(value);
  assertFalseFields(
    row,
    [
      'source_content_returned',
      'credentials_returned',
      'raw_cursor_returned',
      'customer_material_mutation_executed',
    ],
    '连接器健康状态',
  );
  const freshness = asRecord(row.freshness);
  const metrics = asRecord(row.metrics);
  const evidence = asRecord(row.evidence);
  return {
    schema: asString(row.schema) || undefined,
    status: asString(row.status) || 'NOT_MEASURED',
    recommended_action: asString(row.recommended_action) || 'REVIEW_CONNECTOR',
    attention_reasons: asArray(row.attention_reasons).map(asString).filter(Boolean),
    credential_status: asString(row.credential_status) || undefined,
    reauthorization_required: asBoolean(row.reauthorization_required),
    reauthorization_reason: asString(row.reauthorization_reason) || undefined,
    freshness: {
      status: asString(freshness.status) || 'UNKNOWN',
      last_successful_sync_at_utc: asString(freshness.last_successful_sync_at_utc) || undefined,
      age_seconds: asNumber(freshness.age_seconds),
      refresh_interval_seconds: asNumber(freshness.refresh_interval_seconds),
      stale_after_seconds: asNumber(freshness.stale_after_seconds),
    },
    webhook: row.webhook ? toWebhook(row.webhook) : undefined,
    oauth: row.oauth ? toOauth(row.oauth) : undefined,
    metrics: {
      last_attempt_at_utc: asString(metrics.last_attempt_at_utc) || undefined,
      discovered_resource_count: asNumber(metrics.discovered_resource_count) || 0,
      covered_resource_count: asNumber(metrics.covered_resource_count) || 0,
      unsupported_resource_count: asNumber(metrics.unsupported_resource_count) || 0,
      coverage_ratio: asNumber(metrics.coverage_ratio),
      failure_count: asNumber(metrics.failure_count) || 0,
      retry_count: asNumber(metrics.retry_count) || 0,
      materialized_resource_count: asNumber(metrics.materialized_resource_count) || 0,
      unchanged_resource_count: asNumber(metrics.unchanged_resource_count) || 0,
      unchanged_reuse_ratio: asNumber(metrics.unchanged_reuse_ratio),
      semantic_refresh_status: asString(metrics.semantic_refresh_status) || 'NOT_RECORDED',
      semantic_event_count: asNumber(metrics.semantic_event_count) || 0,
      semantic_changed_source_count: asNumber(metrics.semantic_changed_source_count) || 0,
      acl_propagation_status: asString(metrics.acl_propagation_status) || 'NOT_RECORDED',
    },
    evidence: {
      source: asString(evidence.source) || 'connector_sync_receipt',
      measured: asBoolean(evidence.measured),
      coverage_receipt_present: asBoolean(evidence.coverage_receipt_present),
      latest_sync_receipt_present: asBoolean(evidence.latest_sync_receipt_present),
      checked_at_utc: asString(evidence.checked_at_utc),
    },
    source_content_returned: false,
    credentials_returned: false,
    raw_cursor_returned: false,
    customer_material_mutation_executed: false,
  };
}

function toWebhook(value: unknown): KnowledgeConnectorWebhook {
  const row = asRecord(value);
  const state = row.state ? asRecord(row.state) : undefined;
  return {
    schema: asString(row.schema) || undefined,
    connector_instance_id: asString(row.connector_instance_id) || undefined,
    connector_type: asString(row.connector_type) || undefined,
    connector_status: asString(row.connector_status) || undefined,
    supported: asBoolean(row.supported),
    enabled: asBoolean(row.enabled),
    status: asString(row.status) || 'NOT_AVAILABLE',
    calibration_required: asBoolean(row.calibration_required)
      || asBoolean(state?.calibration_required),
    policy: row.policy ? asRecord(row.policy) : undefined,
    state: state
      ? {
        last_sequence: asNumber(state.last_sequence),
        last_event_timestamp_utc: asString(state.last_event_timestamp_utc) || undefined,
        calibration_required: asBoolean(state.calibration_required),
        last_success_event: state.last_success_event ? asRecord(state.last_success_event) : null,
        last_failure_event: state.last_failure_event ? asRecord(state.last_failure_event) : null,
      }
      : undefined,
    events: asArray(row.events).map(asRecord),
    governance: row.governance ? asRecord(row.governance) : undefined,
    error_code: asString(row.error_code) || undefined,
  };
}

function toOauth(value: unknown): KnowledgeConnectorOAuth {
  const row = asRecord(value);
  assertFalseFields(
    row,
    [
      'authorization_code_returned',
      'access_token_returned',
      'refresh_token_returned',
      'credential_values_returned',
      'remote_deletion_inferred',
    ],
    'OAuth 授权状态',
  );
  return {
    schema: asString(row.schema) || undefined,
    connector_instance_id: asString(row.connector_instance_id) || undefined,
    connector_type: asString(row.connector_type) || undefined,
    supported: asBoolean(row.supported),
    configured: asBoolean(row.configured),
    status: asString(row.status) || 'NOT_AVAILABLE',
    credential_status: asString(row.credential_status) || undefined,
    required_scopes: asArray(row.required_scopes).map(asString).filter(Boolean),
    granted_scopes: asArray(row.granted_scopes).map(asString).filter(Boolean),
    missing_scopes: asArray(row.missing_scopes).map(asString).filter(Boolean),
    permission_status: asString(row.permission_status) || undefined,
    automatic_refresh_supported: asBoolean(row.automatic_refresh_supported),
    automatic_refresh_status: asString(row.automatic_refresh_status) || undefined,
    last_refresh_at_utc: asString(row.last_refresh_at_utc) || undefined,
    last_refresh_failure: row.last_refresh_failure
      ? asRecord(row.last_refresh_failure)
      : null,
    last_authorized_at_utc: asString(row.last_authorized_at_utc) || undefined,
    last_failure: row.last_failure ? asRecord(row.last_failure) : null,
    pending_transaction_count: asNumber(row.pending_transaction_count),
    source_identity_preserved: asBoolean(row.source_identity_preserved),
    checkpoint_preserved: asBoolean(row.checkpoint_preserved),
    remote_deletion_inferred: false,
    governance: row.governance ? asRecord(row.governance) : undefined,
    error_code: asString(row.error_code) || undefined,
  };
}

function toSemanticRefresh(value: unknown): KnowledgeConnectorSemanticRefresh {
  const row = asRecord(value);
  const events = asArray(row.events).map((event) => {
    const item = asRecord(event);
    return {
      event: asString(item.event),
      reason_code: asString(item.reason_code) || undefined,
      source_label: asString(item.source_label) || undefined,
      source_identity_fingerprint: asString(item.source_identity_fingerprint) || undefined,
    };
  });
  return {
    status: asString(row.status) || 'NOT_RECORDED',
    sync_epoch_id: asString(row.sync_epoch_id) || undefined,
    event_count: asNumber(row.event_count) || 0,
    changed_source_count: asNumber(row.changed_source_count) || 0,
    unchanged_source_count: asNumber(row.unchanged_source_count) || 0,
    affected_content_blocks: asNumber(row.affected_content_blocks) || 0,
    affected_facts: asNumber(row.affected_facts) || 0,
    affected_entities: asNumber(row.affected_entities) || 0,
    affected_behaviors: asNumber(row.affected_behaviors) || 0,
    affected_scenarios: asNumber(row.affected_scenarios) || 0,
    affected_regression_items: asNumber(row.affected_regression_items) || 0,
    downstream: asArray(row.downstream).map((stage) => {
      const item = asRecord(stage);
      return {
        stage: asString(item.stage),
        status: asString(item.status) || 'NOT_RECORDED',
        executed: asBoolean(item.executed),
      };
    }),
    events,
    unchanged_materials_reanalyzed: asBoolean(row.unchanged_materials_reanalyzed),
    full_project_recompute_requested: asBoolean(row.full_project_recompute_requested),
    incremental_executor_installed: asBoolean(row.incremental_executor_installed),
  };
}

function toSyncImpact(value: unknown): KnowledgeConnectorSyncImpact {
  const row = asRecord(value);
  return {
    sync_epoch_id: asString(row.sync_epoch_id) || undefined,
    status: asString(row.status) || 'NOT_RECORDED',
    completed_at_utc: asString(row.completed_at_utc) || undefined,
    acl_propagation_status: asString(row.acl_propagation_status) || 'NOT_RECORDED',
    acl_snapshot_count: asNumber(row.acl_snapshot_count) || 0,
    acl_incomplete_count: asNumber(row.acl_incomplete_count) || 0,
    semantic_refresh_status: asString(row.semantic_refresh_status) || 'NOT_RECORDED',
    semantic_event_count: asNumber(row.semantic_event_count) || 0,
    semantic_changed_source_count: asNumber(row.semantic_changed_source_count) || 0,
    semantic_refresh: row.semantic_refresh ? toSemanticRefresh(row.semantic_refresh) : undefined,
    source_content_returned: false,
    remote_resource_identities_returned: false,
    source_refs_returned: false,
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
    metadata: asRecord(row.metadata),
    connection_profile: row.connection_profile ? toProfile(row.connection_profile) : undefined,
    auto_sync: row.auto_sync ? toAutoSync(row.auto_sync) : undefined,
    coverage: row.coverage ? toCoverage(row.coverage) : undefined,
    health: row.health ? toConnectorHealth(row.health) : undefined,
    webhook: row.webhook ? toWebhook(row.webhook) : undefined,
    oauth: row.oauth ? toOauth(row.oauth) : undefined,
    acceptance: row.acceptance ? toAcceptance(row.acceptance) : undefined,
  };
}

function toAcceptance(value: unknown): KnowledgeConnectorAcceptance {
  const row = asRecord(value);
  assertFalseFields(
    row,
    [
      'source_content_returned',
      'raw_cursor_returned',
      'credential_values_returned',
      'filesystem_path_returned',
    ],
    '璧勬枡楠屾敹鎶ュ憡',
  );
  const latest = row.latest_report ? asRecord(row.latest_report) : null;
  const latestReport = latest
    ? Object.fromEntries(
      [
        'report_id',
        'acceptance_id',
        'profile',
        'verdict',
        'acceptance_ready',
        'started_at_utc',
        'completed_at_utc',
      ].flatMap((field) => (field in latest ? [[field, latest[field]]] : [])),
    )
    : null;
  if (latest && 'summary' in latest) {
    const summary = asRecord(latest.summary);
    if (Object.keys(summary).length) {
      latestReport!.summary = Object.fromEntries(
        [
          'check_count',
          'blocker_failure_count',
          'executed_run_count',
          'required_run_count',
          'maximum_run_duration_seconds',
          'minimum_coverage_ratio',
          'maximum_discovered_resource_count',
        ].flatMap((field) => (field in summary ? [[field, summary[field]]] : [])),
      );
    }
  }
  return {
    schema: asString(row.schema) || undefined,
    status: asString(row.status) || 'NOT_RUN',
    acceptance_ready: asBoolean(row.acceptance_ready),
    latest_report: latestReport,
    source_content_returned: false,
    raw_cursor_returned: false,
    credential_values_returned: false,
    filesystem_path_returned: false,
  };
}

function toManifest(value: unknown): ConnectorManifest {
  const row = asRecord(value);
  return {
    schema: asString(row.schema) || undefined,
    connector_type: asString(row.connector_type),
    display_name: asString(row.display_name),
    category: asString(row.category),
    version: asString(row.version),
    auth_modes: asArray(row.auth_modes).map(asString).filter(Boolean),
    scope_schema: asRecord(row.scope_schema),
    quick_connect_schema: row.quick_connect_schema
      ? {
        input_type: asString(asRecord(row.quick_connect_schema).input_type) || undefined,
        scope_field: asString(asRecord(row.quick_connect_schema).scope_field) || undefined,
        priority: asNumber(asRecord(row.quick_connect_schema).priority),
      }
      : undefined,
    entrypoint_evidence: row.entrypoint_evidence
      ? {
        content_types: asArray(asRecord(row.entrypoint_evidence).content_types)
          .map(asString)
          .filter(Boolean),
        document_shapes: asArray(asRecord(row.entrypoint_evidence).document_shapes)
          .map(asString)
          .filter(Boolean),
        host_suffixes: asArray(asRecord(row.entrypoint_evidence).host_suffixes)
          .map(asString)
          .filter(Boolean),
        path_suffixes: asArray(asRecord(row.entrypoint_evidence).path_suffixes)
          .map(asString)
          .filter(Boolean),
      }
      : undefined,
    supported_resource_types: asArray(row.supported_resource_types).map(asString).filter(Boolean),
    sync_modes: asArray(row.sync_modes).map(asString).filter(Boolean),
    webhook_supported: asBoolean(row.webhook_supported),
    local_runner_supported: asBoolean(row.local_runner_supported),
    local_runner_required: asBoolean(row.local_runner_required),
    read_only: asBoolean(row.read_only),
    credential_fields: asArray(row.credential_fields).map((field) => {
      const item = asRecord(field);
      return {
        name: asString(item.name),
        display_name: asString(item.display_name) || undefined,
        field_type: asString(item.field_type),
        required: asBoolean(item.required),
        secret: asBoolean(item.secret),
        description: asString(item.description) || undefined,
        auth_modes: asArray(item.auth_modes).map(asString).filter(Boolean),
      };
    }).filter((field) => Boolean(field.name)),
    capability_contract_version: asString(row.capability_contract_version),
    webhook_policy_schema: row.webhook_policy_schema
      ? asRecord(row.webhook_policy_schema)
      : undefined,
    oauth_schema: row.oauth_schema
      ? asRecord(row.oauth_schema)
      : undefined,
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
      health_attention_connector_count: asNumber(summary.health_attention_connector_count),
      remote_absent_resource_count: asNumber(summary.remote_absent_resource_count),
      remote_unconfirmed_missing_resource_count: asNumber(summary.remote_unconfirmed_missing_resource_count),
      remote_retired_resource_count: asNumber(summary.remote_retired_resource_count),
    },
    governance: asRecord(data.governance),
  };
}

export async function listConnectorTypes(): Promise<ConnectorTypeCatalog> {
  const payload = await connectorRequest('/api/v1/connector-types');
  const data = asRecord(payload.data);
  return {
    schema: asString(data.schema),
    connector_types: asArray(data.connector_types)
      .map(toManifest)
      .filter((manifest) => Boolean(manifest.connector_type)),
    governance: asRecord(data.governance),
  };
}

export async function getConnectorType(connectorType: string): Promise<ConnectorManifest> {
  const type = connectorType.trim();
  if (!type) throw new Error('connector_type_required');
  const payload = await connectorRequest(`/api/v1/connector-types/${encodeURIComponent(type)}`);
  const row = asRecord(asRecord(payload.data).connector_type);
  const manifest = toManifest(row);
  if (!manifest.connector_type || !manifest.display_name) throw new Error('connector_manifest_incomplete');
  return manifest;
}

export async function preflightConnectorSource(
  projectId: string,
  url: string,
): Promise<ConnectorSourcePreflight> {
  const sourceUrl = url.trim();
  if (!sourceUrl) throw new Error('请先粘贴一个在线资料入口 URL。');
  const payload = await connectorRequest(
    projectConnectorPath(projectId, '/source-preflight'),
    {
      method: 'POST',
      body: JSON.stringify({ url: sourceUrl }),
    },
  );
  const data = asRecord(payload.data);
  const governance = asRecord(data.governance);
  assertFalseFields(
    governance,
    [
      'request_body_sent',
      'write_performed',
      'source_content_returned',
      'response_body_persisted',
      'credentials_returned',
      'raw_cursor_returned',
    ],
    '来源入口预检',
  );
  return {
    schema: asString(data.schema) || undefined,
    project_id: asString(data.project_id) || projectId,
    status: asString(data.status) || 'NO_QUICK_CONNECTOR',
    recommended_connector_type: asString(data.recommended_connector_type),
    candidates: asArray(data.candidates).map((value) => {
      const row = asRecord(value);
      return {
        connector_type: asString(row.connector_type),
        display_name: asString(row.display_name),
        category: asString(row.category),
        scope_field: asString(row.scope_field),
        match_status: asString(row.match_status) || 'AVAILABLE',
        reason_code: asString(row.reason_code),
        evidence: asArray(row.evidence).map(asString).filter(Boolean),
        priority: asNumber(row.priority) ?? 100,
        requires_user_confirmation: asBoolean(row.requires_user_confirmation),
      };
    }).filter((candidate) => Boolean(candidate.connector_type)),
    observation: {
      http_status: asNumber(asRecord(data.observation).http_status) ?? 0,
      content_type: asString(asRecord(data.observation).content_type),
      response_bytes_read: asNumber(asRecord(data.observation).response_bytes_read) ?? 0,
      response_fingerprint: asString(asRecord(data.observation).response_fingerprint),
      document_shapes: asArray(asRecord(data.observation).document_shapes)
        .map(asString)
        .filter(Boolean),
      path_suffix_observed: asBoolean(asRecord(data.observation).path_suffix_observed),
      redirected: asBoolean(asRecord(data.observation).redirected),
      final_host_fingerprint: asString(asRecord(data.observation).final_host_fingerprint),
    },
    governance,
  };
}

export async function getKnowledgeConnector(
  projectId: string,
  connectorId: string,
): Promise<KnowledgeConnectorRecord> {
  const connector = connectorId.trim();
  if (!connector) throw new Error('connector_instance_id_required');
  const payload = await connectorRequest(
    projectConnectorPath(projectId, `/${encodeURIComponent(connector)}`),
  );
  return toConnector(payload.data);
}

export async function configureKnowledgeConnector(
  projectId: string,
  input: ConfigureConnectorInput,
): Promise<KnowledgeConnectorRecord> {
  if (!input.connector_type.trim()) throw new Error('connector_type_required');
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

export async function patchKnowledgeConnector(
  projectId: string,
  connectorId: string,
  patch: Partial<Omit<ConfigureConnectorInput, 'connector_type' | 'connector_instance_id'>> & {
    connector_type?: string;
  },
): Promise<KnowledgeConnectorRecord> {
  const connector = connectorId.trim();
  if (!connector) throw new Error('connector_instance_id_required');
  const payload = await connectorRequest(
    projectConnectorPath(projectId, `/${encodeURIComponent(connector)}`),
    { method: 'PATCH', body: JSON.stringify(patch) },
  );
  const data = asRecord(payload.data);
  return toConnector({
    ...asRecord(data.connector_instance),
    connection_profile: data.connection_profile,
  });
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
  action: 'test' | 'sync' | 'pause' | 'resume' | 'reauthorize',
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

export function pauseKnowledgeConnector(
  projectId: string,
  connectorId: string,
): Promise<KnowledgeConnectorActionResult> {
  return connectorAction(projectId, connectorId, 'pause');
}

export function resumeKnowledgeConnector(
  projectId: string,
  connectorId: string,
): Promise<KnowledgeConnectorActionResult> {
  return connectorAction(projectId, connectorId, 'resume');
}

export function reauthorizeKnowledgeConnector(
  projectId: string,
  connectorId: string,
  body: JsonRecord = {},
): Promise<KnowledgeConnectorActionResult> {
  return connectorAction(projectId, connectorId, 'reauthorize', body);
}

export type KnowledgeConnectorOAuthStart = {
  schema?: string;
  connector_instance_id: string;
  transaction_id: string;
  authorization_url: string;
  requested_scopes: string[];
  expires_at_utc?: string;
  state_returned_only_inside_authorization_url: boolean;
  pkce_method: string;
  state_persisted_as_hash: boolean;
  credential_values_returned: false;
  source_content_returned: false;
};

export async function startKnowledgeConnectorOAuth(
  projectId: string,
  connectorId: string,
  additionalScopes: string[] = [],
): Promise<KnowledgeConnectorOAuthStart> {
  const connector = connectorId.trim();
  if (!connector) throw new Error('connector_instance_id_required');
  const payload = await connectorRequest(
    projectConnectorPath(projectId, `/${encodeURIComponent(connector)}/oauth/start`),
    {
      method: 'POST',
      body: JSON.stringify({ additional_scopes: additionalScopes }),
    },
  );
  const row = asRecord(payload.data);
  return {
    schema: asString(row.schema) || undefined,
    connector_instance_id: asString(row.connector_instance_id) || connector,
    transaction_id: asString(row.transaction_id),
    authorization_url: asString(row.authorization_url),
    requested_scopes: asArray(row.requested_scopes).map(asString).filter(Boolean),
    expires_at_utc: asString(row.expires_at_utc) || undefined,
    state_returned_only_inside_authorization_url: asBoolean(
      row.state_returned_only_inside_authorization_url,
    ),
    pkce_method: asString(row.pkce_method),
    state_persisted_as_hash: asBoolean(row.state_persisted_as_hash),
    credential_values_returned: false,
    source_content_returned: false,
  };
}

export async function listConnectorResources(
  projectId: string,
  connectorId: string,
): Promise<ConnectorResourceInventory> {
  const connector = connectorId.trim();
  if (!connector) throw new Error('connector_instance_id_required');
  const payload = await connectorRequest(
    projectConnectorPath(projectId, `/${encodeURIComponent(connector)}/resources`),
  );
  const data = asRecord(payload.data);
  assertFalseFields(
    data,
    [
      'source_content_returned',
      'raw_cursor_returned',
      'credential_values_returned',
      'remote_resource_identities_returned',
      'source_refs_returned',
    ],
    '璧勬枡棰勮',
  );
  return {
    schema: asString(data.schema) || undefined,
    project_id: asString(data.project_id) || projectId,
    connector_instance_id: asString(data.connector_instance_id) || connector,
    status: asString(data.status) || 'NOT_AVAILABLE',
    discovered_count: asNumber(data.discovered_count) || 0,
    covered_count: asNumber(data.covered_count) || 0,
    unsupported_count: asNumber(data.unsupported_count) || 0,
    materialized_preview_count: asNumber(data.materialized_preview_count) || 0,
    resources: asArray(data.resources).map((value, index) => {
      const row = asRecord(value);
      return {
        resource_index: asNumber(row.resource_index) ?? index,
        display_title: asString(row.display_title) || undefined,
        resource_kind: asString(row.resource_kind) || undefined,
        remote_object_type: asString(row.remote_object_type) || undefined,
        state: asString(row.state) || 'UNKNOWN',
        reason_code: asString(row.reason_code) || undefined,
        updated_at_utc: asString(row.updated_at_utc) || undefined,
        source_updated_at: asString(row.source_updated_at) || undefined,
        permission_scope: toPermissionScope(row.permission_scope),
      };
    }),
    preview_truncated: asBoolean(data.preview_truncated),
    source_content_returned: false,
    raw_cursor_returned: false,
    credential_values_returned: false,
    remote_resource_identities_returned: false,
    source_refs_returned: false,
  };
}

export async function getConnectorCoverage(
  projectId: string,
  connectorId: string,
): Promise<KnowledgeConnectorCoverage> {
  const connector = connectorId.trim();
  if (!connector) throw new Error('connector_instance_id_required');
  const payload = await connectorRequest(
    projectConnectorPath(projectId, `/${encodeURIComponent(connector)}/coverage`),
  );
  return toCoverage(payload.data);
}

export async function listConnectorRuns(
  projectId: string,
  connectorId: string,
): Promise<ConnectorRunInventory> {
  const connector = connectorId.trim();
  if (!connector) throw new Error('connector_instance_id_required');
  const payload = await connectorRequest(
    projectConnectorPath(projectId, `/${encodeURIComponent(connector)}/runs`),
  );
  const data = asRecord(payload.data);
  assertFalseFields(
    data,
    ['raw_cursor_returned', 'source_content_returned', 'credential_values_returned'],
    '同步运行记录',
  );
  return {
    project_id: asString(data.project_id) || projectId,
    connector_instance_id: asString(data.connector_instance_id) || connector,
    runs: asArray(data.runs).map((value) => {
      const row = asRecord(value);
      return {
        sync_epoch_id: asString(row.sync_epoch_id) || undefined,
        connector_instance_id: asString(row.connector_instance_id) || connector,
        sync_mode: asString(row.sync_mode) || undefined,
        status: asString(row.status) || undefined,
        item_count: asNumber(row.item_count),
        success_count: asNumber(row.success_count),
        materialized_success_count: asNumber(row.materialized_success_count),
        unchanged_success_count: asNumber(row.unchanged_success_count),
        coverage_observation_count: asNumber(row.coverage_observation_count),
        knowledge_coverage_status: asString(row.knowledge_coverage_status) || undefined,
        failure_count: asNumber(row.failure_count),
        retired_count: asNumber(row.retired_count),
        cursor_checkpoint_committed: typeof row.cursor_checkpoint_committed === 'boolean'
          ? row.cursor_checkpoint_committed
          : undefined,
        started_at_utc: asString(row.started_at_utc) || undefined,
        completed_at_utc: asString(row.completed_at_utc) || undefined,
        raw_cursor_returned: false,
        source_content_returned: false,
      } as ConnectorRunSummary;
    }),
    truncated: asBoolean(data.truncated),
    raw_cursor_returned: false,
    source_content_returned: false,
    credential_values_returned: false,
  };
}

export async function getConnectorAcceptance(
  projectId: string,
  connectorId: string,
): Promise<KnowledgeConnectorAcceptance> {
  const connector = connectorId.trim();
  if (!connector) throw new Error('connector_instance_id_required');
  const payload = await connectorRequest(
    projectConnectorPath(projectId, `/${encodeURIComponent(connector)}/acceptance`),
  );
  return toAcceptance(payload.data);
}

export async function connectKnowledgeConnector(
  projectId: string,
  input: ConfigureConnectorInput,
): Promise<{ connector: KnowledgeConnectorRecord; connection: KnowledgeConnectorActionResult; sync: KnowledgeConnectorActionResult }> {
  const connector = await configureKnowledgeConnector(projectId, input);
  const connection = await testKnowledgeConnector(projectId, input.connector_instance_id);
  const sync = await syncKnowledgeConnector(projectId, input.connector_instance_id);
  return { connector, connection, sync };
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
