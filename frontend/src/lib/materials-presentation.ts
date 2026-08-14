/**
 * 企业资料页（Materials）的展示层纯函数与类型。
 * 拆分自 pages/Materials.tsx：承载来源/连接器/范围/认证的展示映射与
 * 范围（scope）序列化逻辑；Materials.tsx 只做状态装配与布局。
 */
import { asArray, asOptionalNumber, asRecord, asString } from './value-guards';
import type {
  ConnectorManifest,
  ConnectorPermissionScope,
  KnowledgeConnectorActionResult,
  KnowledgeConnectorHealth,
  KnowledgeConnectorRecord,
  KnowledgeConnectorWebhook,
} from '../api/knowledge-connectors';

export type KnowledgeSource = {

  source_id: string;

  source_ref: string;

  source_type: string;

  original_name: string;

  status: string;

  version?: number;

  source_origin?: string;

  source_identity_fingerprints?: string[];

  created_at_utc?: string;

  updated_at_utc?: string;

  last_seen_at_utc?: string;

  source_updated_at?: string;

  permission_scope?: ConnectorPermissionScope;

};



export type ScopeProperty = Record<string, unknown>;

export type ScopeValues = Record<string, unknown>;

export type ParsedScopeValues = { values: ScopeValues; error?: string };

export type QuickConnectResult = { values: ScopeValues; manifest: ConnectorManifest };

export const DEFAULT_CONNECTOR_ID = 'connector-main';

export const DEFAULT_CONNECTOR_NAME = '在线资料连接器';

