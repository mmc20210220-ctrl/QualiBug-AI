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
  checkpoint_fingerprint?: string;
  plaintext_returned?: boolean;
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
  degraded_resource_count?: number;
  snapshot_complete?: boolean;
  cursor_checkpoint_committed?: boolean;
  checkpoint_storage?: string;
  next_cursor_returned_to_client?: boolean;
  source_content_returned?: boolean;
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
    || message.includes('transaction_busy')
  ) {
    return '资料正在更新，无需重复操作。系统完成后会自动显示最新状态。';
  }
  if (
    message.includes('checkpoint')
    || message.includes('cursor_mismatch')
    || message.includes('previous_cursor_required')
  ) {
    return '上次同步状态不完整，系统已保留原有资料。请点击“立即更新”重试；仍未恢复时重新授权即可。';
  }
  if (
    message.includes('permission')
    || message.includes('forbidden')
    || message.includes('code_1069902')
    || message.includes('code_99991663')
  ) {
    return '飞书授权范围不足。请在飞书开放平台为应用补充知识库与云文档只读权限，然后点击“重新授权”。';
  }
  if (
    message.includes('credential')
    || message.includes('profile')
    || message.includes('app_secret')
    || message.includes('access_token')
    || message.includes('auth_mode')
  ) {
    return '飞书连接信息未通过验证。请检查 App ID 与 App Secret，或重新填写访问令牌。';
  }
  if (
    message.includes('rate')
    || message.includes('99991400')
    || status === 429
  ) {
    return '飞书暂时限制了访问频率，原有资料不受影响。稍后点击“立即更新”即可。';
  }
  if (
    message.includes('transport_failed')
    || message.includes('api_failed')
    || message.includes('download_failed')
    || status >= 500
  ) {
    return '暂时无法读取飞书资料，原有资料不受影响。请检查网络后点击“立即更新”。';
  }
  if (
    message.includes('unsupported')
    || message.includes('export')
  ) {
    return '部分飞书资料暂时无法读取，系统没有覆盖原有资料。请确认文档权限或改用支持的文档格式。';
  }
  return '在线资料操作未完成，原有资料不受影响。请重试；仍失败时重新授权。';
}

async function connectorRequest(path: string, init?: RequestInit): Promise<JsonRecord> {
  const session = await getSession();
  if (!session) throw new Error('未登录或会话已失效，请重新登录。');

  const headers = new Headers(init?.headers);
  if (init?.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
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
  if (!project) throw new Error('请先选择客户项目。');
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
    checkpoint_fingerprint: asString(row.checkpoint_fingerprint) || undefined,
    plaintext_returned: asBoolean(row.plaintext_returned),
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
  };
}

export async function listKnowledgeConnectors(projectId: string): Promise<KnowledgeConnectorInventory> {
  const payload = await connectorRequest(projectConnectorPath(projectId));
  const data = asRecord(payload.data);
  const summary = asRecord(data.summary);
  return {
    project_id: asString(data.project_id) || projectId,
    connectors: asArray(data.connectors)
      .map(toConnector)
      .filter((item) => Boolean(item.connector_instance_id)),
    summary: {
      connector_instance_count: asNumber(summary.connector_instance_count),
      active_count: asNumber(summary.active_count),
      running_count: asNumber(summary.running_count),
      profile_count: asNumber(summary.profile_count),
      credentials_configured_count: asNumber(summary.credentials_configured_count),
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
  const instance = asRecord(data.connector_instance);
  return toConnector({
    ...instance,
    connection_profile: data.connection_profile,
  });
}

async function connectorAction(
  projectId: string,
  connectorId: string,
  action: 'test' | 'sync' | 'abort',
  body: JsonRecord = {},
): Promise<KnowledgeConnectorActionResult> {
  const connector = connectorId.trim();
  if (!connector) throw new Error('在线资料源尚未配置。');
  const payload = await connectorRequest(
    projectConnectorPath(projectId, `/${encodeURIComponent(connector)}/${action}`),
    { method: 'POST', body: JSON.stringify(body) },
  );
  return asRecord(payload.data) as KnowledgeConnectorActionResult;
}

export function testKnowledgeConnector(
  projectId: string,
  connectorId: string,
): Promise<KnowledgeConnectorActionResult> {
  return connectorAction(projectId, connectorId, 'test');
}

export function syncKnowledgeConnector(
  projectId: string,
  connectorId: string,
  options?: {
    deletion_policy?: 'RETAIN' | 'RETIRE_MISSING';
    allow_raw_text_fallback?: boolean;
    max_retire_count?: number;
    max_retire_ratio?: number;
  },
): Promise<KnowledgeConnectorActionResult> {
  return connectorAction(projectId, connectorId, 'sync', {
    deletion_policy: options?.deletion_policy || 'RETAIN',
    allow_raw_text_fallback: options?.allow_raw_text_fallback === true,
    max_retire_count: options?.max_retire_count ?? 100,
    max_retire_ratio: options?.max_retire_ratio ?? 0.25,
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

export function abortKnowledgeConnector(
  projectId: string,
  connectorId: string,
  reason: string,
): Promise<KnowledgeConnectorActionResult> {
  return connectorAction(projectId, connectorId, 'abort', { reason });
}
