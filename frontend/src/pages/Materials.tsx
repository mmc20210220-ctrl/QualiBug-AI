import { useCallback, useEffect, useMemo, useRef, useState, type ChangeEvent } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { getKnowledgeAsset, ingestKnowledge } from '../api/client';
import {
  configureKnowledgeConnector,
  connectKnowledgeConnector,
  listConnectorResources,
  listConnectorTypes,
  listKnowledgeConnectors,
  pauseKnowledgeConnector,
  preflightConnectorSource,
  reauthorizeKnowledgeConnector,
  refreshKnowledgeConnector,
  resumeKnowledgeConnector,
  startKnowledgeConnectorOAuth,
  type ConfigureConnectorInput,
  type ConnectorManifest,
  type ConnectorResourceInventory,
  type ConnectorSourcePreflight,
  type KnowledgeConnectorRecord,
} from '../api/knowledge-connectors';
import { ConnectorAcceptancePanel } from '../components/ConnectorAcceptancePanel';
import { ConnectorCoverage } from '../components/ConnectorCoverage';
import { ConnectorResourcePreview } from '../components/materials/ConnectorResourcePreview';
import { useToast } from '../components/useToast';
import { materialSourceTypeLabel, normalizeMaterialSourceType } from '../lib/material-type-presentation';
import {
  DEFAULT_CONNECTOR_ID,
  DEFAULT_CONNECTOR_NAME,
  applyQuickConnectUrl,
  authModeLabel,
  connectorFreshnessLabel,
  connectorHealthActionLabel,
  connectorHealthLabel,
  connectorLabel,
  connectorOauthLabel,
  connectorOauthRefreshLabel,
  connectorTone,
  connectorWebhookLabel,
  credentialFieldLabel,
  defaultScopeValues,
  formatTime,
  isObjectScope,
  manifestFields,
  missingRequiredScopeFields,
  parseScopeValues,
  permissionScopeLabel,
  quickConnectManifests,
  scopePresets,
  scopeProperties,
  scopeSchemaHint,
  serializeScope,
  sourceRows,
  syncCompletionMessage,
  type KnowledgeSource,
  type ScopeValues,
} from '../lib/materials-presentation';
import { usePageTitle } from '../lib/page-title';
import { asArray, asOptionalNumber, asRecord, asString } from '../lib/value-guards';
import './Materials.css';

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
  const refreshGenerationRef = useRef(0);

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
    const generation = ++refreshGenerationRef.current;
    if (!project) {
      setConnectors([]);
      setManifests([]);
      setResourcePreviews({});
      setSources([]);
      setLoading(false);
      setLoadError('');
      return;
    }
    setLoading(true);
    setLoadError('');
    setResourcePreviews({});

    const inventoryPromise = listKnowledgeConnectors(project);
    const assetPromise = getKnowledgeAsset(project);
    const catalogPromise = listConnectorTypes();
    const previewsPromise = inventoryPromise.then(async (inventory) => {
      const settled = await Promise.allSettled(
        inventory.connectors.map(async (connector) => [
          connector.connector_instance_id,
          await listConnectorResources(project, connector.connector_instance_id),
        ] as const),
      );
      const previews: Record<string, ConnectorResourceInventory> = {};
      settled.forEach((result) => {
        if (result.status === 'fulfilled') {
          previews[result.value[0]] = result.value[1];
        }
      });
      return previews;
    }).catch((): Record<string, ConnectorResourceInventory> => ({}));

    try {
      const [inventory, asset, catalog] = await Promise.all([
        inventoryPromise,
        assetPromise,
        catalogPromise,
      ]);
      if (generation !== refreshGenerationRef.current) return;

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
      setSources(sourceRows(asset));

      void previewsPromise.then((previews) => {
        if (generation === refreshGenerationRef.current) {
          setResourcePreviews(previews);
        }
      });
    } catch (error: unknown) {
      if (generation === refreshGenerationRef.current) {
        setLoadError(error instanceof Error ? error.message : '企业资料加载失败');
      }
    } finally {
      if (generation === refreshGenerationRef.current) {
        setLoading(false);
      }
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

  const activeSources = sources.filter((item) => item.status === 'active');
  const activeCount = activeSources.length;
  const processingCount = sources.filter((item) => item.status === 'processing').length;
  const failedCount = sources.filter((item) => ['failed', 'degraded'].includes(String(item.status || ''))).length;
  const sourceTypeCounts = new Map<string, number>();
  activeSources.forEach((item) => {
    const key = normalizeMaterialSourceType(item.source_type);
    sourceTypeCounts.set(key, (sourceTypeCounts.get(key) || 0) + 1);
  });
  const observedTypeCount = sourceTypeCounts.size;
  const topSourceTypes = [...sourceTypeCounts.entries()]
    .sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]))
    .slice(0, 6);
  const parseHeadline = sources.length === 0
    ? '等待接入资料'
    : failedCount > 0
      ? `${failedCount} 份资料解析失败，需要处理`
      : processingCount > 0
        ? `${processingCount} 份资料仍在解析中`
        : `${activeCount} 份资料已进入企业知识主链`;

  return (
    <div className="materials-page">
      <header className="page-header materials-header">
        <div>
          <span className="panel-kicker">Enterprise Materials</span>
          <h1>企业资料</h1>
          <p>连接一次，系统自动读取、识别、去重、更新和恢复；日常无需维护。</p>
        </div>
        <button className="btn btn-primary" type="button" onClick={openCreateForm}>
          接入在线资料
        </button>
      </header>

      <section className="customer-summary-grid materials-readiness-grid">
        {[
          { label: '资料总数', value: sources.length, tone: sources.length > 0 ? 'primary' : 'neutral', note: '已进入企业知识主链的资料数量' },
          { label: '已生效', value: activeCount, tone: activeCount > 0 ? 'success' : 'neutral', note: '当前可被后续链路消费的真实 active 资料' },
          { label: '资料类型', value: observedTypeCount, tone: observedTypeCount > 0 ? 'success' : 'neutral', note: observedTypeCount > 0 ? '由真实 active source 动态统计，不设固定类型白名单' : '形成 active 资料后自动展示实际类型' },
          { label: '异常资料', value: failedCount, tone: failedCount > 0 ? 'danger' : 'neutral', note: failedCount > 0 ? '解析失败会直接影响后续执行' : '当前无失败资料' },
        ].map((item) => (
          <article key={item.label} className={`customer-summary-card tone-${item.tone}`}>
            <span>{item.label}</span>
            <strong>{item.value}</strong>
            <small>{item.note}</small>
          </article>
        ))}
      </section>

      <section className="materials-secondary-card">
        <div className="materials-section-heading">
          <div>
            <span className="panel-kicker">解析结果</span>
            <h2>{parseHeadline}</h2>
            <p>只有真实进入企业知识主链并成功解析的资料，才会被后续扫描和证据链消费。</p>
          </div>
        </div>
        <div className="customer-secondary-grid">
          <article className="customer-secondary-card">
            <span className="customer-value-kicker">资料来源结构</span>
            <h3>在线 {onlineSources.length} 份 · 文件补充 {uploadedSources.length} 份</h3>
            <p>在线资料作为默认主来源持续更新，文件上传用于补充在线来源没有覆盖的内容；两者最终进入同一企业知识主链。</p>
          </article>
          <article className="customer-secondary-card">
            <span className="customer-value-kicker">解析状态</span>
            <h3>{processingCount > 0 ? '仍在处理' : failedCount > 0 ? '存在失败项' : '当前稳定可用'}</h3>
            <p>处理中 {processingCount} 份，失败 {failedCount} 份。</p>
          </article>
          <article className="customer-secondary-card">
            <span className="customer-value-kicker">资料类型结构</span>
            <h3>{topSourceTypes.length > 0 ? topSourceTypes.map(([type]) => materialSourceTypeLabel(type)).join('、') : '等待可读资料'}</h3>
            <p>
              {topSourceTypes.length > 0
                ? `${topSourceTypes.map(([type, count]) => `${materialSourceTypeLabel(type)} ${count} 份`).join('，')}。类型来自后端真实 source_type，未知类型会原样展示。`
                : '形成真实 active 资料后会在这里展示当前项目的动态资料类型分布。'}
            </p>
          </article>
        </div>
      </section>

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
                {manifests.length === 0 && (
                  <option value="">
                    {loading ? '正在加载连接器清单…' : loadError ? '清单不可用：请查看页面顶部错误' : '暂无可用连接器'}
                  </option>
                )}
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
                        min={asOptionalNumber(property.minimum)}
                        max={asOptionalNumber(property.maximum)}
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
            <p>用于补充在线资料没有覆盖的需求、接口、UI/UX、历史缺陷、数据库说明或设计资料；当前上传分类只代表后端已提供的显式入口，不代表企业资料类型全集。</p>
          </div>
        </div>
        <div className="materials-upload-row">
          <select className="form-input" value={uploadType} onChange={(event) => setUploadType(event.target.value)}>
            <option value="prd">需求 / PRD</option>
            <option value="openapi">OpenAPI / 接口文档</option>
            <option value="historical_bug">历史缺陷</option>
            <option value="database_schema">数据库结构</option>
            <option value="ui_ux">UI / UX 设计 / 原型</option>
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
                      {online ? '在线资料' : '离线补充资料'} · {materialSourceTypeLabel(normalizeMaterialSourceType(source.source_type))}
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
