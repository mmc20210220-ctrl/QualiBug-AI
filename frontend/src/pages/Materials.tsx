import { useCallback, useEffect, useMemo, useState, type ChangeEvent } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { getKnowledgeAsset, ingestKnowledge } from '../api/client';
import {
  connectKnowledgeConnector,
  listConnectorResources,
  listConnectorTypes,
  listKnowledgeConnectors,
  pauseKnowledgeConnector,
  reauthorizeKnowledgeConnector,
  refreshKnowledgeConnector,
  resumeKnowledgeConnector,
  type ConfigureConnectorInput,
  type ConnectorManifest,
  type ConnectorResourceInventory,
  type KnowledgeConnectorActionResult,
  type KnowledgeConnectorRecord,
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
};

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

function sourceRows(payload: unknown): KnowledgeSource[] {
  const root = asRecord(payload);
  const asset = asRecord(root.knowledge_asset || root.data || root);
  return asArray(asset.sources)
    .map(asRecord)
    .map((row) => ({
      source_id: asString(row.source_id) || asString(row.source_occurrence_id),
      source_ref: asString(row.source_ref) || asString(row.external_ref),
      source_type: asString(row.source_type),
      original_name: asString(row.original_name) || asString(row.filename),
      status: asString(row.status) || 'active',
      version: asNumber(row.version) || asNumber(row.occurrence_version),
    }))
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

function connectorTone(connector: KnowledgeConnectorRecord): string {
  if (connector.active_sync_epoch_id || connector.auto_sync?.state === 'running') return 'warning';
  if (connector.auto_sync?.maintenance_required_by_user || connector.connection_profile?.reauthorization_required) return 'danger';
  if (connector.auto_sync?.state === 'retrying') return 'warning';
  if (connector.coverage?.status === 'PARTIAL_UNSUPPORTED') return 'warning';
  if (connector.last_successful_sync_epoch_id) return 'success';
  return 'neutral';
}

function connectorLabel(connector: KnowledgeConnectorRecord): string {
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
  const [authMode, setAuthMode] = useState('');
  const [credentialValues, setCredentialValues] = useState<Record<string, string>>({});
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
      setConnectorType((current) => (
        current && catalog.connector_types.some((manifest) => manifest.connector_type === current)
          ? current
          : catalog.connector_types[0]?.connector_type || ''
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
    () => sources.filter((source) => source.source_ref.startsWith('connector://')),
    [sources],
  );
  const uploadedSources = useMemo(
    () => sources.filter((source) => !source.source_ref.startsWith('connector://')),
    [sources],
  );

  const resetForm = () => {
    setEditingId('');
    const firstManifest = manifests[0];
    setConnectorType(firstManifest?.connector_type || '');
    setResourceScope(scopePresets(firstManifest)[0] || '');
    setAuthMode(firstManifest?.auth_modes[0] || '');
    setCredentialValues({});
  };

  const openCreateForm = () => {
    resetForm();
    setFormOpen(true);
  };

  const openEditForm = (connector: KnowledgeConnectorRecord) => {
    setEditingId(connector.connector_instance_id);
    setConnectorType(connector.connector_type);
    setResourceScope(connector.resource_scope);
    const mode = connector.connection_profile?.auth_mode
      || manifests.find((manifest) => manifest.connector_type === connector.connector_type)?.auth_modes[0]
      || '';
    setAuthMode(mode);
    setCredentialValues({});
    setFormOpen(true);
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
    if (editingId) return true;
    return selectedCredentialFields.every(
      (field) => !field.required || Boolean(credentialValues[field.name]?.trim()),
    );
  };

  const saveAndStart = async () => {
    if (!project) return;
    const scope = resourceScope.trim();
    if (!scope) {
      toast.show('请选择同步范围，或填写Manifest声明的范围值。', 'warning');
      return;
    }
    if (!selectedManifest) {
      toast.show('请先选择一个可用的连接器类型。', 'warning');
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
      const result = await connectKnowledgeConnector(project, {
        connector_type: selectedManifest.connector_type,
        connector_instance_id: connectorId,
        display_name: selectedManifest.display_name || DEFAULT_CONNECTOR_NAME,
        resource_scope: scope,
        status: 'ACTIVE',
        connection_profile: profilePayload(),
      });
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
      if (action === 'reauthorize') await reauthorizeKnowledgeConnector(project, id);
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
                  setAuthMode(manifest?.auth_modes[0] || '');
                  setResourceScope(scopePresets(manifest)[0] || '');
                  setCredentialValues({});
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
                  {selectedManifest.auth_modes.map((mode) => <option key={mode} value={mode}>{mode}</option>)}
                </select>
              </div>
            </details>
          )}

          <div className="materials-form-grid">
            {selectedCredentialFields.map((field) => (
              <label className="form-group" key={field.name}>
                <span className="form-label">{field.name}{field.required ? ' *' : ''}</span>
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

          <div className="materials-step">
            <span className="materials-step-number">2</span>
            <div>
              <h3>选择资料范围</h3>
              <p>范围格式由Manifest声明；系统只会读取授权允许的在线资料。</p>
            </div>
          </div>

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
              const online = source.source_ref.startsWith('connector://');
              return (
                <article className="materials-source-row" key={source.source_id || source.source_ref}>
                  <span className={`materials-source-icon ${online ? 'online' : 'upload'}`}>{online ? '在线' : '文件'}</span>
                  <div className="materials-source-copy">
                    <strong>{source.original_name || '企业资料'}</strong>
                    <span>
                      {online ? '在线资料' : '离线补充资料'} · {source.source_type || '自动识别'}
                      {source.version ? ` · v${source.version}` : ''}
                    </span>
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
