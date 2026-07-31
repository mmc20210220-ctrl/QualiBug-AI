import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { getKnowledgeAsset, ingestKnowledge } from '../api/client';
import {
  connectFeishuKnowledge,
  listKnowledgeConnectors,
  refreshKnowledgeConnector,
  type ConfigureFeishuConnectorInput,
  type KnowledgeConnectorRecord,
} from '../api/knowledge-connectors';
import { useToast } from '../components/useToast';
import { usePageTitle } from '../lib/page-title';
import './Materials.css';

type JsonRecord = Record<string, unknown>;
type AuthMode = ConfigureFeishuConnectorInput['connection_profile']['auth_mode'];
type ScopeMode = 'all' | 'space' | 'advanced';

type KnowledgeSource = {
  source_id: string;
  source_ref: string;
  source_type: string;
  original_name: string;
  status: string;
  version?: number;
};

const MASKED_SECRET = '********';
const DEFAULT_CONNECTOR_ID = 'feishu-main';
const DEFAULT_CONNECTOR_NAME = '飞书企业资料';

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

function formatTime(value?: string): string {
  if (!value) return '尚未更新';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString('zh-CN', { hour12: false });
}

function connectorTone(connector: KnowledgeConnectorRecord): string {
  if (connector.active_sync_epoch_id) return 'warning';
  if (connector.last_failed_sync_epoch_id && !connector.last_successful_sync_epoch_id) return 'danger';
  if (connector.last_successful_sync_epoch_id) return 'success';
  return 'neutral';
}

function connectorLabel(connector: KnowledgeConnectorRecord): string {
  if (connector.active_sync_epoch_id) return '正在更新';
  if (connector.last_failed_sync_epoch_id && !connector.last_successful_sync_epoch_id) return '需要重新授权';
  if (connector.last_successful_sync_epoch_id) return '已连接';
  return connector.connection_profile?.credentials_configured ? '等待首次更新' : '需要授权';
}

function scopeDraft(resourceScope: string): {
  mode: ScopeMode;
  spaceId: string;
  advancedScope: string;
} {
  if (!resourceScope || resourceScope === 'wiki-all-accessible') {
    return { mode: 'all', spaceId: '', advancedScope: '' };
  }
  if (resourceScope.startsWith('wiki-space:')) {
    return {
      mode: 'space',
      spaceId: resourceScope.slice('wiki-space:'.length),
      advancedScope: '',
    };
  }
  return { mode: 'advanced', spaceId: '', advancedScope: resourceScope };
}

