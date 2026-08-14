/**
 * 在线资料连接器的解析与请求基础设施。
 * 拆分自 api/knowledge-connectors.ts：承载安全断言、字段收敛、
 * 只读契约校验与请求传输；由 knowledge-connectors.ts 的业务接口复用。
 */
import { currentToken, getSession } from './client';
import { asArray, asOptionalNumber, asRecord, asString } from '../lib/value-guards';
import type { KnowledgeConnectorProfile, ConnectorManifest, ConnectorPermissionScope, KnowledgeConnectorAutoSync, KnowledgeConnectorHealth, KnowledgeConnectorWebhook, KnowledgeConnectorOAuth, KnowledgeConnectorUnsupportedResource, KnowledgeConnectorRemoteLifecycle, KnowledgeConnectorCoverage, KnowledgeConnectorSemanticRefresh, KnowledgeConnectorSyncImpact, KnowledgeConnectorRecord, KnowledgeConnectorAcceptance } from './knowledge-connector-types';

type JsonRecord = Record<string, unknown>;

export const asBoolean = (value: unknown): boolean => value === true;

export function toPermissionScope(value: unknown): ConnectorPermissionScope | undefined {
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

export function assertFalseFields(row: JsonRecord, fields: string[], label: string): void {
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

export async function connectorRequest(path: string, init?: RequestInit): Promise<JsonRecord> {
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

export function projectConnectorPath(projectId: string, suffix = ''): string {
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
    failure_count: asOptionalNumber(row.failure_count) || 0,
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
    refresh_interval_seconds: asOptionalNumber(row.refresh_interval_seconds),
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
    resource_index: asOptionalNumber(row.resource_index),
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
    present_count: asOptionalNumber(row.present_count) || 0,
    absent_count: asOptionalNumber(row.absent_count) || 0,
    unconfirmed_missing_count: asOptionalNumber(row.unconfirmed_missing_count) || 0,
    retirement_eligible_count: asOptionalNumber(row.retirement_eligible_count) || 0,
    retired_count: asOptionalNumber(row.retired_count) || 0,
    renamed_resource_count: asOptionalNumber(row.renamed_resource_count) || 0,
    moved_resource_count: asOptionalNumber(row.moved_resource_count) || 0,
    reappeared_resource_count: asOptionalNumber(row.reappeared_resource_count) || 0,
    retire_after_complete_snapshots: asOptionalNumber(row.retire_after_complete_snapshots) || 0,
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

export function toCoverage(value: unknown): KnowledgeConnectorCoverage {
  const row = asRecord(value);
  assertFalseFields(
    row,
    ['source_content_returned', 'customer_material_mutation_executed'],
    '璧勬枡瑕嗙洊鎶ュ憡',
  );
  return {
    status: asString(row.status) || 'NOT_AVAILABLE',
    complete: asBoolean(row.complete),
    discovered_count: asOptionalNumber(row.discovered_count) || 0,
    covered_count: asOptionalNumber(row.covered_count) || 0,
    unsupported_count: asOptionalNumber(row.unsupported_count) || 0,
    coverage_ratio: asOptionalNumber(row.coverage_ratio) || 0,
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
      age_seconds: asOptionalNumber(freshness.age_seconds),
      refresh_interval_seconds: asOptionalNumber(freshness.refresh_interval_seconds),
      stale_after_seconds: asOptionalNumber(freshness.stale_after_seconds),
    },
    webhook: row.webhook ? toWebhook(row.webhook) : undefined,
    oauth: row.oauth ? toOauth(row.oauth) : undefined,
    metrics: {
      last_attempt_at_utc: asString(metrics.last_attempt_at_utc) || undefined,
      discovered_resource_count: asOptionalNumber(metrics.discovered_resource_count) || 0,
      covered_resource_count: asOptionalNumber(metrics.covered_resource_count) || 0,
      unsupported_resource_count: asOptionalNumber(metrics.unsupported_resource_count) || 0,
      coverage_ratio: asOptionalNumber(metrics.coverage_ratio),
      failure_count: asOptionalNumber(metrics.failure_count) || 0,
      retry_count: asOptionalNumber(metrics.retry_count) || 0,
      materialized_resource_count: asOptionalNumber(metrics.materialized_resource_count) || 0,
      unchanged_resource_count: asOptionalNumber(metrics.unchanged_resource_count) || 0,
      unchanged_reuse_ratio: asOptionalNumber(metrics.unchanged_reuse_ratio),
      semantic_refresh_status: asString(metrics.semantic_refresh_status) || 'NOT_RECORDED',
      semantic_event_count: asOptionalNumber(metrics.semantic_event_count) || 0,
      semantic_changed_source_count: asOptionalNumber(metrics.semantic_changed_source_count) || 0,
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
        last_sequence: asOptionalNumber(state.last_sequence),
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
    pending_transaction_count: asOptionalNumber(row.pending_transaction_count),
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
    event_count: asOptionalNumber(row.event_count) || 0,
    changed_source_count: asOptionalNumber(row.changed_source_count) || 0,
    unchanged_source_count: asOptionalNumber(row.unchanged_source_count) || 0,
    affected_content_blocks: asOptionalNumber(row.affected_content_blocks) || 0,
    affected_facts: asOptionalNumber(row.affected_facts) || 0,
    affected_entities: asOptionalNumber(row.affected_entities) || 0,
    affected_behaviors: asOptionalNumber(row.affected_behaviors) || 0,
    affected_scenarios: asOptionalNumber(row.affected_scenarios) || 0,
    affected_regression_items: asOptionalNumber(row.affected_regression_items) || 0,
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
    acl_snapshot_count: asOptionalNumber(row.acl_snapshot_count) || 0,
    acl_incomplete_count: asOptionalNumber(row.acl_incomplete_count) || 0,
    semantic_refresh_status: asString(row.semantic_refresh_status) || 'NOT_RECORDED',
    semantic_event_count: asOptionalNumber(row.semantic_event_count) || 0,
    semantic_changed_source_count: asOptionalNumber(row.semantic_changed_source_count) || 0,
    semantic_refresh: row.semantic_refresh ? toSemanticRefresh(row.semantic_refresh) : undefined,
    source_content_returned: false,
    remote_resource_identities_returned: false,
    source_refs_returned: false,
  };
}

export function toConnector(value: unknown): KnowledgeConnectorRecord {
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

export function toAcceptance(value: unknown): KnowledgeConnectorAcceptance {
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

export function toManifest(value: unknown): ConnectorManifest {
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
        priority: asOptionalNumber(asRecord(row.quick_connect_schema).priority),
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
