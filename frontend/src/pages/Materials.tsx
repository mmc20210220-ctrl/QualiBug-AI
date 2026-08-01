import { useCallback, useEffect, useMemo, useState, type ChangeEvent } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { getKnowledgeAsset, ingestKnowledge } from '../api/client';
import {
  connectFeishuKnowledge,
  listKnowledgeConnectors,
  refreshKnowledgeConnector,
  type ConfigureFeishuConnectorInput,
  type KnowledgeConnectorActionResult,
  type KnowledgeConnectorRecord,
} from '../api/knowledge-connectors';
import { ConnectorAcceptancePanel } from '../components/ConnectorAcceptancePanel';
import { ConnectorCoverage } from '../components/ConnectorCoverage';
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
  if (!value) return '尚未完成首次更新';
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
    return `${prefix}。发现 ${discovered} 份，已读取 ${covered} 份，${unsupported} 份资料类型暂不支持。`;
  }
  return `${prefix}，已读取 ${covered} 份在线资料。`;
}

function connectorTone(connector: KnowledgeConnectorRecord): string {
  if (connector.active_sync_epoch_id || connector.auto_sync?.state === 'running') return 'warning';
  if (connector.auto_sync?.maintenance_required_by_user) return 'danger';
  if (connector.auto_sync?.state === 'retrying') return 'warning';
  if (connector.coverage?.status === 'PARTIAL_UNSUPPORTED') return 'warning';
  if (connector.last_successful_sync_epoch_id) return 'success';
  return 'neutral';
}

function connectorLabel(connector: KnowledgeConnectorRecord): string {
  if (connector.active_sync_epoch_id || connector.auto_sync?.state === 'running') return '正在自动更新';
  if (connector.auto_sync?.maintenance_required_by_user) return connector.auto_sync.message || '需要重新授权';
  if (connector.auto_sync?.state === 'retrying') return connector.auto_sync.message || '系统正在自动恢复';
  if (connector.coverage?.status === 'PARTIAL_UNSUPPORTED') {
    return `已读取 ${connector.coverage.covered_count}/${connector.coverage.discovered_count}`;
  }
  if (connector.auto_sync?.message) return connector.auto_sync.message;
  if (connector.last_successful_sync_epoch_id) return '自动更新正常';
  return '等待首次更新';
}