export function Materials() {
  usePageTitle('企业资料');
  const [params] = useSearchParams();
  const project = params.get('project')?.trim() || '';
  const toast = useToast();

  const [connectors, setConnectors] = useState<KnowledgeConnectorRecord[]>([]);
  const [sources, setSources] = useState<KnowledgeSource[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [operation, setOperation] = useState<Record<string, string>>({});

  const [formOpen, setFormOpen] = useState(false);
  const [editingId, setEditingId] = useState('');
  const [originalAuthMode, setOriginalAuthMode] = useState<AuthMode>('internal_app');
  const [scopeMode, setScopeMode] = useState<ScopeMode>('all');
  const [spaceId, setSpaceId] = useState('');
  const [advancedScope, setAdvancedScope] = useState('');
  const [authMode, setAuthMode] = useState<AuthMode>('internal_app');
  const [appId, setAppId] = useState('');
  const [appSecret, setAppSecret] = useState('');
  const [tenantToken, setTenantToken] = useState('');
  const [userToken, setUserToken] = useState('');
  const [saving, setSaving] = useState(false);

  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadType, setUploadType] = useState('prd');
  const [uploading, setUploading] = useState(false);

  const refresh = useCallback(async () => {
    if (!project) {
      setConnectors([]);
      setSources([]);
      return;
    }
    setLoading(true);
    setLoadError('');
    try {
      const [connectorInventory, asset] = await Promise.all([
        listKnowledgeConnectors(project),
        getKnowledgeAsset(project),
      ]);
      setConnectors(connectorInventory.connectors);
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
    setOriginalAuthMode('internal_app');
    setScopeMode('all');
    setSpaceId('');
    setAdvancedScope('');
    setAuthMode('internal_app');
    setAppId('');
    setAppSecret('');
    setTenantToken('');
    setUserToken('');
  };

  const openCreateForm = () => {
    resetForm();
    setFormOpen(true);
  };

  const openEditForm = (connector: KnowledgeConnectorRecord) => {
    const scope = scopeDraft(connector.resource_scope);
    const currentAuth = connector.connection_profile?.auth_mode;
    const nextAuth: AuthMode = currentAuth === 'tenant_access_token' || currentAuth === 'user_access_token'
      ? currentAuth
      : 'internal_app';
    setEditingId(connector.connector_instance_id);
    setOriginalAuthMode(nextAuth);
    setScopeMode(scope.mode);
    setSpaceId(scope.spaceId);
    setAdvancedScope(scope.advancedScope);
    setAuthMode(nextAuth);
    setAppId('');
    setAppSecret('');
    setTenantToken('');
    setUserToken('');
    setFormOpen(true);
  };

  const resourceScope = (): string => {
    if (scopeMode === 'all') return 'wiki-all-accessible';
    if (scopeMode === 'space') return `wiki-space:${spaceId.trim()}`;
    return advancedScope.trim();
  };

  const profilePayload = (): ConfigureFeishuConnectorInput['connection_profile'] => {
    const preserveExisting = Boolean(editingId && authMode === originalAuthMode);
    if (authMode === 'tenant_access_token') {
      return {
        auth_mode: authMode,
        tenant_access_token: tenantToken.trim() || (preserveExisting ? MASKED_SECRET : undefined),
      };
    }
    if (authMode === 'user_access_token') {
      return {
        auth_mode: authMode,
        user_access_token: userToken.trim() || (preserveExisting ? MASKED_SECRET : undefined),
      };
    }
    return {
      auth_mode: authMode,
      app_id: appId.trim() || (preserveExisting ? MASKED_SECRET : undefined),
      app_secret: appSecret.trim() || (preserveExisting ? MASKED_SECRET : undefined),
    };
  };

  const credentialsReady = (): boolean => {
    if (editingId && authMode === originalAuthMode) return true;
    if (authMode === 'internal_app') return Boolean(appId.trim() && appSecret.trim());
    if (authMode === 'tenant_access_token') return Boolean(tenantToken.trim());
    return Boolean(userToken.trim());
  };

  const saveAndStart = async () => {
    if (!project) return;
    const scope = resourceScope();
    if (!scope || (scopeMode === 'space' && !spaceId.trim())) {
      toast.show('请选择同步全部知识库，或填写一个飞书知识空间 ID。', 'warning');
      return;
    }
    if (!credentialsReady()) {
      toast.show('请填写完整的飞书授权信息。', 'warning');
      return;
    }

    const connectorId = editingId || DEFAULT_CONNECTOR_ID;
    setSaving(true);
    setOperation((current) => ({ ...current, [connectorId]: '正在连接飞书并同步资料…' }));
    try {
      const result = await connectFeishuKnowledge(project, {
        connector_instance_id: connectorId,
        display_name: DEFAULT_CONNECTOR_NAME,
        resource_scope: scope,
        status: 'ACTIVE',
        connection_profile: profilePayload(),
      });
      const count = result.sync.materialized_resource_count ?? result.sync.success_count ?? 0;
      setFormOpen(false);
      resetForm();
      await refresh();
      toast.show(`飞书资料已连接，并同步 ${count} 份资料。后续更新由系统自动维护。`, 'success');
    } catch (error: unknown) {
      await refresh();
      toast.show(error instanceof Error ? error.message : '飞书资料连接未完成，请重试。', 'danger');
    } finally {
      setSaving(false);
      setOperation((current) => ({ ...current, [connectorId]: '' }));
    }
  };

  const updateNow = async (connector: KnowledgeConnectorRecord) => {
    const id = connector.connector_instance_id;
    setOperation((current) => ({ ...current, [id]: '正在读取飞书最新资料…' }));
    try {
      const result = await refreshKnowledgeConnector(project, id);
      const count = result.materialized_resource_count ?? result.success_count ?? 0;
      await refresh();
      toast.show(`资料已更新，共处理 ${count} 份在线资料。`, 'success');
    } catch (error: unknown) {
      await refresh();
      toast.show(error instanceof Error ? error.message : '资料更新未完成，请重试。', 'danger');
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
      toast.show('补充资料已加入企业知识库。', 'success');
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
        <p>请先选择客户项目，再接入飞书资料或上传补充文件。</p>
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
          <p>连接一次，系统自动读取、识别、去重和维护企业资料；离线上传只作为补充。</p>
          <div className="page-summary-strip">
            <span className="summary-pill strong">在线来源 {connectors.length}</span>
            <span className="summary-pill">在线资料 {onlineSources.length}</span>
            <span className="summary-pill">上传补充 {uploadedSources.length}</span>
            <span className="summary-pill">资料总数 {sources.length}</span>
          </div>
        </div>
        {connectors.length === 0 && (
          <button className="btn btn-primary" type="button" onClick={openCreateForm}>
            接入飞书资料
          </button>
        )}
      </header>

      {loadError && <div className="materials-alert tone-danger">{loadError}</div>}

      <section className="materials-primary-card">
        <div className="materials-section-heading">
          <div>
            <span className="settings-hero-kicker">主要采集方式</span>
            <h2>飞书在线资料</h2>
            <p>用户只负责授权和选择范围，资料更新、版本维护和异常恢复由系统处理。</p>
          </div>
          <button className="btn btn-secondary" type="button" onClick={() => void refresh()} disabled={loading}>
            {loading ? '刷新中…' : '刷新状态'}
          </button>
        </div>

        {connectors.length === 0 ? (
          <div className="materials-empty-state">
            <strong>尚未连接飞书资料</strong>
            <span>填写 App ID 与 App Secret，保存后系统会自动验证并完成首次同步。</span>
            <button className="btn btn-primary" type="button" onClick={openCreateForm}>
              开始连接
            </button>
          </div>
        ) : (
          <div className="materials-connector-grid">
            {connectors.map((connector) => {
              const busy = Boolean(operation[connector.connector_instance_id]);
              const running = Boolean(connector.active_sync_epoch_id);
              return (
                <article className="materials-connector-card" key={connector.connector_instance_id}>
                  <div className="materials-connector-top">
                    <div>
                      <span className="materials-source-kind">飞书知识库</span>
                      <h3>{connector.display_name || DEFAULT_CONNECTOR_NAME}</h3>
                      <span className="materials-simple-scope">
                        {connector.resource_scope === 'wiki-all-accessible' ? '同步全部可访问知识库' : '同步指定资料范围'}
                      </span>
                    </div>
                    <span className={`status status-${connectorTone(connector)}`}>
                      {connectorLabel(connector)}
                    </span>
                  </div>

                  <div className="materials-connector-meta">
                    <div>
                      <span>授权状态</span>
                      <strong>{connector.connection_profile?.credentials_configured ? '已安全保存' : '需要重新授权'}</strong>
                    </div>
                    <div>
                      <span>最近更新</span>
                      <strong>{formatTime(connector.last_successful_sync_at_utc)}</strong>
                    </div>
                    <div>
                      <span>维护方式</span>
                      <strong>系统自动维护</strong>
                    </div>
                  </div>

                  {(operation[connector.connector_instance_id] || running) && (
                    <div className="materials-operation-note">
                      {operation[connector.connector_instance_id] || '系统正在更新资料，请稍候…'}
                    </div>
                  )}

                  <div className="materials-card-actions">
                    <button
                      className="btn btn-secondary"
                      type="button"
                      onClick={() => openEditForm(connector)}
                      disabled={busy || running}
                    >
                      重新授权
                    </button>
                    <button
                      className="btn btn-primary"
                      type="button"
                      onClick={() => void updateNow(connector)}
                      disabled={busy || running || connector.status !== 'ACTIVE'}
                    >
                      {running ? '正在更新' : '立即更新'}
                    </button>
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </section>

      {formOpen && (
        <section className="materials-config-card" aria-label="飞书资料连接">
          <div className="materials-section-heading">
            <div>
              <span className="settings-hero-kicker">两步完成</span>
              <h2>{editingId ? '重新授权飞书' : '连接飞书资料'}</h2>
              <p>保存后系统会自动验证连接并同步资料，不需要再做其他配置。</p>
            </div>
            <button className="btn btn-ghost" type="button" onClick={() => setFormOpen(false)}>关闭</button>
          </div>

          <div className="materials-step">
            <span className="materials-step-number">1</span>
            <div>
              <h3>填写飞书授权</h3>
              <p>推荐使用企业自建应用。密钥只加密保存，不会在页面回显。</p>
            </div>
          </div>

          {authMode === 'internal_app' ? (
            <div className="materials-form-grid">
              <label className="form-group">
                <span className="form-label">App ID</span>
                <input
                  className="form-input"
                  value={appId}
                  onChange={(event) => setAppId(event.target.value)}
                  placeholder={editingId ? '留空保持当前值' : 'cli_xxx'}
                  autoComplete="off"
                />
              </label>
              <label className="form-group">
                <span className="form-label">App Secret</span>
                <input
                  className="form-input"
                  type="password"
                  value={appSecret}
                  onChange={(event) => setAppSecret(event.target.value)}
                  placeholder={editingId ? '留空保持当前值' : '输入应用密钥'}
                  autoComplete="new-password"
                />
              </label>
            </div>
          ) : (
            <div className="materials-form-grid">
              <label className="form-group materials-form-wide">
                <span className="form-label">
                  {authMode === 'tenant_access_token' ? 'Tenant Access Token' : 'User Access Token'}
                </span>
                <input
                  className="form-input"
                  type="password"
                  value={authMode === 'tenant_access_token' ? tenantToken : userToken}
                  onChange={(event) => (
                    authMode === 'tenant_access_token'
                      ? setTenantToken(event.target.value)
                      : setUserToken(event.target.value)
                  )}
                  placeholder={editingId ? '留空保持当前值' : '输入访问令牌'}
                  autoComplete="new-password"
                />
              </label>
            </div>
          )}

          <details className="materials-advanced">
            <summary>其他授权方式</summary>
            <label className="form-group materials-advanced-field">
              <span className="form-label">授权方式</span>
              <select
                className="form-input"
                value={authMode}
                onChange={(event) => setAuthMode(event.target.value as AuthMode)}
              >
                <option value="internal_app">企业自建应用（推荐）</option>
                <option value="tenant_access_token">Tenant Access Token</option>
                <option value="user_access_token">User Access Token</option>
              </select>
            </label>
          </details>

          <div className="materials-step">
            <span className="materials-step-number">2</span>
            <div>
              <h3>选择资料范围</h3>
              <p>大多数企业直接选择全部知识库；需要隔离时再指定空间。</p>
            </div>
          </div>

          <div className="materials-choice-grid">
            <button
              className={`materials-choice${scopeMode === 'all' ? ' active' : ''}`}
              type="button"
              onClick={() => setScopeMode('all')}
            >
              <strong>全部可访问知识库</strong>
              <span>推荐，后续新增资料也会自动纳入。</span>
            </button>
            <button
              className={`materials-choice${scopeMode === 'space' ? ' active' : ''}`}
              type="button"
              onClick={() => setScopeMode('space')}
            >
              <strong>指定一个知识空间</strong>
              <span>只同步一个明确的飞书知识空间。</span>
            </button>
          </div>

          {scopeMode === 'space' && (
            <label className="form-group materials-scope-field">
              <span className="form-label">知识空间 ID</span>
              <input
                className="form-input form-input-mono"
                value={spaceId}
                onChange={(event) => setSpaceId(event.target.value)}
                placeholder="填写飞书知识空间 ID"
              />
            </label>
          )}

          <details className="materials-advanced" open={scopeMode === 'advanced'}>
            <summary>高级资料范围</summary>
            <div className="materials-advanced-field">
              <p>仅用于多个空间或指定节点。普通使用无需填写。</p>
              <input
                className="form-input form-input-mono"
                value={advancedScope}
                onFocus={() => setScopeMode('advanced')}
                onChange={(event) => {
                  setScopeMode('advanced');
                  setAdvancedScope(event.target.value);
                }}
                placeholder="wiki-spaces:ID1,ID2 或 wiki-node:SPACE:NODE"
              />
            </div>
          </details>

          <div className="materials-form-actions">
            <button className="btn btn-secondary" type="button" onClick={() => setFormOpen(false)}>
              取消
            </button>
            <button className="btn btn-primary" type="button" onClick={() => void saveAndStart()} disabled={saving}>
              {saving ? '正在连接并同步…' : editingId ? '保存并更新资料' : '保存并开始同步'}
            </button>
          </div>
        </section>
      )}

      <section className="materials-secondary-card">
        <div className="materials-section-heading">
          <div>
            <span className="settings-hero-kicker">补充采集方式</span>
            <h2>上传补充资料</h2>
            <p>仅用于飞书中没有的 PRD、接口文档、历史缺陷、数据库说明或设计稿。</p>
          </div>
        </div>
        <div className="materials-upload-row">
          <select className="form-input" value={uploadType} onChange={(event) => setUploadType(event.target.value)}>
            <option value="prd">需求 / PRD</option>
            <option value="openapi">OpenAPI / 接口文档</option>
            <option value="historical_bug">历史缺陷</option>
            <option value="database_schema">数据库结构</option>
            <option value="ui_ux">原型 / 设计稿</option>
            <option value="other_document">其他资料</option>
          </select>
          <input
            id="materials-upload-file"
            className="form-input"
            type="file"
            onChange={(event) => setUploadFile(event.target.files?.[0] || null)}
          />
          <button className="btn btn-secondary" type="button" onClick={() => void uploadSupplement()} disabled={uploading}>
            {uploading ? '上传中…' : '上传补充'}
          </button>
        </div>
      </section>

      <section className="materials-inventory-card">
        <div className="materials-section-heading">
          <div>
            <span className="settings-hero-kicker">统一来源清单</span>
            <h2>已纳入理解的资料</h2>
            <p>在线与上传资料会合并为统一企业知识库，系统自动去重并保留来源证据。</p>
          </div>
        </div>
        {sources.length === 0 ? (
          <div className="materials-empty-state compact">尚无企业资料。</div>
        ) : (
          <div className="materials-source-list">
            {sources.map((source) => {
              const online = source.source_ref.startsWith('connector://');
              return (
                <article className="materials-source-row" key={source.source_id || source.source_ref}>
                  <span className={`materials-source-icon ${online ? 'online' : 'upload'}`}>
                    {online ? '云' : '件'}
                  </span>
                  <div className="materials-source-copy">
                    <strong>{source.original_name || source.source_ref || source.source_id}</strong>
                    <span>{online ? '在线资料' : '上传补充'} · {source.source_type || '自动识别'}</span>
                    <code>{source.source_ref || source.source_id}</code>
                  </div>
                  <span className="status status-neutral">
                    {source.version ? `版本 ${source.version}` : source.status}
                  </span>
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
