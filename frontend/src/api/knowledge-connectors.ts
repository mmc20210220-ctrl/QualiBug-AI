/**
 * 在线资料连接器业务接口 + 对外再导出桶。
 * 拆分自原 api/knowledge-connectors.ts：纯类型迁至 knowledge-connector-types.ts，
 * 解析/请求基础设施迁至 knowledge-connector-parsers.ts；本文件保持原导出不变。
 */
import { asArray, asOptionalNumber, asRecord, asString } from '../lib/value-guards';
import { asBoolean, assertFalseFields, connectorRequest, projectConnectorPath, toAcceptance, toConnector, toCoverage, toManifest, toPermissionScope } from './knowledge-connector-parsers';
import type { ConnectorManifest, ConnectorTypeCatalog, ConnectorSourcePreflight, KnowledgeConnectorCoverage, KnowledgeConnectorRecord, KnowledgeConnectorInventory, ConfigureFeishuConnectorInput, ConfigureConnectorInput, ConnectorResourceInventory, ConnectorRunSummary, ConnectorRunInventory, KnowledgeConnectorAcceptance, KnowledgeConnectorActionResult, ConnectFeishuKnowledgeResult, KnowledgeConnectorOAuthStart } from './knowledge-connector-types';

type JsonRecord = Record<string, unknown>;

export type {
  KnowledgeConnectorProfile,
  ConnectorCredentialField,
  ConnectorManifest,
  ConnectorPermissionScope,
  ConnectorTypeCatalog,
  ConnectorSourcePreflightCandidate,
  ConnectorSourcePreflight,
  KnowledgeConnectorAutoSync,
  KnowledgeConnectorHealth,
  KnowledgeConnectorWebhook,
  KnowledgeConnectorOAuth,
  KnowledgeConnectorUnsupportedResource,
  KnowledgeConnectorRemoteLifecycle,
  KnowledgeConnectorCoverage,
  KnowledgeConnectorSemanticEvent,
  KnowledgeConnectorSemanticRefresh,
  KnowledgeConnectorSyncImpact,
  KnowledgeConnectorRecord,
  KnowledgeConnectorInventory,
  ConfigureFeishuConnectorInput,
  ConfigureConnectorInput,
  ConnectorResource,
  ConnectorResourceInventory,
  ConnectorRunSummary,
  ConnectorRunInventory,
  KnowledgeConnectorAcceptance,
  KnowledgeConnectorActionResult,
  ConnectFeishuKnowledgeResult,
  KnowledgeConnectorOAuthStart,
} from './knowledge-connector-types';

export async function listKnowledgeConnectors(projectId: string): Promise<KnowledgeConnectorInventory> {
  const payload = await connectorRequest(projectConnectorPath(projectId));
  const data = asRecord(payload.data);
  const summary = asRecord(data.summary);
  return {
    project_id: asString(data.project_id) || projectId,
    connectors: asArray(data.connectors).map(toConnector).filter((item) => Boolean(item.connector_instance_id)),
    summary: {
      connector_instance_count: asOptionalNumber(summary.connector_instance_count),
      active_count: asOptionalNumber(summary.active_count),
      running_count: asOptionalNumber(summary.running_count),
      profile_count: asOptionalNumber(summary.profile_count),
      credentials_configured_count: asOptionalNumber(summary.credentials_configured_count),
      automatic_refresh_enabled: asBoolean(summary.automatic_refresh_enabled),
      partial_coverage_connector_count: asOptionalNumber(summary.partial_coverage_connector_count),
      unsupported_resource_count: asOptionalNumber(summary.unsupported_resource_count),
      health_attention_connector_count: asOptionalNumber(summary.health_attention_connector_count),
      remote_absent_resource_count: asOptionalNumber(summary.remote_absent_resource_count),
      remote_unconfirmed_missing_resource_count: asOptionalNumber(summary.remote_unconfirmed_missing_resource_count),
      remote_retired_resource_count: asOptionalNumber(summary.remote_retired_resource_count),
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
        priority: asOptionalNumber(row.priority) ?? 100,
        requires_user_confirmation: asBoolean(row.requires_user_confirmation),
      };
    }).filter((candidate) => Boolean(candidate.connector_type)),
    observation: {
      http_status: asOptionalNumber(asRecord(data.observation).http_status) ?? 0,
      content_type: asString(asRecord(data.observation).content_type),
      response_bytes_read: asOptionalNumber(asRecord(data.observation).response_bytes_read) ?? 0,
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
    discovered_count: asOptionalNumber(data.discovered_count) || 0,
    covered_count: asOptionalNumber(data.covered_count) || 0,
    unsupported_count: asOptionalNumber(data.unsupported_count) || 0,
    materialized_preview_count: asOptionalNumber(data.materialized_preview_count) || 0,
    resources: asArray(data.resources).map((value, index) => {
      const row = asRecord(value);
      return {
        resource_index: asOptionalNumber(row.resource_index) ?? index,
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
        item_count: asOptionalNumber(row.item_count),
        success_count: asOptionalNumber(row.success_count),
        materialized_success_count: asOptionalNumber(row.materialized_success_count),
        unchanged_success_count: asOptionalNumber(row.unchanged_success_count),
        coverage_observation_count: asOptionalNumber(row.coverage_observation_count),
        knowledge_coverage_status: asString(row.knowledge_coverage_status) || undefined,
        failure_count: asOptionalNumber(row.failure_count),
        retired_count: asOptionalNumber(row.retired_count),
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
