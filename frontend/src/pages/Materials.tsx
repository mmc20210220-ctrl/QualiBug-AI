import { useCallback, useEffect, useMemo, useState, type ChangeEvent } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { getKnowledgeAsset, ingestKnowledge } from '../api/client';
import {
  connectKnowledgeConnector,
  configureKnowledgeConnector,
  listConnectorResources,
  listConnectorTypes,
  listKnowledgeConnectors,
  preflightConnectorSource,
  pauseKnowledgeConnector,
  reauthorizeKnowledgeConnector,
  refreshKnowledgeConnector,
  resumeKnowledgeConnector,
  startKnowledgeConnectorOAuth,
  type ConfigureConnectorInput,
  type ConnectorManifest,
  type ConnectorPermissionScope,
  type ConnectorResourceInventory,
  type ConnectorSourcePreflight,
  type KnowledgeConnectorActionResult,
  type KnowledgeConnectorHealth,
  type KnowledgeConnectorRecord,
  type KnowledgeConnectorWebhook,
} from '../api/knowledge-connectors';
import { ConnectorAcceptancePanel } from '../components/ConnectorAcceptancePanel';
import { ConnectorCoverage } from '../components/ConnectorCoverage';
import { useToast } from '../components/useToast';
import { usePageTitle } from '../lib/page-title';
import './Materials.css';

type JsonRecord = Record<string, unknown>;