function scopeDraft(resourceScope: string): { mode: ScopeMode; spaceId: string; advanced: string } {
  if (!resourceScope || resourceScope === 'wiki-all-accessible') {
    return { mode: 'all', spaceId: '', advanced: '' };
  }
  if (resourceScope.startsWith('wiki-space:')) {
    return { mode: 'space', spaceId: resourceScope.slice('wiki-space:'.length), advanced: '' };
  }
  return { mode: 'advanced', spaceId: '', advanced: resourceScope };
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
      const [inventory, asset] = await Promise.all([
        listKnowledgeConnectors(project),
        getKnowledgeAsset(project),
      ]);
      setConnectors(inventory.connectors);
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
    setEditingId(connector.connector_instance_id);
    setScopeMode(scope.mode);
    setSpaceId(scope.spaceId);
    setAdvancedScope(scope.advanced);
    setAuthMode(
      connector.connection_profile?.auth_mode === 'tenant_access_token'
        || connector.connection_profile?.auth_mode === 'user_access_token'
        ? connector.connection_profile.auth_mode
        : 'internal_app',
    );
    setAppId('');
    setAppSecret('');
    setTenantToken('');
    setUserToken('');
    setFormOpen(true);
  };

  const profilePayload = (): ConfigureFeishuConnectorInput['connection_profile'] => {
    if (authMode === 'tenant_access_token') {
      return {
        auth_mode: authMode,
        tenant_access_token: tenantToken.trim() || (editingId ? MASKED_SECRET : undefined),
      };
    }
    if (authMode === 'user_access_token') {
      return {
        auth_mode: authMode,
        user_access_token: userToken.trim() || (editingId ? MASKED_SECRET : undefined),
      };
    }
    return {
      auth_mode: authMode,
      app_id: appId.trim() || (editingId ? MASKED_SECRET : undefined),
      app_secret: appSecret.trim() || (editingId ? MASKED_SECRET : undefined),
    };
  };

  const credentialsReady = (): boolean => {
    if (editingId) return true;
    if (authMode === 'tenant_access_token') return Boolean(tenantToken.trim());
    if (authMode === 'user_access_token') return Boolean(userToken.trim());
    return Boolean(appId.trim() && appSecret.trim());
  };

  const resourceScope = (): string => {
    if (scopeMode === 'all') return 'wiki-all-accessible';
    if (scopeMode === 'space') return spaceId.trim() ? `wiki-space:${spaceId.trim()}` : '';
    return advancedScope.trim();
  };

  const saveAndStart = async () => {
    if (!project) return;
    const scope = resourceScope();
    if (!scope) {
      toast.show('请选择同步全部知识库，或填写一个飞书知识空间 ID。', 'warning');
      return;
    }
    if (!credentialsReady()) {
      toast.show('请填写完整的飞书授权信息。', 'warning');
      return;
    }

    const connectorId = editingId || DEFAULT_CONNECTOR_ID;
    setSaving(true);
    setOperation((current) => ({ ...current, [connectorId]: '正在连接飞书并读取资料…' }));
    try {
      const result = await connectFeishuKnowledge(project, {
        connector_instance_id: connectorId,
        display_name: DEFAULT_CONNECTOR_NAME,
        resource_scope: scope,
        status: 'ACTIVE',
        connection_profile: profilePayload(),
      });
      setFormOpen(false);
      resetForm();
      await refresh();
      toast.show(syncCompletionMessage('飞书资料已连接', result.sync), 'success');
    } catch (error: unknown) {
      await refresh();
      toast.show(error instanceof Error ? error.message : '飞书资料连接未完成，请重试。', 'danger');
    } finally {
      setSaving(false);
      setOperation((current) => ({ ...current, [connectorId]: '' }));
    }
  };

  const checkNow = async (connector: KnowledgeConnectorRecord) => {
    const id = connector.connector_instance_id;
    setOperation((current) => ({ ...current, [id]: '正在检查飞书最新资料…' }));
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
          <p>连接一次，系统自动读取、识别、去重、更新和恢复；日常无需维护。</p>
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
            <span className="settings-hero-kicker">自动维护</span>
            <h2>飞书在线资料</h2>
            <p>系统定期检查更新，遇到临时故障自动重试；只有授权失效时才需要你处理。</p>
          </div>
          <button className="btn btn-secondary" type="button" onClick={() => void refresh()} disabled={loading}>
            {loading ? '刷新中…' : '刷新状态'}
          </button>
        </div>

        {connectors.length === 0 ? (
          <div className="materials-empty-state">
            <strong>尚未连接飞书资料</strong>
            <span>填写授权并选择范围，系统会自动验证并完成首次读取。</span>
            <button className="btn btn-primary" type="button" onClick={openCreateForm}>开始连接</button>
          </div>
        ) : (
          <div className="materials-connector-grid">
            {connectors.map((connector) => {
              const busy = Boolean(operation[connector.connector_instance_id]);
              const running = Boolean(connector.active_sync_epoch_id) || connector.auto_sync?.state === 'running';
              const needsHelp = Boolean(
                connector.auto_sync?.maintenance_required_by_user
                || !connector.connection_profile?.credentials_configured,
              );
              return (
                <article className="materials-connector-card" key={connector.connector_instance_id}>
                  <div className="materials-connector-top">
                    <div>
                      <span className="materials-source-kind">飞书知识库</span>
                      <h3>{connector.display_name || DEFAULT_CONNECTOR_NAME}</h3>
                      <span className="materials-simple-scope">
                        {connector.resource_scope === 'wiki-all-accessible'
                          ? '读取全部可访问知识库'
                          : '读取指定资料范围'}
                      </span>
                    </div>
                    <span className={`status status-${connectorTone(connector)}`}>
                      {connectorLabel(connector)}
                    </span>
                  </div>

                  <div className="materials-connector-meta">
                    <div>
                      <span>自动更新</span>
                      <strong>{connector.auto_sync?.enabled === false ? '已关闭' : '已开启'}</strong>
                    </div>
                    <div>
                      <span>最近完成</span>
                      <strong>{formatTime(connector.last_successful_sync_at_utc)}</strong>
                    </div>
                    <div>
                      <span>异常处理</span>
                      <strong>{needsHelp ? '需要重新授权' : '系统自动恢复'}</strong>
                    </div>
                  </div>

                  <ConnectorCoverage coverage={connector.coverage} />

                  <ConnectorAcceptancePanel
                    projectId={project}
                    connectorId={connector.connector_instance_id}
                    disabled={busy || running || needsHelp || connector.status !== 'ACTIVE'}
                  />

                  {(operation[connector.connector_instance_id] || running || connector.auto_sync?.state === 'retrying') && (
                    <div className="materials-operation-note">
                      {operation[connector.connector_instance_id]
                        || connector.auto_sync?.message
                        || '系统正在自动更新资料…'}
                    </div>
                  )}

                  {needsHelp && (
                    <div className="materials-card-actions">
                      <button
                        className="btn btn-primary"
                        type="button"
                        onClick={() => openEditForm(connector)}
                        disabled={busy || running}
                      >
                        重新授权
                      </button>
                    </div>
                  )}

                  <details className="materials-advanced">
                    <summary>遇到问题时</summary>
                    <div className="materials-advanced-field">
                      <p>系统会自动更新和重试。只有需要立即确认最新资料时，才手动检查一次。</p>
                      <button
                        className="btn btn-secondary"
                        type="button"
                        onClick={() => void checkNow(connector)}
                        disabled={busy || running || connector.status !== 'ACTIVE'}
                      >
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
        <section className="materials-config-card" aria-label="飞书资料连接">
          <div className="materials-section-heading">
            <div>
              <span className="settings-hero-kicker">两步完成</span>
              <h2>{editingId ? '重新授权飞书' : '连接飞书资料'}</h2>
              <p>保存后自动验证并读取资料，后续更新和重试由系统处理。</p>
            </div>
            <button className="btn btn-ghost" type="button" onClick={() => setFormOpen(false)}>关闭</button>
          </div>

          <div className="materials-step">
            <span className="materials-step-number">1</span>
            <div>
              <h3>填写飞书授权</h3>
              <p>推荐使用企业自建应用，只需要 App ID 与 App Secret。</p>
            </div>
          </div>

          <div className="materials-form-grid">
            {authMode === 'internal_app' ? (
              <>
                <label className="form-group">
                  <span className="form-label">App ID</span>
                  <input
                    className="form-input"
                    value={appId}
                    onChange={(event: ChangeEvent<HTMLInputElement>) => setAppId(event.target.value)}
                    placeholder={editingId ? '留空保持当前值' : 'cli_xxx'}
                  />
                </label>
                <label className="form-group">
                  <span className="form-label">App Secret</span>
                  <input
                    className="form-input"
                    type="password"
                    value={appSecret}
                    onChange={(event: ChangeEvent<HTMLInputElement>) => setAppSecret(event.target.value)}
                    placeholder={editingId ? '留空保持当前值' : '输入应用密钥'}
                    autoComplete="new-password"
                  />
                </label>
              </>
            ) : (
              <label className="form-group materials-form-wide">
                <span className="form-label">
                  {authMode === 'tenant_access_token' ? 'Tenant Access Token' : 'User Access Token'}
                </span>
                <input
                  className="form-input"
                  type="password"
                  value={authMode === 'tenant_access_token' ? tenantToken : userToken}
                  onChange={(event: ChangeEvent<HTMLInputElement>) => (
                    authMode === 'tenant_access_token'
                      ? setTenantToken(event.target.value)
                      : setUserToken(event.target.value)
                  )}
                  placeholder={editingId ? '留空保持当前值' : '输入访问令牌'}
                  autoComplete="new-password"
                />
              </label>
            )}
          </div>

          <details className="materials-advanced">
            <summary>其他授权方式</summary>
            <div className="materials-advanced-field">
              <select
                className="form-input"
                value={authMode}
                onChange={(event: ChangeEvent<HTMLSelectElement>) => setAuthMode(event.target.value as AuthMode)}
              >
                <option value="internal_app">企业自建应用（推荐）</option>
                <option value="tenant_access_token">Tenant Access Token</option>
                <option value="user_access_token">User Access Token</option>
              </select>
            </div>
          </details>

          <div className="materials-step">
            <span className="materials-step-number">2</span>
            <div>
              <h3>选择资料范围</h3>
              <p>默认读取应用有权限访问的全部知识库。</p>
            </div>
          </div>

          <div className="materials-choice-grid">
            <button
              className={`materials-choice${scopeMode === 'all' ? ' active' : ''}`}
              type="button"
              onClick={() => setScopeMode('all')}
            >
              <strong>全部可访问知识库</strong>
              <span>推荐。权限变化后系统会自动按最新范围读取。</span>
            </button>
            <button
              className={`materials-choice${scopeMode === 'space' ? ' active' : ''}`}
              type="button"
              onClick={() => setScopeMode('space')}
            >
              <strong>指定一个知识空间</strong>
              <span>仅读取一个明确的飞书知识空间。</span>
            </button>
          </div>

          {scopeMode === 'space' && (
            <label className="form-group materials-scope-field">
              <span className="form-label">知识空间 ID</span>
              <input
                className="form-input"
                value={spaceId}
                onChange={(event: ChangeEvent<HTMLInputElement>) => setSpaceId(event.target.value)}
                placeholder="space_id"
              />
            </label>
          )}

          <details className="materials-advanced">
            <summary>高级资料范围</summary>
            <div className="materials-advanced-field">
              <p>仅用于多空间或指定节点场景，普通接入无需填写。</p>
              <input
                className="form-input form-input-mono"
                value={advancedScope}
                onFocus={() => setScopeMode('advanced')}
                onChange={(event: ChangeEvent<HTMLInputElement>) => {
                  setScopeMode('advanced');
                  setAdvancedScope(event.target.value);
                }}
                placeholder="wiki-spaces:ID1,ID2 或 wiki-node:SPACE:NODE"
              />
            </div>
          </details>

          <div className="materials-form-actions">
            <button className="btn btn-secondary" type="button" onClick={() => setFormOpen(false)}>取消</button>
            <button className="btn btn-primary" type="button" onClick={() => void saveAndStart()} disabled={saving}>
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
            <p>用于补充飞书里没有的 PRD、接口文档、历史缺陷、数据库说明或设计稿。</p>
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
          <input
            id="materials-upload-file"
            className="form-input"
            type="file"
            onChange={(event) => setUploadFile(event.target.files?.[0] || null)}
          />
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
            <p>飞书资料和上传文件统一进入同一企业知识主链。</p>
          </div>
        </div>
        {sources.length === 0 ? (
          <div className="materials-empty-state compact">尚无资料。</div>
        ) : (
          <div className="materials-source-list">
            {sources.map((source) => {
              const online = source.source_ref.startsWith('connector://');
              return (
                <article className="materials-source-row" key={source.source_id || source.source_ref}>
                  <span className={`materials-source-icon ${online ? 'online' : 'upload'}`}>{online ? '云' : '文'}</span>
                  <div className="materials-source-copy">
                    <strong>{source.original_name || source.source_id || '企业资料'}</strong>
                    <span>{online ? '飞书在线资料' : '离线补充资料'} · {source.source_type || '自动识别'}</span>
                    <code>{source.source_ref}</code>
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