export function sourcePermissionScope(value: unknown): ConnectorPermissionScope | undefined {

  const row = asRecord(value);

  if (Object.keys(row).length === 0) return undefined;

  if (row.raw_remote_principals_returned !== false) {

    throw new Error('knowledge_source_permission_scope_principals_returned');

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



export function sourceRows(payload: unknown): KnowledgeSource[] {

  const root = asRecord(payload);

  const asset = asRecord(root.knowledge_asset || root.data || root);

  return asArray(asset.sources)

    .map(asRecord)

    .map((row) => {

      const fingerprints = asArray(row.source_identity_fingerprints)

        .map(asString)

        .filter(Boolean);

      return {

        source_id: asString(row.source_id) || asString(row.source_occurrence_id) || fingerprints[0] || '',

        source_ref: asString(row.source_ref) || asString(row.external_ref),

        source_type: asString(row.source_type),

        original_name: asString(row.original_name) || asString(row.filename),

        status: asString(row.status) || 'active',

        version: asOptionalNumber(row.version) || asOptionalNumber(row.occurrence_version),

        source_origin: asString(row.source_origin)

          || (asString(row.source_ref).startsWith('connector://') ? 'ONLINE_CONNECTOR' : 'DOCUMENT_REFERENCE'),

        source_identity_fingerprints: fingerprints,

        created_at_utc: asString(row.created_at_utc) || undefined,

        updated_at_utc: asString(row.updated_at_utc) || undefined,

        last_seen_at_utc: asString(row.last_seen_at_utc) || undefined,

        source_updated_at: asString(row.source_updated_at) || undefined,

        permission_scope: sourcePermissionScope(row.permission_scope),

      };

    })

    .filter((row) => Boolean(row.source_id || row.source_ref));

}



export function formatTime(value?: string, empty = '尚未完成首次更新'): string {

  if (!value) return empty;

  const parsed = new Date(value);

  if (Number.isNaN(parsed.getTime())) return value;

  return parsed.toLocaleString('zh-CN', { hour12: false });

}



export function syncCompletionMessage(prefix: string, result: KnowledgeConnectorActionResult): string {

  const materialized = result.materialized_resource_count ?? 0;

  const unchanged = result.unchanged_resource_count ?? 0;

  const covered = result.covered_resource_count ?? materialized + unchanged;

  const unsupported = result.unsupported_resource_count ?? 0;

  const discovered = result.discovered_resource_count ?? covered + unsupported;

  if (unsupported > 0) {

    return `${prefix}。发现 ${discovered} 项，已读取 ${covered} 项，${unsupported} 项资料类型暂不支持。`;

  }

  return `${prefix}，已读取 ${covered} 份在线资料。`;

}



export function permissionScopeLabel(scope?: ConnectorPermissionScope): string {

  if (!scope) return '未声明权限范围';

  if (scope.visibility === 'NOT_DECLARED') return '未声明权限范围';

  if (scope.availability === 'PERMISSION_DENIED') return '远端权限不足';

  if (scope.evidence_status && scope.evidence_status !== 'COMPLETE') {

    return `权限证据待确认 · ${scope.evidence_status}`;

  }

  return `权限范围 · ${scope.visibility || 'UNKNOWN'}`;

}



export function connectorHealthLabel(health?: KnowledgeConnectorHealth): string {

  switch (health?.status) {

    case 'HEALTHY': return '连接正常';

    case 'SYNCING': return '正在更新';

    case 'REAUTHORIZATION_REQUIRED': return '需要重新授权';

    case 'PERMISSION_INSUFFICIENT': return '授权范围不足';

    case 'AUTHORIZATION_EXPIRING': return '授权即将过期';

    case 'STALE': return '资料需要更新';

    case 'DUE': return '等待自动更新';

    case 'NOT_SYNCED': return '等待首次更新';

    case 'DOWNSTREAM_DEGRADED': return '资料已读取，语义刷新未完成';

    case 'CALIBRATION_REQUIRED': return '事件中断，等待完整校准';

    case 'PARTIAL_COVERAGE': return '部分资料未读取';

    case 'RETRYING': return '系统正在重试';

    case 'DISABLED': return '连接已关闭';

    case 'PAUSED': return '自动更新已暂停';

    case 'DEGRADED': return '最近一次更新未完成';

    default: return '健康状态待确认';

  }

}



export function connectorWebhookLabel(webhook?: KnowledgeConnectorWebhook): string {

  if (!webhook?.supported) return '';

  switch (webhook.status) {

    case 'CALIBRATION_REQUIRED': return '事件中断，需要完整校准';

    case 'ENABLED': return '事件触发已启用';

    case 'DISABLED': return '事件触发未启用';

    default: return `事件状态：${webhook.status || 'NOT_AVAILABLE'}`;

  }

}



export function connectorFreshnessLabel(health?: KnowledgeConnectorHealth): string {

  switch (health?.freshness.status) {

    case 'FRESH': return '资料保持最新';

    case 'DUE': return '已到自动更新时间';

    case 'STALE': return '资料新鲜度已过期';

    default: return '尚未测得资料新鲜度';

  }

}



export function connectorHealthActionLabel(health?: KnowledgeConnectorHealth): string {

  switch (health?.recommended_action) {

    case 'RUN_SYNC': return '建议立即检查一次';

    case 'RUN_CONNECTION_TEST': return '建议先验证连接';

    case 'REAUTHORIZE_CONNECTOR': return '请重新授权';

    case 'REVIEW_SEMANTIC_REFRESH': return '请检查语义刷新';

    case 'REVIEW_UNSUPPORTED_RESOURCES': return '请查看未支持的资料类型';

    case 'WAIT_FOR_AUTOMATIC_RETRY': return '系统会自动重试';

    case 'WAIT_FOR_SYNC': return '系统完成后自动显示';

    default: return '';

  }

}



export function connectorOauthLabel(oauth?: KnowledgeConnectorRecord['oauth']): string {

  if (!oauth?.supported) return '';

  switch (oauth.status) {

    case 'AUTHORIZED': return 'OAuth 授权已生效';

    case 'EXPIRING': return 'OAuth 授权即将过期';

    case 'EXPIRED': return 'OAuth 授权已过期';

    case 'PERMISSION_INSUFFICIENT': return 'OAuth 权限范围不足';

    case 'REAUTHORIZATION_REQUIRED': return '需要重新授权';

    case 'REVOKED': return '授权已被撤销';

    case 'NOT_AUTHORIZED': return '等待完成 OAuth 授权';

    default: return `OAuth 状态：${oauth.status || 'NOT_AVAILABLE'}`;

  }

}



export function connectorOauthRefreshLabel(oauth?: KnowledgeConnectorRecord['oauth']): string {

  if (!oauth?.automatic_refresh_supported) return '自动续期：连接器未声明 Refresh Token';

  switch (oauth.automatic_refresh_status) {

    case 'SUCCEEDED': return '自动续期：最近一次已成功';

    case 'FAILED': return '自动续期：最近一次失败，系统将继续重试';

    case 'NOT_DUE': return '自动续期：已启用，当前无需续期';

    default: return '自动续期：已启用';

  }

}



export function connectorTone(connector: KnowledgeConnectorRecord): string {

  const healthStatus = connector.health?.status;

  if (healthStatus === 'REAUTHORIZATION_REQUIRED' || healthStatus === 'PERMISSION_INSUFFICIENT' || healthStatus === 'AUTHORIZATION_EXPIRING' || healthStatus === 'DOWNSTREAM_DEGRADED' || healthStatus === 'CALIBRATION_REQUIRED' || healthStatus === 'DEGRADED') return 'danger';

  if (healthStatus === 'STALE' || healthStatus === 'DUE' || healthStatus === 'PARTIAL_COVERAGE' || healthStatus === 'RETRYING' || healthStatus === 'SYNCING') return 'warning';

  if (healthStatus === 'HEALTHY') return 'success';

  if (connector.active_sync_epoch_id || connector.auto_sync?.state === 'running') return 'warning';

  if (connector.auto_sync?.maintenance_required_by_user || connector.connection_profile?.reauthorization_required) return 'danger';

  if (connector.auto_sync?.state === 'retrying') return 'warning';

  if (connector.coverage?.status === 'PARTIAL_UNSUPPORTED') return 'warning';

  if (connector.last_successful_sync_epoch_id) return 'success';

  return 'neutral';

}



export function connectorLabel(connector: KnowledgeConnectorRecord): string {

  if (connector.health) return connectorHealthLabel(connector.health);

  if (connector.active_sync_epoch_id || connector.auto_sync?.state === 'running') return '正在自动更新';

  if (connector.auto_sync?.maintenance_required_by_user || connector.connection_profile?.reauthorization_required) {

    return connector.auto_sync?.message || '需要重新授权';

  }

  if (connector.auto_sync?.state === 'retrying') return connector.auto_sync.message || '系统正在自动恢复';

  if (connector.coverage?.status === 'PARTIAL_UNSUPPORTED') {

    return `已读取 ${connector.coverage.covered_count}/${connector.coverage.discovered_count}`;

  }

  if (connector.auto_sync?.message) return connector.auto_sync.message;

  if (connector.last_successful_sync_epoch_id) return '自动更新正常';

  return '等待首次更新';

}



export function manifestFields(manifest: ConnectorManifest | undefined, authMode: string) {

  return (manifest?.credential_fields || []).filter((field) => (

    !field.auth_modes?.length || field.auth_modes.includes(authMode)

  ));

}



export function credentialFieldLabel(field: ConnectorManifest['credential_fields'][number]): string {

  if (field.display_name) return field.display_name;

  return field.name

    .replace(/[_:-]+/g, ' ')

    .replace(/\b\w/g, (letter) => letter.toUpperCase());

}



export function authModeLabel(mode: string): string {

  return mode

    .replace(/[_:-]+/g, ' ')

    .replace(/\b\w/g, (letter) => letter.toUpperCase());

}



export function scopePresets(manifest: ConnectorManifest | undefined): string[] {

  const presets = manifest?.scope_schema?.presets;

  return Array.isArray(presets)

    ? presets.filter((value): value is string => typeof value === 'string' && Boolean(value.trim()))

    : [];

}



export function scopeSchemaHint(manifest: ConnectorManifest | undefined): string {

  const schema = asRecord(manifest?.scope_schema);

  const required = asArray(schema.required)

    .filter((value): value is string => typeof value === 'string' && Boolean(value.trim()));

  const description = asString(schema.description);

  const shorthand = asString(schema.shorthand);

  return [

    description,

    shorthand ? `支持格式：${shorthand}` : '',

    required.length > 0 ? `必填字段：${required.join('、')}` : '',

  ].filter(Boolean).join('；');

}



export function scopeProperties(manifest: ConnectorManifest | undefined): Array<[string, ScopeProperty]> {

  const properties = asRecord(asRecord(manifest?.scope_schema).properties);

  return Object.entries(properties).map(([name, value]) => [name, asRecord(value)]);

}



export function quickConnectManifests(manifests: ConnectorManifest[]): ConnectorManifest[] {

  return manifests

    .filter((manifest) => {

      const schema = asRecord(manifest.quick_connect_schema);

      return asString(schema.input_type) === 'url' && Boolean(asString(schema.scope_field));

    })

    .sort((left, right) => {

      const leftPriority = asOptionalNumber(asRecord(left.quick_connect_schema).priority) ?? 100;

      const rightPriority = asOptionalNumber(asRecord(right.quick_connect_schema).priority) ?? 100;

      return leftPriority - rightPriority || left.display_name.localeCompare(right.display_name);

    });

}



export function applyQuickConnectUrl(

  manifest: ConnectorManifest,

  rawUrl: string,

): QuickConnectResult {

  const value = rawUrl.trim();

  if (!value) throw new Error('璇峰厛绮樿创涓€涓湪绾胯祫鏂欏叆鍙ｆ湇鍔″櫒 URL');

  let parsed: URL;

  try {

    parsed = new URL(value);

  } catch {

    throw new Error('璇疯緭鍏ユ湁鏁堢殑 HTTP(S) URL');

  }

  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {

    throw new Error('鍦ㄧ嚎璧勬枡鍏ュ彛蹇呴』浣跨敤 HTTP(S) URL');

  }

  const schema = asRecord(manifest.quick_connect_schema);

  const scopeField = asString(schema.scope_field);

  const property = scopeProperties(manifest).find(([name]) => name === scopeField)?.[1];

  const propertyType = asString(property?.type);

  if (!scopeField || (propertyType !== 'array' && propertyType !== 'string')) {

    throw new Error('鎺ュ叆鍣ㄧ殑 Manifest 鏈０鏄庡彲鐢ㄧ殑 URL 鑼冨洿瀛楁');

  }

  return {

    manifest,

    values: {

      ...defaultScopeValues(manifest),

      [scopeField]: propertyType === 'array' ? [value] : value,

    },

  };

}



export function isObjectScope(manifest: ConnectorManifest | undefined): boolean {

  return asString(asRecord(manifest?.scope_schema).type) === 'object'

    && scopeProperties(manifest).length > 0;

}



export function defaultScopeValues(manifest: ConnectorManifest | undefined): ScopeValues {

  const values: ScopeValues = {};

  for (const [name, property] of scopeProperties(manifest)) {

    if (Object.prototype.hasOwnProperty.call(property, 'default')) {

      values[name] = property.default;

      continue;

    }

    const type = asString(property.type);

    values[name] = type === 'array' ? [] : type === 'boolean' ? false : '';

  }

  return values;

}



export function parseScopeValues(manifest: ConnectorManifest | undefined, raw: string): ParsedScopeValues {

  const defaults = defaultScopeValues(manifest);

  if (!isObjectScope(manifest)) return { values: defaults };

  const shorthand = raw.trim();

  if (shorthand.startsWith('http://') || shorthand.startsWith('https://')) {

    const required = asArray(asRecord(manifest?.scope_schema).required)

      .filter((value): value is string => typeof value === 'string' && Boolean(value.trim()));

    const target = required.find((name) => {

      const property = scopeProperties(manifest).find(([key]) => key === name)?.[1];

      return asString(property?.type) === 'array' || asString(property?.type) === 'string';

    });

    if (target) {

      const property = scopeProperties(manifest).find(([name]) => name === target)?.[1];

      return {

        values: {

          ...defaults,

          [target]: asString(property?.type) === 'array' ? [shorthand] : shorthand,

        },

      };

    }

  }

  try {

    const parsed: unknown = JSON.parse(raw);

    if (parsed !== null && typeof parsed === 'object' && !Array.isArray(parsed)) {

      return { values: { ...defaults, ...(parsed as ScopeValues) } };

    }

  } catch {

    return {

      values: defaults,

      error: '已保存的资料范围不是有效的 JSON；请修正范围后再保存。',

    };

  }

  return {

    values: defaults,

    error: '已保存的资料范围不是对象格式；请按 Manifest 字段重新填写。',

  };

}



export function serializeScope(

  manifest: ConnectorManifest | undefined,

  values: ScopeValues,

  raw: string,

): string {

  if (!isObjectScope(manifest)) return raw.trim();

  const properties = Object.fromEntries(

    Object.entries(values)

      .filter(([, value]) => (

        value !== undefined

        && value !== null

        && value !== ''

        && (!Array.isArray(value) || value.length > 0)

      )),

  );

  return JSON.stringify(properties);

}



export function missingRequiredScopeFields(

  manifest: ConnectorManifest | undefined,

  values: ScopeValues,

): string[] {

  const required = asArray(asRecord(manifest?.scope_schema).required)

    .filter((value): value is string => typeof value === 'string' && Boolean(value.trim()));

  return required.filter((name) => {

    const value = values[name];

    return value === undefined || value === null || value === ''

      || (Array.isArray(value) && value.length === 0);

  });

}
