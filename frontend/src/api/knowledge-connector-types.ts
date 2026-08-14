/**
 * 在线资料连接器（knowledge-connectors）的纯类型定义，无运行时依赖。
 * 拆分自 api/knowledge-connectors.ts；由 knowledge-connectors.ts 再导出。
 */

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