type KnowledgeSource = {
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

type ScopeProperty = Record<string, unknown>;
type ScopeValues = Record<string, unknown>;
type ParsedScopeValues = { values: ScopeValues; error?: string };
type QuickConnectResult = { values: ScopeValues; manifest: ConnectorManifest };

const DEFAULT_CONNECTOR_ID = 'connector-main';
const DEFAULT_CONNECTOR_NAME = '在线资料连接器';

const asRecord = (value: unknown): JsonRecord => (
  value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as JsonRecord
    : {}
);
const asArray = (value: unknown): unknown[] => Array.isArray(value) ? value : [];
const asString = (value: unknown): string => typeof value === 'string' ? value : '';
const asNumber = (value: unknown): number | undefined => typeof value === 'number' ? value : undefined;

function sourcePermissionScope(value: unknown): ConnectorPermissionScope | undefined {
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

function sourceRows(payload: unknown): KnowledgeSource[] {
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
        version: asNumber(row.version) || asNumber(row.occurrence_version),
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

function formatTime(value?: string, empty = '尚未完成首次更新'): string {
  if (!value) return empty;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString('zh-CN', { hour12: false });
}

function syncCompletionMessage(prefix: string, result: KnowledgeConnectorActionResult): string {
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

function permissionScopeLabel(scope?: ConnectorPermissionScope): string {
  if (!scope) return '未声明权限范围';
  if (scope.visibility === 'NOT_DECLARED') return '未声明权限范围';
  if (scope.availability === 'PERMISSION_DENIED') return '远端权限不足';
  if (scope.evidence_status && scope.evidence_status !== 'COMPLETE') {
    return `权限证据待确认 · ${scope.evidence_status}`;
  }
  return `权限范围 · ${scope.visibility || 'UNKNOWN'}`;
}

function connectorHealthLabel(health?: KnowledgeConnectorHealth): string {
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

function connectorWebhookLabel(webhook?: KnowledgeConnectorWebhook): string {
  if (!webhook?.supported) return '';
  switch (webhook.status) {
    case 'CALIBRATION_REQUIRED': return '事件中断，需要完整校准';
    case 'ENABLED': return '事件触发已启用';
    case 'DISABLED': return '事件触发未启用';
    default: return `事件状态：${webhook.status || 'NOT_AVAILABLE'}`;
  }
}

function connectorFreshnessLabel(health?: KnowledgeConnectorHealth): string {
  switch (health?.freshness.status) {
    case 'FRESH': return '资料保持最新';
    case 'DUE': return '已到自动更新时间';
    case 'STALE': return '资料新鲜度已过期';
    default: return '尚未测得资料新鲜度';
  }
}

function connectorHealthActionLabel(health?: KnowledgeConnectorHealth): string {
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

function connectorOauthLabel(oauth?: KnowledgeConnectorRecord['oauth']): string {
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

function connectorOauthRefreshLabel(oauth?: KnowledgeConnectorRecord['oauth']): string {
  if (!oauth?.automatic_refresh_supported) return '自动续期：连接器未声明 Refresh Token';
  switch (oauth.automatic_refresh_status) {
    case 'SUCCEEDED': return '自动续期：最近一次已成功';
    case 'FAILED': return '自动续期：最近一次失败，系统将继续重试';
    case 'NOT_DUE': return '自动续期：已启用，当前无需续期';
    default: return '自动续期：已启用';
  }
}

function connectorTone(connector: KnowledgeConnectorRecord): string {
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

function connectorLabel(connector: KnowledgeConnectorRecord): string {
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

function manifestFields(manifest: ConnectorManifest | undefined, authMode: string) {
  return (manifest?.credential_fields || []).filter((field) => (
    !field.auth_modes?.length || field.auth_modes.includes(authMode)
  ));
}

function credentialFieldLabel(field: ConnectorManifest['credential_fields'][number]): string {
  if (field.display_name) return field.display_name;
  return field.name
    .replace(/[_:-]+/g, ' ')
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function authModeLabel(mode: string): string {
  return mode
    .replace(/[_:-]+/g, ' ')
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function scopePresets(manifest: ConnectorManifest | undefined): string[] {
  const presets = manifest?.scope_schema?.presets;
  return Array.isArray(presets)
    ? presets.filter((value): value is string => typeof value === 'string' && Boolean(value.trim()))
    : [];
}

function scopeSchemaHint(manifest: ConnectorManifest | undefined): string {
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

function scopeProperties(manifest: ConnectorManifest | undefined): Array<[string, ScopeProperty]> {
  const properties = asRecord(asRecord(manifest?.scope_schema).properties);
  return Object.entries(properties).map(([name, value]) => [name, asRecord(value)]);
}

function quickConnectManifests(manifests: ConnectorManifest[]): ConnectorManifest[] {
  return manifests
    .filter((manifest) => {
      const schema = asRecord(manifest.quick_connect_schema);
      return asString(schema.input_type) === 'url' && Boolean(asString(schema.scope_field));
    })
    .sort((left, right) => {
      const leftPriority = asNumber(asRecord(left.quick_connect_schema).priority) ?? 100;
      const rightPriority = asNumber(asRecord(right.quick_connect_schema).priority) ?? 100;
      return leftPriority - rightPriority || left.display_name.localeCompare(right.display_name);
    });
}

function applyQuickConnectUrl(
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

function isObjectScope(manifest: ConnectorManifest | undefined): boolean {
  return asString(asRecord(manifest?.scope_schema).type) === 'object'
    && scopeProperties(manifest).length > 0;
}

function defaultScopeValues(manifest: ConnectorManifest | undefined): ScopeValues {
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

function parseScopeValues(manifest: ConnectorManifest | undefined, raw: string): ParsedScopeValues {
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

function serializeScope(
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

function missingRequiredScopeFields(
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

function ConnectorResourcePreview({ preview }: { preview?: ConnectorResourceInventory }) {
  if (!preview || preview.status === 'NOT_AVAILABLE') return null;
  return (
    <section className="connector-resource-preview" aria-label="发现资源预览">
      <div className="connector-resource-preview-heading">
        <span>发现资源预览</span>
        <strong>{preview.discovered_count} 项 · 已接入 {preview.covered_count} 项</strong>
      </div>
      {preview.resources.length === 0 ? (
        <p>尚未形成可展示的资源摘要，完成首次同步后会自动更新。</p>
      ) : (
        <div className="connector-resource-preview-list">
          {preview.resources.slice(0, 5).map((resource) => (
            <article key={resource.resource_index}>
              <strong>{resource.display_title || '未命名资源'}</strong>
              <span>{resource.remote_object_type || resource.resource_kind || resource.state}</span>
              <small>
                {resource.updated_at_utc
                  ? `最近观测 · ${formatTime(resource.updated_at_utc, '暂无记录')}`
                  : '尚未记录更新时间'}
                {resource.source_updated_at ? ` · 来源更新标记 · ${resource.source_updated_at}` : ''}
                {resource.permission_scope ? ` · ${permissionScopeLabel(resource.permission_scope)}` : ''}
              </small>
            </article>
          ))}
        </div>
      )}
      {preview.preview_truncated && <small>资源较多，当前仅展示前 100 项摘要。</small>}
    </section>
  );
}

export function Materials() {
  usePageTitle('企业资料');
  const [params] = useSearchParams();
  const project = params.get('project')?.trim() || '';
  const toast = useToast();

  const [connectors, setConnectors] = useState<KnowledgeConnectorRecord[]>([]);
  const [manifests, setManifests] = useState<ConnectorManifest[]>([]);
  const [resourcePreviews, setResourcePreviews] = useState<Record<string, ConnectorResourceInventory>>({});
  const [sources, setSources] = useState<KnowledgeSource[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [operation, setOperation] = useState<Record<string, string>>({});

  const [formOpen, setFormOpen] = useState(false);
  const [editingId, setEditingId] = useState('');
  const [connectorType, setConnectorType] = useState('');
  const [resourceScope, setResourceScope] = useState('');
  const [scopeValues, setScopeValues] = useState<ScopeValues>({});
  const [scopeParseError, setScopeParseError] = useState('');
  const [quickConnectType, setQuickConnectType] = useState('');
  const [quickConnectUrl, setQuickConnectUrl] = useState('');
  const [quickConnectApplied, setQuickConnectApplied] = useState(false);
  const [sourcePreflight, setSourcePreflight] = useState<ConnectorSourcePreflight | null>(null);
  const [preflighting, setPreflighting] = useState(false);
  const [authMode, setAuthMode] = useState('');
  const [credentialValues, setCredentialValues] = useState<Record<string, string>>({});
  const [webhookEnabled, setWebhookEnabled] = useState(false);
  const [saving, setSaving] = useState(false);

  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadType, setUploadType] = useState('prd');
  const [uploading, setUploading] = useState(false);

  const selectedManifest = useMemo(
    () => manifests.find((manifest) => manifest.connector_type === connectorType),
    [connectorType, manifests],
  );
  const selectedCredentialFields = useMemo(
    () => manifestFields(selectedManifest, authMode),
    [authMode, selectedManifest],
  );
  const quickConnectOptions = useMemo(
    () => quickConnectManifests(manifests),
    [manifests],
  );

  const refresh = useCallback(async () => {
    if (!project) {
      setConnectors([]);
      setManifests([]);
      setResourcePreviews({});
      setSources([]);
      return;
    }
    setLoading(true);
    setLoadError('');
    try {
      const [inventory, asset, catalog] = await Promise.all([
        listKnowledgeConnectors(project),
        getKnowledgeAsset(project),
        listConnectorTypes(),
      ]);
      setConnectors(inventory.connectors);
      setManifests(catalog.connector_types);
      const quickOptions = quickConnectManifests(catalog.connector_types);
      setConnectorType((current) => (
        current && catalog.connector_types.some((manifest) => manifest.connector_type === current)
          ? current
          : catalog.connector_types[0]?.connector_type || ''
      ));
      setQuickConnectType((current) => (
        current && quickOptions.some((manifest) => manifest.connector_type === current)
          ? current
          : quickOptions[0]?.connector_type || ''
      ));
      const previews = await Promise.all(
        inventory.connectors.map(async (connector) => [
          connector.connector_instance_id,
          await listConnectorResources(project, connector.connector_instance_id),
        ] as const),
      );
      setResourcePreviews(Object.fromEntries(previews));
      setSources(sourceRows(asset));
    } catch (error: unknown) {
      setLoadError(error instanceof Error ? error.message : '企业资料加载失败');
    } finally {
      setLoading(false);
    }
  }, [project]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const onlineSources = useMemo(
    () => sources.filter((source) => (
      source.source_origin === 'ONLINE_CONNECTOR'
      || source.source_ref.startsWith('connector://')
    )),
    [sources],
  );
  const uploadedSources = useMemo(
    () => sources.filter((source) => (
      source.source_origin !== 'ONLINE_CONNECTOR'
      && !source.source_ref.startsWith('connector://')
    )),
    [sources],
  );

  const resetForm = () => {
    setEditingId('');
    const firstManifest = quickConnectOptions[0] || manifests[0];
    setConnectorType(firstManifest?.connector_type || '');
    setQuickConnectType(firstManifest?.connector_type || '');
    setQuickConnectUrl('');
    setQuickConnectApplied(false);
    setSourcePreflight(null);
    setResourceScope(scopePresets(firstManifest)[0] || '');
    setScopeValues(defaultScopeValues(firstManifest));
    setScopeParseError('');
    setAuthMode(firstManifest?.auth_modes[0] || '');
    setCredentialValues({});
    setWebhookEnabled(false);
  };

  const openCreateForm = () => {
    resetForm();
    setFormOpen(true);
  };

  const openEditForm = (connector: KnowledgeConnectorRecord) => {
    setEditingId(connector.connector_instance_id);
    setConnectorType(connector.connector_type);
    setResourceScope(connector.resource_scope);
    const manifest = manifests.find((item) => item.connector_type === connector.connector_type);
    const parsedScope = parseScopeValues(manifest, connector.resource_scope);
    setScopeValues(parsedScope.values);
    setScopeParseError(parsedScope.error || '');
    setQuickConnectType('');
    setQuickConnectUrl('');
    setQuickConnectApplied(false);
    setSourcePreflight(null);
    const mode = connector.connection_profile?.auth_mode
      || manifests.find((manifest) => manifest.connector_type === connector.connector_type)?.auth_modes[0]
      || '';
    setAuthMode(mode);
    setCredentialValues({});
    setWebhookEnabled(connector.webhook?.enabled === true);
    setFormOpen(true);
  };

  const applyQuickConnectManifest = (manifest: ConnectorManifest, message: string) => {
    if (!manifest) {
      toast.show('褰撳墠娌℃湁 Manifest 澹版槑 URL 鍏ュ彛鐨勮繛鎺ュ櫒', 'warning');
      return;
    }
    try {
      const result = applyQuickConnectUrl(manifest, quickConnectUrl);
      setConnectorType(result.manifest.connector_type);
      setQuickConnectType(result.manifest.connector_type);
      setAuthMode(result.manifest.auth_modes[0] || '');
      setScopeValues(result.values);
      setResourceScope('');
      setScopeParseError('');
      setCredentialValues({});
      setWebhookEnabled(false);
      setQuickConnectApplied(true);
      toast.show(message, 'success');
    } catch (error: unknown) {
      toast.show(error instanceof Error ? error.message : '入口 URL 未能用于快速接入', 'warning');
    }
  };

  const useQuickConnectUrl = () => {
    const manifest = quickConnectOptions.find((item) => item.connector_type === quickConnectType)
      || quickConnectOptions[0];
    if (!manifest) {
      toast.show('褰撳墠娌℃湁 Manifest 澹版槑 URL 鍏ュ彛鐨勮繛鎺ュ櫒', 'warning');
      return;
    }
    applyQuickConnectManifest(
      manifest,
      '已根据入口 URL 填写安全默认范围；首次读取前仍会执行连接与边界检查',
    );
  };

  const preflightSourceUrl = async () => {
    if (!project) return;
    setPreflighting(true);
    setSourcePreflight(null);
    try {
      const result = await preflightConnectorSource(project, quickConnectUrl);
      setSourcePreflight(result);
      const recommended = quickConnectOptions.find(
        (manifest) => manifest.connector_type === result.recommended_connector_type,
      );
      if (recommended) {
        applyQuickConnectManifest(
          recommended,
          `已根据在线入口识别为${recommended.display_name}；请确认授权后开始读取`,
        );
      } else if (result.status === 'AUTHORIZATION_REQUIRED') {
        toast.show('入口需要授权才能识别类型；请从下方候选能力继续并填写授权。', 'warning');
      } else if (result.candidates.length > 0) {
        toast.show('入口可以接入，但需要你确认使用哪一种资料能力。', 'warning');
      } else {
        toast.show('当前没有可用的 URL 连接器 Manifest，请改用已声明的接入方式。', 'warning');
      }
    } catch (error: unknown) {
      toast.show(error instanceof Error ? error.message : '入口预检未完成，请检查 URL 后重试。', 'danger');
    } finally {
      setPreflighting(false);
    }
  };

  const choosePreflightCandidate = (connectorTypeValue: string) => {
    const manifest = quickConnectOptions.find((item) => item.connector_type === connectorTypeValue);
    if (!manifest) {
      toast.show('候选连接器 Manifest 已不可用，请刷新页面后重试。', 'warning');
      return;
    }
    setQuickConnectType(manifest.connector_type);
    applyQuickConnectManifest(
      manifest,
      `已选择${manifest.display_name}并填写入口范围；首次读取前仍会执行连接与边界检查`,
    );
  };

  const profilePayload = (): ConfigureConnectorInput['connection_profile'] => {
    const fields = Object.fromEntries(
      selectedCredentialFields
        .map((field) => [field.name, credentialValues[field.name]?.trim()] as const)
        .filter(([, value]) => Boolean(value)),
    );
    return authMode ? { auth_mode: authMode, ...fields } : fields;
  };

  const credentialsReady = (): boolean => {
    const requiredCredentialsReady = editingId || selectedCredentialFields.every(
      (field) => !field.required || Boolean(credentialValues[field.name]?.trim()),
    );
    if (!requiredCredentialsReady) return false;
    if (!selectedManifest?.webhook_supported || !webhookEnabled) return true;
    const editingConnector = connectors.find(
      (connector) => connector.connector_instance_id === editingId,
    );
    return Boolean(
      credentialValues.webhook_secret?.trim()
      || editingConnector?.connection_profile?.configured_fields?.webhook_secret,
    );
  };

  const saveAndStart = async () => {
    if (!project) return;
    if (scopeParseError) {
      toast.show(scopeParseError, 'warning');
      return;
    }
    const scope = serializeScope(selectedManifest, scopeValues, resourceScope);
    if (!scope) {
      toast.show('请选择同步范围，或填写Manifest声明的范围值。', 'warning');
      return;
    }
    if (!selectedManifest) {
      toast.show('请先选择一个可用的连接器类型。', 'warning');
      return;
    }
    const missingScopeFields = missingRequiredScopeFields(selectedManifest, scopeValues);
    if (missingScopeFields.length > 0) {
      toast.show(`请填写范围必填字段：${missingScopeFields.join('、')}`, 'warning');
      return;
    }
    if (!credentialsReady()) {
      toast.show('请填写完整的连接器授权信息。', 'warning');
      return;
    }

    const connectorId = editingId || `${DEFAULT_CONNECTOR_ID}-${selectedManifest.connector_type}`;
    setSaving(true);
    setOperation((current) => ({ ...current, [connectorId]: '正在连接在线资料并读取资源…' }));
    try {
      const configuration = {
        connector_type: selectedManifest.connector_type,
        connector_instance_id: connectorId,
        display_name: selectedManifest.display_name || DEFAULT_CONNECTOR_NAME,
        resource_scope: scope,
        status: 'ACTIVE',
        connection_profile: profilePayload(),
        webhook_policy: selectedManifest.webhook_supported
          ? { enabled: webhookEnabled }
          : undefined,
      };
      if (selectedManifest.oauth_schema && Object.keys(selectedManifest.oauth_schema).length > 0) {
        await configureKnowledgeConnector(project, configuration);
        const started = await startKnowledgeConnectorOAuth(project, connectorId);
        if (!started.authorization_url) throw new Error('OAuth 授权地址为空。');
        window.location.assign(started.authorization_url);
        return;
      }
      const result = await connectKnowledgeConnector(project, configuration);
      setFormOpen(false);
      resetForm();
      await refresh();
      toast.show(syncCompletionMessage(`${selectedManifest.display_name || '在线资料'}已连接`, result.sync), 'success');
    } catch (error: unknown) {
      await refresh();
      toast.show(error instanceof Error ? error.message : '在线资料连接未完成，请重试。', 'danger');
    } finally {
      setSaving(false);
      setOperation((current) => ({ ...current, [connectorId]: '' }));
    }
  };

  const checkNow = async (connector: KnowledgeConnectorRecord) => {
    const id = connector.connector_instance_id;
    setOperation((current) => ({ ...current, [id]: '正在检查在线资料最新状态…' }));
    try {
      const result = await refreshKnowledgeConnector(project, id);
      await refresh();
      toast.show(syncCompletionMessage('检查完成', result), 'success');
    } catch (error: unknown) {
      await refresh();
      toast.show(error instanceof Error ? error.message : '检查未完成，系统仍会自动重试。', 'danger');
    } finally {
      setOperation((current) => ({ ...current, [id]: '' }));
    }
  };

  const runLifecycleAction = async (
    connector: KnowledgeConnectorRecord,
    action: 'pause' | 'resume' | 'reauthorize',
  ) => {
    const id = connector.connector_instance_id;
    setOperation((current) => ({ ...current, [id]: '正在更新连接器状态…' }));
    try {
      if (action === 'pause') await pauseKnowledgeConnector(project, id);
      if (action === 'resume') await resumeKnowledgeConnector(project, id);
      if (action === 'reauthorize') {
        if (connector.oauth?.supported) {
          const started = await startKnowledgeConnectorOAuth(project, id);
          if (!started.authorization_url) throw new Error('OAuth 授权地址为空。');
          window.location.assign(started.authorization_url);
          return;
        }
        await reauthorizeKnowledgeConnector(project, id);
      }
      await refresh();
      toast.show('连接器状态已更新。', 'success');
    } catch (error: unknown) {
      await refresh();
      toast.show(error instanceof Error ? error.message : '连接器状态更新未完成。', 'danger');
    } finally {
      setOperation((current) => ({ ...current, [id]: '' }));
    }
  };

  const uploadSupplement = async () => {
    if (!project || !uploadFile) {
      toast.show('请选择要补充上传的资料。', 'warning');
      return;
    }
    setUploading(true);
    try {
      await ingestKnowledge(project, uploadFile, uploadType);
      setUploadFile(null);
      const input = document.getElementById('materials-upload-file') as HTMLInputElement | null;
      if (input) input.value = '';
      await refresh();
      toast.show('补充资料已加入统一企业知识库。', 'success');
    } catch (error: unknown) {
      toast.show(error instanceof Error ? error.message : '资料上传失败', 'danger');
    } finally {
      setUploading(false);
    }
  };

  if (!project) {
    return (
      <div className="materials-empty-project">
        <span className="panel-kicker">Enterprise Materials</span>
        <h1>企业资料</h1>
        <p>请先选择客户项目，再接入在线资料或上传补充文件。</p>
        <Link className="btn btn-primary" to="/settings">选择客户</Link>
      </div>
    );
  }

  return (
    <div className="materials-page">
      <header className="page-header materials-header">
        <div>
          <span className="panel-kicker">Enterprise Materials</span>
          <h1>企业资料</h1>
          <p>连接一次，系统自动读取、识别、去重、更新和恢复；日常无需维护。</p>
          <div className="page-summary-strip">
            <span className="summary-pill strong">在线来源 {connectors.length}</span>
            <span className="summary-pill">在线资料 {onlineSources.length}</span>
            <span className="summary-pill">上传补充 {uploadedSources.length}</span>
            <span className="summary-pill">资料总数 {sources.length}</span>
          </div>
        </div>
        <button className="btn btn-primary" type="button" onClick={openCreateForm}>
          接入在线资料
        </button>
      </header>

      {loadError && <div className="materials-alert tone-danger">{loadError}</div>}

      <section className="materials-primary-card">
        <div className="materials-section-heading">
          <div>
            <span className="settings-hero-kicker">自动维护</span>
            <h2>在线连接器</h2>
            <p>系统定期检查更新，遇到临时故障自动重试；只有授权失效时才需要你处理。</p>
          </div>
          <button className="btn btn-secondary" type="button" onClick={() => void refresh()} disabled={loading}>
            {loading ? '刷新中…' : '刷新状态'}
          </button>
        </div>

        {connectors.length === 0 ? (
          <div className="materials-empty-state">
            <strong>尚未连接在线资料</strong>
            <span>选择连接器类型、填写授权并选择范围，系统会自动验证并完成首次读取。</span>
            <button className="btn btn-primary" type="button" onClick={openCreateForm}>开始连接</button>
          </div>
        ) : (
          <div className="materials-connector-grid">
            {connectors.map((connector) => {
              const busy = Boolean(operation[connector.connector_instance_id]);
              const running = Boolean(connector.active_sync_epoch_id) || connector.auto_sync?.state === 'running';
              const needsHelp = Boolean(
                connector.auto_sync?.maintenance_required_by_user
                || connector.connection_profile?.reauthorization_required
                || connector.health?.status === 'PERMISSION_INSUFFICIENT'
                || connector.health?.status === 'AUTHORIZATION_EXPIRING'
                || connector.health?.status === 'CALIBRATION_REQUIRED'
                || !connector.connection_profile?.credentials_configured,
              );
              return (
                <article className="materials-connector-card" key={connector.connector_instance_id}>
                  <div className="materials-connector-top">
                    <div>
                      <span className="materials-source-kind">{connector.connector_type || '在线连接器'}</span>
                      <h3>{connector.display_name || connector.connector_type || DEFAULT_CONNECTOR_NAME}</h3>
                      <span className="materials-simple-scope">{connector.resource_scope || '按Manifest默认范围读取'}</span>
                    </div>
                    <span className={`status status-${connectorTone(connector)}`}>{connectorLabel(connector)}</span>
                  </div>

                  <div className="materials-connector-meta">
                    <div><span>自动更新</span><strong>{connector.auto_sync?.enabled === false ? '已关闭' : '已开启'}</strong></div>
                    <div><span>最近完成</span><strong>{formatTime(connector.last_successful_sync_at_utc)}</strong></div>
                    <div><span>最近失败</span><strong>{formatTime(connector.last_failed_sync_at_utc, '暂无记录')}</strong></div>
                    <div><span>授权状态</span><strong>{needsHelp ? '需要处理' : '正常'}</strong></div>
                  </div>

                  {connector.health && (
                    <div className={`materials-health-summary tone-${connectorTone(connector)}`}>
                      <div>
                        <strong>{connectorHealthLabel(connector.health)}</strong>
                        <span>{connectorFreshnessLabel(connector.health)}</span>
                      </div>
                      <span>
                        {connector.health.evidence.measured ? '已根据同步收据核验' : '等待首次同步收据'}
                        {connectorHealthActionLabel(connector.health)
                          ? ` · ${connectorHealthActionLabel(connector.health)}`
                          : ''}
                      </span>
                    </div>
                  )}

                  {connector.webhook?.supported && (
                    <div className={`materials-health-summary tone-${connector.webhook.status === 'CALIBRATION_REQUIRED' ? 'danger' : 'neutral'}`}>
                      <div>
                        <strong>事件触发</strong>
                        <span>{connectorWebhookLabel(connector.webhook)}</span>
                      </div>
                      <span>
                        {connector.webhook.status === 'CALIBRATION_REQUIRED'
                          ? '请执行一次完整数据同步以恢复事件序列'
                          : '事件仅触发现有同步，不会直接修改资料'}
                      </span>
                    </div>
                  )}

                  {connector.oauth?.supported && (
                    <div className={`materials-health-summary tone-${connector.oauth.status === 'PERMISSION_INSUFFICIENT' || connector.oauth.status === 'REAUTHORIZATION_REQUIRED' ? 'danger' : 'neutral'}`}>
                      <div>
                        <strong>OAuth 授权</strong>
                        <span>{connectorOauthLabel(connector.oauth)}</span>
                        <span>{connectorOauthRefreshLabel(connector.oauth)}</span>
                      </div>
                      <span>
                        {connector.oauth.required_scopes?.length
                          ? `最小权限：${connector.oauth.required_scopes.join('、')}`
                          : '权限由连接器 Manifest 声明'}
                      </span>
                    </div>
                  )}

                  {connector.last_failed_sync_epoch_id && (
                    <div className="materials-operation-note">
                      {connector.auto_sync?.message || '最近一次同步未完成，系统会自动重试。'}
                    </div>
                  )}

                  <ConnectorCoverage coverage={connector.coverage} />
                  <ConnectorResourcePreview preview={resourcePreviews[connector.connector_instance_id]} />

                  <ConnectorAcceptancePanel
                    projectId={project}
                    connectorId={connector.connector_instance_id}
                    connectorName={connector.display_name || connector.connector_type}
                    disabled={busy || running || needsHelp || connector.status !== 'ACTIVE'}
                  />

                  {(operation[connector.connector_instance_id] || running || connector.auto_sync?.state === 'retrying') && (
                    <div className="materials-operation-note">
                      {operation[connector.connector_instance_id] || connector.auto_sync?.message || '系统正在自动更新资料。'}
                    </div>
                  )}

                  <div className="materials-card-actions">
                    {connector.status === 'ACTIVE' && (
                      <button className="btn btn-secondary" type="button" onClick={() => void runLifecycleAction(connector, 'pause')} disabled={busy || running}>
                        暂停自动更新
                      </button>
                    )}
                    {connector.status === 'PAUSED' && (
                      <button className="btn btn-secondary" type="button" onClick={() => void runLifecycleAction(connector, 'resume')} disabled={busy}>
                        恢复自动更新
                      </button>
                    )}
                    {needsHelp && (
                      <button className="btn btn-primary" type="button" onClick={() => openEditForm(connector)} disabled={busy || running}>
                        重新授权
                      </button>
                    )}
                  </div>

                  <details className="materials-advanced">
                    <summary>遇到问题时</summary>
                    <div className="materials-advanced-field">
                      <p>系统会自动更新和重试。只有需要立即确认最新资料时，才手动检查一次。</p>
                      <button className="btn btn-secondary" type="button" onClick={() => void checkNow(connector)} disabled={busy || running || connector.status !== 'ACTIVE'}>
                        现在检查一次
                      </button>
                    </div>
                  </details>
                </article>
              );
            })}
          </div>
        )}
      </section>

      {formOpen && (
        <section className="materials-config-card" aria-label="在线资料连接">
          <div className="materials-section-heading">
            <div>
              <span className="settings-hero-kicker">两步完成</span>
              <h2>{editingId ? `重新授权${selectedManifest?.display_name || '连接器'}` : `连接${selectedManifest?.display_name || '在线资料'}`}</h2>
              <p>保存后自动测试并读取资料，后续更新和重试由系统处理。</p>
            </div>
            <button className="btn btn-ghost" type="button" onClick={() => setFormOpen(false)}>关闭</button>
          </div>

          {!editingId && quickConnectOptions.length > 0 && (
            <section className="materials-quick-connect" aria-label="快速接入在线资料">
              <div>
                <span className="settings-hero-kicker">来源优先</span>
                <h3>粘贴一个在线资料入口</h3>
                <p>系统先用一次受边界约束的只读预检识别可用资料能力，再按 Manifest 填写范围；无需先理解连接器类型或手工编写范围 JSON。</p>
              </div>
              <div className="materials-quick-connect-row">
                <label className="form-group materials-quick-connect-url">
                  <span className="form-label">入口 URL</span>
                  <input
                    className="form-input"
                    type="url"
                    value={quickConnectUrl}
                    onChange={(event) => setQuickConnectUrl(event.target.value)}
                    placeholder="https://example.com/docs"
                    autoComplete="url"
                  />
                </label>
                <button className="btn btn-secondary" type="button" onClick={() => void preflightSourceUrl()} disabled={preflighting}>
                  {preflighting ? '正在识别入口…' : '识别并填写范围'}
                </button>
              </div>
              <details className="materials-advanced materials-quick-connect-manual">
                <summary>我已知道资料类型，直接选择</summary>
                <div className="materials-advanced-field">
                  <label className="form-group">
                    <span className="form-label">资料来源能力</span>
                    <select
                      className="form-input"
                      value={quickConnectType}
                      onChange={(event) => setQuickConnectType(event.target.value)}
                    >
                      {quickConnectOptions.map((manifest) => (
                        <option key={manifest.connector_type} value={manifest.connector_type}>
                          {manifest.display_name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <button className="btn btn-secondary" type="button" onClick={useQuickConnectUrl}>
                    按所选能力填写范围
                  </button>
                </div>
              </details>
              {sourcePreflight && (
                <div className="materials-preflight-result" aria-label="在线入口识别结果">
                  <div className="materials-preflight-heading">
                    <strong>
                      {sourcePreflight.status === 'READY'
                        ? '已找到明确的资料能力'
                        : sourcePreflight.status === 'AUTHORIZATION_REQUIRED'
                          ? '入口需要授权后才能确认'
                          : sourcePreflight.status === 'REMOTE_ERROR'
                            ? '入口暂时无法读取'
                          : sourcePreflight.status === 'NO_QUICK_CONNECTOR'
                            ? '没有可用的 URL 连接器'
                            : '请确认要使用的资料能力'}
                    </strong>
                    <span>
                      只读预检 · HTTP {sourcePreflight.observation.http_status}
                      {sourcePreflight.observation.content_type
                        ? ` · ${sourcePreflight.observation.content_type}`
                        : ''}
                    </span>
                  </div>
                  <small>预检只返回结构证据和指纹，不返回资料正文、凭据或写入结果。</small>
                  {sourcePreflight.candidates.length > 0 && (
                    <div className="materials-preflight-candidates">
                      {sourcePreflight.candidates.map((candidate) => {
                        const manifest = quickConnectOptions.find(
                          (item) => item.connector_type === candidate.connector_type,
                        );
                        return (
                          <div className="materials-preflight-candidate" key={candidate.connector_type}>
                            <div>
                              <strong>{candidate.display_name || candidate.connector_type}</strong>
                              <span>
                                {candidate.match_status === 'MATCHED' ? '已获得来源证据' : '可按此能力继续'}
                                {candidate.evidence.length > 0 ? ` · ${candidate.evidence.join('、')}` : ''}
                              </span>
                            </div>
                            {manifest && (
                              <button
                                className="btn btn-secondary"
                                type="button"
                                onClick={() => choosePreflightCandidate(candidate.connector_type)}
                              >
                                使用此能力
                              </button>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              )}
              {quickConnectApplied && (
                <div className="materials-quick-connect-applied">
                  已使用入口 URL；其余范围保持 Manifest 默认值。保存后会直接进入连接测试和首次只读同步。
                </div>
              )}
            </section>
          )}

          <div className="materials-step">
            <span className="materials-step-number">1</span>
            <div>
              <h3>选择连接器并填写授权</h3>
              <p>权限要求、认证字段和支持的资料类型均来自连接器Manifest。</p>
            </div>
          </div>

          <div className="materials-form-grid">
            <label className="form-group">
              <span className="form-label">连接器类型</span>
              <select
                className="form-input"
                value={connectorType}
                disabled={Boolean(editingId)}
                onChange={(event: ChangeEvent<HTMLSelectElement>) => {
                  const next = event.target.value;
                  const manifest = manifests.find((item) => item.connector_type === next);
                  setConnectorType(next);
                  setSourcePreflight(null);
                  setQuickConnectType(
                    quickConnectOptions.some((item) => item.connector_type === next) ? next : '',
                  );
                  setQuickConnectApplied(false);
                  setAuthMode(manifest?.auth_modes[0] || '');
                  setResourceScope(scopePresets(manifest)[0] || '');
                  setScopeValues(defaultScopeValues(manifest));
                  setScopeParseError('');
                  setCredentialValues({});
                  setWebhookEnabled(false);
                }}
              >
                {manifests.map((manifest) => <option key={manifest.connector_type} value={manifest.connector_type}>{manifest.display_name}</option>)}
              </select>
            </label>
            {selectedManifest && (
              <div className="form-group">
                <span className="form-label">权限与能力</span>
                <p>{selectedManifest.read_only ? '只读访问' : '按Manifest声明的访问方式'} · {selectedManifest.supported_resource_types.join('、') || '资料类型由连接器声明'}</p>
              </div>
            )}
          </div>

          {selectedManifest && selectedManifest.auth_modes.length > 1 && (
            <details className="materials-advanced" open>
              <summary>认证方式</summary>
              <div className="materials-advanced-field">
                <select className="form-input" value={authMode} onChange={(event: ChangeEvent<HTMLSelectElement>) => {
                  setAuthMode(event.target.value);
                  setCredentialValues({});
                }}>
                  {selectedManifest.auth_modes.map((mode) => <option key={mode} value={mode}>{authModeLabel(mode)}</option>)}
                </select>
              </div>
            </details>
          )}

          <div className="materials-form-grid">
            {selectedCredentialFields.map((field) => (
              <label className="form-group" key={field.name}>
                <span className="form-label">{credentialFieldLabel(field)}{field.required ? ' *' : ''}</span>
                <input
                  className="form-input"
                  type={field.secret || field.field_type.includes('token') || field.field_type.includes('password') ? 'password' : 'text'}
                  value={credentialValues[field.name] || ''}
                  onChange={(event: ChangeEvent<HTMLInputElement>) => setCredentialValues((current) => ({ ...current, [field.name]: event.target.value }))}
                  placeholder={editingId ? '留空保持当前授权' : field.description || '按Manifest填写'}
                  autoComplete="new-password"
                />
                {field.description && <small>{field.description}</small>}
              </label>
            ))}
          </div>

          {selectedManifest?.webhook_supported && (
            <label className="form-group materials-webhook-toggle">
              <span className="form-label">事件触发同步</span>
              <span>
                <input
                  type="checkbox"
                  checked={webhookEnabled}
                  onChange={(event: ChangeEvent<HTMLInputElement>) => setWebhookEnabled(event.target.checked)}
                />
                {' '}启用签名事件触发已有同步
              </span>
              <small>事件不会直接修改资料；检测到事件丢失时会要求一次完整校准。</small>
            </label>
          )}

          <div className="materials-step">
            <span className="materials-step-number">2</span>
            <div>
              <h3>选择资料范围</h3>
              <p>范围格式由Manifest声明；系统只会读取授权允许的在线资料。</p>
            </div>
          </div>

          {scopeParseError && <div className="materials-alert tone-danger">{scopeParseError}</div>}

          {!isObjectScope(selectedManifest) && (
            <div className="materials-form-grid">
            {scopePresets(selectedManifest).length > 0 && (
              <label className="form-group">
                <span className="form-label">范围预设</span>
                <select className="form-input" value={resourceScope} onChange={(event: ChangeEvent<HTMLSelectElement>) => setResourceScope(event.target.value)}>
                  {scopePresets(selectedManifest).map((preset) => <option key={preset} value={preset}>{preset}</option>)}
                </select>
              </label>
            )}
            <label className="form-group materials-form-wide">
              <span className="form-label">同步范围</span>
              <input className="form-input form-input-mono" value={resourceScope} onChange={(event: ChangeEvent<HTMLInputElement>) => setResourceScope(event.target.value)} placeholder="填写Manifest声明的范围值" />
              {scopeSchemaHint(selectedManifest) && <small>{scopeSchemaHint(selectedManifest)}</small>}
            </label>
            </div>
          )}

          {isObjectScope(selectedManifest) && (
            <details
              className="materials-advanced materials-scope-editor-details"
              open={!quickConnectApplied}
            >
              <summary>{quickConnectApplied ? '调整同步范围（可选）' : '配置同步范围'}</summary>
              <div className="materials-advanced-field">
                {selectedManifest && (
            <div className="materials-form-grid materials-scope-editor">
              {scopeProperties(selectedManifest).map(([name, property]) => {
                const required = asArray(asRecord(selectedManifest?.scope_schema).required)
                  .includes(name);
                const type = asString(property.type);
                const format = asString(property.format);
                const value = scopeValues[name];
                const enumValues = asArray(property.enum)
                  .filter((item): item is string => typeof item === 'string');
                const setValue = (next: unknown) => {
                  setScopeValues((current) => ({ ...current, [name]: next }));
                };
                return (
                  <label className="form-group" key={name}>
                    <span className="form-label">{name}{required ? ' *' : ''}</span>
                    {enumValues.length > 0 ? (
                      <select
                        className="form-input"
                        value={asString(value)}
                        onChange={(event) => setValue(event.target.value)}
                      >
                        <option value="">请选择</option>
                        {enumValues.map((option) => <option key={option} value={option}>{option}</option>)}
                      </select>
                    ) : type === 'array' ? (
                      <textarea
                        className="form-input form-input-mono"
                        rows={3}
                        value={Array.isArray(value) ? value.join('\n') : ''}
                        onChange={(event) => setValue(
                          event.target.value.split(/\r?\n|,/).map((item) => item.trim()).filter(Boolean),
                        )}
                        placeholder="每行填写一个值"
                      />
                    ) : type === 'boolean' ? (
                      <input
                        type="checkbox"
                        checked={value === true}
                        onChange={(event) => setValue(event.target.checked)}
                      />
                    ) : (
                      <input
                        className="form-input"
                        type={type === 'integer' || type === 'number' ? 'number' : format === 'uri' ? 'url' : 'text'}
                        min={asNumber(property.minimum)}
                        max={asNumber(property.maximum)}
                        step={type === 'integer' ? 1 : type === 'number' ? 'any' : undefined}
                        value={typeof value === 'number' ? value : asString(value)}
                        onChange={(event) => setValue(
                          type === 'integer' || type === 'number'
                            ? (event.target.value === '' ? '' : Number(event.target.value))
                            : event.target.value,
                        )}
                      />
                    )}
                    {asString(property.description) && <small>{asString(property.description)}</small>}
                  </label>
                );
              })}
            </div>
                )}
              </div>
            </details>
          )}

          {isObjectScope(selectedManifest) && (
            <details className="materials-advanced">
              <summary>高级范围预览</summary>
              <div className="materials-advanced-field">
                <pre className="materials-scope-preview">
                  {serializeScope(selectedManifest, scopeValues, resourceScope) || '{}'}
                </pre>
              </div>
            </details>
          )}

          <details className="materials-advanced">
            <summary>高级资料范围</summary>
            <div className="materials-advanced-field">
              <p>仅用于连接器声明的多空间或节点场景，普通接入无需额外配置。</p>
            </div>
          </details>

          <div className="materials-form-actions">
            <button className="btn btn-secondary" type="button" onClick={() => setFormOpen(false)}>取消</button>
            <button className="btn btn-primary" type="button" onClick={() => void saveAndStart()} disabled={saving || !selectedManifest}>
              {saving ? '正在连接并读取…' : editingId ? '保存并重新读取' : '保存并开始读取'}
            </button>
          </div>
        </section>
      )}

      <section className="materials-secondary-card">
        <div className="materials-section-heading">
          <div>
            <span className="settings-hero-kicker">补充方式</span>
            <h2>离线资料上传</h2>
            <p>用于补充在线资料没有的 PRD、接口文档、历史缺陷、数据库说明或设计稿。</p>
          </div>
        </div>
        <div className="materials-upload-row">
          <select className="form-input" value={uploadType} onChange={(event) => setUploadType(event.target.value)}>
            <option value="prd">需求 / PRD</option>
            <option value="openapi">OpenAPI / 接口文档</option>
            <option value="historical_bug">历史缺陷</option>
            <option value="database_schema">数据库结构</option>
            <option value="ui_ux">原型 / 设计稿</option>
          </select>
          <input id="materials-upload-file" className="form-input" type="file" onChange={(event) => setUploadFile(event.target.files?.[0] || null)} />
          <button className="btn btn-secondary" type="button" onClick={() => void uploadSupplement()} disabled={uploading}>
            {uploading ? '上传中…' : '补充上传'}
          </button>
        </div>
      </section>

      <section className="materials-inventory-card">
        <div className="materials-section-heading">
          <div>
            <span className="settings-hero-kicker">统一企业知识库</span>
            <h2>已接入资料</h2>
            <p>在线资料和上传文件统一进入同一企业知识主链。</p>
          </div>
        </div>
        {sources.length === 0 ? (
          <div className="materials-empty-state compact">暂无资料。</div>
        ) : (
          <div className="materials-source-list">
            {sources.map((source) => {
              const online = source.source_origin === 'ONLINE_CONNECTOR'
                || source.source_ref.startsWith('connector://');
              const fingerprint = source.source_identity_fingerprints?.[0];
              return (
                <article className="materials-source-row" key={source.source_id || source.source_ref}>
                  <span className={`materials-source-icon ${online ? 'online' : 'upload'}`}>{online ? '在线' : '文件'}</span>
                  <div className="materials-source-copy">
                    <strong>{source.original_name || '企业资料'}</strong>
                    <span>
                      {online ? '在线资料' : '离线补充资料'} · {source.source_type || '自动识别'}
                      {source.version ? ` · v${source.version}` : ''}
                    </span>
                    <span>
                      最近观测 · {formatTime(source.updated_at_utc || source.last_seen_at_utc, '尚未观测')}
                      {' · '}{permissionScopeLabel(source.permission_scope)}
                    </span>
                    {source.source_updated_at && (
                      <span>来源更新标记 · {source.source_updated_at}</span>
                    )}
                    {fingerprint && (
                      <code>来源指纹 · {fingerprint.slice(0, 12)}…</code>
                    )}
                  </div>
                  <span className="status status-success">{source.status === 'active' ? '可用' : source.status}</span>
                </article>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}

export default Materials;
