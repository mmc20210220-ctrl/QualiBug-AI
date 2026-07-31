import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { getKnowledgeAsset, ingestKnowledge } from '../api/client';
import {
  configureFeishuConnector,
  listKnowledgeConnectors,
  syncKnowledgeConnector,
  testKnowledgeConnector,
  type ConfigureFeishuConnectorInput,
  type KnowledgeConnectorRecord,
} from '../api/knowledge-connectors';
import { useToast } from '../components/useToast';
import { usePageTitle } from '../lib/page-title';
import './Materials.css';

type JsonRecord = Record<string, unknown>;
type AuthMode = ConfigureFeishuConnectorInput['connection_profile']['auth_mode'];

type KnowledgeSource = {
  source_id: string;
  source_ref: string;
  source_type: string;
  original_name: string;
  status: string;
  version?: number;
  source_origin?: string;
};

const MASKED_SECRET = '********';

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
      source_origin: asString(row.source_origin),
    }))
    .filter((row) => Boolean(row.source_id || row.source_ref));
}

function formatTime(value?: string): string {
  if (!value) return '尚未同步';
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
  const [connectorId, setConnectorId] = useState('feishu-main');
  const [displayName, setDisplayName] = useState('飞书企业资料');
  const [resourceScope, setResourceScope] = useState('wiki-all-accessible');
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
    setConnectorId('feishu-main');
    setDisplayName('飞书企业资料');
    setResourceScope('wiki-all-accessible');
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
    setEditingId(connector.connector_instance_id);
    setConnectorId(connector.connector_instance_id);
    setDisplayName(connector.display_name || '飞书企业资料');
    setResourceScope(connector.resource_scope || 'wiki-all-accessible');
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

  const saveConnector = async () => {
    if (!project) return;
    if (!connectorId.trim() || !displayName.trim() || !resourceScope.trim()) {
      toast.show('请完整填写资料源标识、名称和资料范围。', 'warning');
      return;
    }
    setSaving(true);
    try {
      await configureFeishuConnector(project, {
        connector_instance_id: connectorId.trim(),
        display_name: displayName.trim(),
        resource_scope: resourceScope.trim(),
        status: 'ACTIVE',
        connection_profile: profilePayload(),
      });
      setFormOpen(false);
      resetForm();
      await refresh();
      toast.show('飞书在线资料源已加密保存。', 'success');
    } catch (error: unknown) {
      toast.show(error instanceof Error ? error.message : '在线资料源保存失败', 'danger');
    } finally {
      setSaving(false);
    }
  };

  const runOperation = async (
    connector: KnowledgeConnectorRecord,
    action: 'test' | 'sync',
  ) => {
    const id = connector.connector_instance_id;
    setOperation((current) => ({ ...current, [id]: action === 'test' ? '正在验证连接…' : '正在同步资料…' }));
    try {
      if (action === 'test') {
        await testKnowledgeConnector(project, id);
        toast.show(`${connector.display_name || id} 连接验证通过。`, 'success');
      } else {
        const result = await syncKnowledgeConnector(project, id, {
          deletion_policy: 'RETAIN',
          allow_raw_text_fallback: false,
        });
        const count = result.materialized_resource_count ?? result.success_count ?? 0;
        toast.show(`同步完成，已处理 ${count} 份在线资料。`, 'success');
      }
      await refresh();
    } catch (error: unknown) {
      toast.show(error instanceof Error ? error.message : '在线资料源操作失败', 'danger');
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
      toast.show('离线资料已进入同一企业知识主链。', 'success');
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
        <p>请先选择客户项目，再配置在线资料源或上传离线补充资料。</p>
        <Link className="btn btn-primary" to="/settings">前往选择客户</Link>
      </div>
    );
  }

  return (
    <div className="materials-page">
      <header className="page-header materials-header">
        <div>
          <span className="panel-kicker">Enterprise Materials</span>
          <h1>企业资料</h1>
          <p>在线资料源优先，离线上传作为补充；两种来源统一进入 Source Occurrence 与企业知识理解主链。</p>
          <div className="page-summary-strip">
            <span className="summary-pill strong">在线来源 {connectors.length}</span>
            <span className="summary-pill">在线资料 {onlineSources.length}</span>
            <span className="summary-pill">上传补充 {uploadedSources.length}</span>
            <span className="summary-pill">资料总数 {sources.length}</span>
          </div>
        </div>
        <button className="btn btn-primary" type="button" onClick={openCreateForm}>接入在线资料源</button>
      </header>

      {loadError && <div className="materials-alert tone-danger">{loadError}</div>}

      <section className="materials-primary-card">
        <div className="materials-section-heading">
          <div>
            <span className="settings-hero-kicker">主要采集方式</span>
            <h2>在线资料源</h2>
            <p>当前优先支持飞书知识库。连接凭据加密保存，正文只进入现有企业资料主链。</p>
          </div>
          <button className="btn btn-secondary" type="button" onClick={() => void refresh()} disabled={loading}>
            {loading ? '刷新中…' : '刷新状态'}
          </button>
        </div>

        {connectors.length === 0 ? (
          <div className="materials-empty-state">
            <strong>尚未接入在线资料源</strong>
            <span>添加飞书知识库后，系统可按完整分页发现、官方格式导出并持续同步企业资料。</span>
            <button className="btn btn-primary" type="button" onClick={openCreateForm}>添加飞书资料源</button>
          </div>
        ) : (
          <div className="materials-connector-grid">
            {connectors.map((connector) => {
              const tone = connectorTone(connector);
              const busy = Boolean(operation[connector.connector_instance_id]);
              return (
                <article className="materials-connector-card" key={connector.connector_instance_id}>
                  <div className="materials-connector-top">
                    <div>
                      <span className="materials-source-kind">飞书知识库</span>
                      <h3>{connector.display_name || connector.connector_instance_id}</h3>
                      <code>{connector.resource_scope || '未配置资料范围'}</code>
                    </div>
                    <span className={`status status-${tone}`}>
                      {connector.active_sync_epoch_id ? '同步中' : connector.status === 'ACTIVE' ? '已启用' : connector.status}
                    </span>
                  </div>
                  <div className="materials-connector-meta">
                    <div><span>凭据</span><strong>{connector.connection_profile?.credentials_configured ? '已加密配置' : '待配置'}</strong></div>
                    <div><span>最近成功</span><strong>{formatTime(connector.last_successful_sync_at_utc)}</strong></div>
                    <div><span>恢复检查点</span><strong>{connector.connection_profile?.checkpoint_configured ? '已建立' : '首次同步后建立'}</strong></div>
                  </div>
                  {operation[connector.connector_instance_id] && (
                    <div className="materials-operation-note">{operation[connector.connector_instance_id]}</div>
                  )}
                  <div className="materials-card-actions">
                    <button className="btn btn-secondary" type="button" onClick={() => openEditForm(connector)} disabled={busy}>编辑</button>
                    <button className="btn btn-secondary" type="button" onClick={() => void runOperation(connector, 'test')} disabled={busy}>测试连接</button>
                    <button className="btn btn-primary" type="button" onClick={() => void runOperation(connector, 'sync')} disabled={busy || connector.status !== 'ACTIVE'}>立即同步</button>
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </section>

      {formOpen && (
        <section className="materials-config-card" aria-label="飞书在线资料源配置">
          <div className="materials-section-heading">
            <div>
              <span className="settings-hero-kicker">加密连接配置</span>
              <h2>{editingId ? '编辑飞书资料源' : '接入飞书资料源'}</h2>
              <p>密钥仅在保存时提交，后续页面只展示“已配置”，不会回显原值。</p>
            </div>
            <button className="btn btn-ghost" type="button" onClick={() => setFormOpen(false)}>关闭</button>
          </div>
          <div className="materials-form-grid">
            <label className="form-group">
              <span className="form-label">资料源标识</span>
              <input className="form-input form-input-mono" value={connectorId} disabled={Boolean(editingId)} onChange={(event) => setConnectorId(event.target.value)} placeholder="feishu-main" />
            </label>
            <label className="form-group">
              <span className="form-label">显示名称</span>
              <input className="form-input" value={displayName} onChange={(event) => setDisplayName(event.target.value)} placeholder="飞书企业资料" />
            </label>
            <label className="form-group materials-form-wide">
              <span className="form-label">资料范围</span>
              <input className="form-input form-input-mono" value={resourceScope} onChange={(event) => setResourceScope(event.target.value)} placeholder="wiki-all-accessible 或 wiki-space:space_id" />
              <small>支持 wiki-all-accessible、wiki-space:ID、wiki-spaces:ID1,ID2、wiki-node:SPACE:NODE。</small>
            </label>
            <label className="form-group">
              <span className="form-label">鉴权方式</span>
              <select className="form-input" value={authMode} onChange={(event) => setAuthMode(event.target.value as AuthMode)}>
                <option value="internal_app">企业自建应用</option>
                <option value="tenant_access_token">Tenant Access Token</option>
                <option value="user_access_token">User Access Token</option>
              </select>
            </label>
            {authMode === 'internal_app' ? (
              <>
                <label className="form-group">
                  <span className="form-label">App ID</span>
                  <input className="form-input" value={appId} onChange={(event) => setAppId(event.target.value)} placeholder={editingId ? '留空保持当前值' : 'cli_xxx'} />
                </label>
                <label className="form-group materials-form-wide">
                  <span className="form-label">App Secret</span>
                  <input className="form-input" type="password" value={appSecret} onChange={(event) => setAppSecret(event.target.value)} placeholder={editingId ? '留空保持当前值' : '输入应用密钥'} autoComplete="new-password" />
                </label>
              </>
            ) : (
              <label className="form-group materials-form-wide">
                <span className="form-label">{authMode === 'tenant_access_token' ? 'Tenant Access Token' : 'User Access Token'}</span>
                <input className="form-input" type="password" value={authMode === 'tenant_access_token' ? tenantToken : userToken} onChange={(event) => authMode === 'tenant_access_token' ? setTenantToken(event.target.value) : setUserToken(event.target.value)} placeholder={editingId ? '留空保持当前值' : '输入访问令牌'} autoComplete="new-password" />
              </label>
            )}
          </div>
          <div className="materials-form-actions">
            <button className="btn btn-secondary" type="button" onClick={() => setFormOpen(false)}>取消</button>
            <button className="btn btn-primary" type="button" onClick={() => void saveConnector()} disabled={saving}>{saving ? '加密保存中…' : '加密保存'}</button>
          </div>
        </section>
      )}

      <section className="materials-secondary-card">
        <div className="materials-section-heading">
          <div>
            <span className="settings-hero-kicker">补充采集方式</span>
            <h2>离线资料上传</h2>
            <p>用于补充无法在线读取的 PRD、接口文档、历史缺陷、数据库说明或设计稿，不建立第二套知识链。</p>
          </div>
        </div>
        <div className="materials-upload-row">
          <select className="form-input" value={uploadType} onChange={(event) => setUploadType(event.target.value)}>
            <option value="prd">需求 / PRD</option>
            <option value="openapi">OpenAPI / 接口文档</option>
            <option value="historical_bug">历史缺陷</option>
            <option value="database_schema">数据库结构</option>
            <option value="ui_ux">UI / 交互资料</option>
            <option value="test_case">测试资料</option>
            <option value="other_document">其他企业资料</option>
          </select>
          <input id="materials-upload-file" className="form-input" type="file" onChange={(event) => setUploadFile(event.target.files?.[0] || null)} />
          <button className="btn btn-secondary" type="button" onClick={() => void uploadSupplement()} disabled={uploading || !uploadFile}>{uploading ? '上传中…' : '上传补充资料'}</button>
        </div>
      </section>

      <section className="materials-inventory-card">
        <div className="materials-section-heading">
          <div>
            <span className="settings-hero-kicker">统一来源清单</span>
            <h2>已进入企业知识主链的资料</h2>
            <p>在线与上传来源可以共享相同内容资产，但各自保留独立 Source Occurrence 和版本证据。</p>
          </div>
          <span className="status status-neutral">{sources.length} 份</span>
        </div>
        {sources.length === 0 ? (
          <div className="materials-empty-state compact"><span>尚无资料。先同步在线来源，或上传一份离线补充资料。</span></div>
        ) : (
          <div className="materials-source-list">
            {sources.map((source) => {
              const online = source.source_ref.startsWith('connector://');
              return (
                <article key={source.source_id || source.source_ref} className="materials-source-row">
                  <div className={`materials-source-icon ${online ? 'online' : 'upload'}`}>{online ? '↻' : '↑'}</div>
                  <div className="materials-source-copy">
                    <strong>{source.original_name || source.source_ref || source.source_id}</strong>
                    <span>{source.source_type || '企业资料'} · {online ? '在线同步' : '离线上传'}{source.version ? ` · v${source.version}` : ''}</span>
                    <code>{source.source_ref || source.source_id}</code>
                  </div>
                  <span className={`status status-${source.status === 'active' ? 'success' : 'neutral'}`}>{source.status || 'active'}</span>
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
