import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { emitScanCompleted } from '../api/data';
import { getServiceCredentials, runV12Scan, type V12ScanResult } from '../api/client';
import {
  issueEnterpriseExecutionApproval,
  listEnterpriseSourceAssets,
  runEnterpriseScan,
  type EnterpriseScanResult,
  type SourceAssetSummary,
  type SourceManifest,
} from '../api/enterprise';
import { useToast } from '../components/useToast';
import { usePageTitle } from '../lib/page-title';
import { useProjectNavigation } from '../lib/project-navigation';

const API_SOURCE_TYPES = new Set(['openapi', 'api_document', 'api_spec']);

type RunMode = 'plan' | 'execute';
type ApprovalAction = 'issue_approval';
type DataStrategy = 'blocked_with_testability_gap' | 'reuse_verified_existing' | 'create_disposable' | 'approved_fixture_setup';
type SavedServiceConfig = {
  name?: string;
  base_url?: string;
  enabled?: boolean;
  auth?: Record<string, unknown>;
  db?: Record<string, unknown>;
};

function asText(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function asRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function asNumber(value: unknown): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function harEntries(value: unknown): Array<Record<string, unknown>> {
  const entries = asRecord(value).entries;
  return Array.isArray(entries) ? entries.map(asRecord) : [];
}

function harRequestLabel(entry: Record<string, unknown>): string {
  const request = asRecord(entry.request);
  const method = asText(request.method) || 'GET';
  const url = asText(request.url) || asText(request.path);
  return `${method} ${url || '/'}`;
}

function harStatusLabel(entry: Record<string, unknown>): string {
  const response = asRecord(entry.response);
  const status = asNumber(response.status) || asNumber(response.status_code);
  return status ? String(status) : 'unknown';
}

function manifestFromAsset(asset: SourceAssetSummary): SourceManifest {
  return {
    source_id: asset.source_id,
    source_hash: asset.latest_source_hash,
    source_version_id: asset.latest_version_id,
    source_origin: 'registered_source_registry',
    source_type: asset.source_type,
  };
}

function releaseTone(result: EnterpriseScanResult | null): string {
  const verdict = result?.release_gate?.verdict;
  if (verdict === 'pass') return 'success';
  if (verdict === 'fail') return 'danger';
  return 'warning';
}

function approvalExpiryDefault(): string {
  return new Date(Date.now() + 60 * 60 * 1000).toISOString().replace(/\.\d{3}Z$/, 'Z');
}

function preflightTone(status: string): string {
  if (status === 'ready') return 'success';
  if (status === 'blocked' || status === 'failed' || status === 'missing') return 'danger';
  return 'warning';
}

function hasConfiguredAuth(service: SavedServiceConfig): boolean {
  const auth = asRecord(service.auth);
  if (asText(auth.bearer_token) || asText(auth.api_key)) return true;
  return Object.entries(auth).some(([key, value]) => {
    if (['type', 'auth_type', 'login_api', 'bearer_token', 'api_key'].includes(key)) return false;
    const role = asRecord(value);
    return Boolean(asText(role.username) || asText(role.password));
  });
}

function hasConfiguredDb(service: SavedServiceConfig): boolean {
  const db = asRecord(service.db);
  return Boolean(asText(db.host) && asText(db.name));
}

function serviceDisplayName(service: SavedServiceConfig): string {
  return asText(service.name) || asText(service.base_url) || '未命名服务';
}

export function EnterpriseCampaigns() {
  usePageTitle('运行中心');
  const [params] = useSearchParams();
  const toast = useToast();
  const { navigateToProjectPath } = useProjectNavigation();
  const project = params.get('project')?.trim() || '';
  const [assets, setAssets] = useState<SourceAssetSummary[]>([]);
  const [loadingAssets, setLoadingAssets] = useState(false);
  const [assetError, setAssetError] = useState('');
  const [services, setServices] = useState<SavedServiceConfig[]>([]);
  const [loadingServices, setLoadingServices] = useState(false);
  const [serviceError, setServiceError] = useState('');
  const [selectedSourceId, setSelectedSourceId] = useState('');
  const [scopeId, setScopeId] = useState('');
  const [environmentRef, setEnvironmentRef] = useState('');
  const [targetBaseUrl, setTargetBaseUrl] = useState('');
  const [executionApprovalId, setExecutionApprovalId] = useState('');
  const [approvalExpiresAt, setApprovalExpiresAt] = useState(approvalExpiryDefault);
  const [strategy, setStrategy] = useState<DataStrategy>('blocked_with_testability_gap');
  const [provenanceReceipt, setProvenanceReceipt] = useState('');
  const [creationReceipt, setCreationReceipt] = useState('');
  const [cleanupReceipt, setCleanupReceipt] = useState('');
  const [disposableScope, setDisposableScope] = useState('');
  const [fixtureRef, setFixtureRef] = useState('');
  const [fixtureReceipt, setFixtureReceipt] = useState('');
  const [running, setRunning] = useState<RunMode | ApprovalAction | null>(null);
  const [standardRunning, setStandardRunning] = useState(false);
  const [standardResult, setStandardResult] = useState<V12ScanResult | null>(null);
  const [result, setResult] = useState<EnterpriseScanResult | null>(null);
  const [error, setError] = useState('');

  const apiAssets = useMemo(
    () => assets.filter((asset) => API_SOURCE_TYPES.has(asset.source_type.toLowerCase())),
    [assets],
  );
  const selectedAsset = useMemo(
    () => apiAssets.find((asset) => asset.source_id === selectedSourceId) || null,
    [apiAssets, selectedSourceId],
  );

  const refreshAssets = useCallback(async () => {
    if (!project) {
      setAssets([]);
      setSelectedSourceId('');
      setAssetError('');
      return;
    }
    setLoadingAssets(true);
    try {
      const next = await listEnterpriseSourceAssets(project);
      setAssets(next);
      setAssetError('');
      const apiSources = next.filter((asset) => API_SOURCE_TYPES.has(asset.source_type.toLowerCase()));
      setSelectedSourceId((previous) => apiSources.some((asset) => asset.source_id === previous) ? previous : (apiSources.length === 1 ? apiSources[0].source_id : ''));
    } catch (caught: unknown) {
      const message = caught instanceof Error ? caught.message : '来源资产加载失败';
      setAssets([]);
      setAssetError(message);
    } finally {
      setLoadingAssets(false);
    }
  }, [project]);

  const refreshServices = useCallback(async () => {
    if (!project) {
      setServices([]);
      setServiceError('');
      return;
    }
    setLoadingServices(true);
    try {
      const payload = asRecord(await getServiceCredentials(project));
      const next = Array.isArray(payload.services) ? payload.services.map((item) => asRecord(item) as SavedServiceConfig) : [];
      setServices(next);
      setServiceError('');
    } catch (caught: unknown) {
      setServices([]);
      setServiceError(caught instanceof Error ? caught.message : '服务配置读取失败');
    } finally {
      setLoadingServices(false);
    }
  }, [project]);

  useEffect(() => { void refreshAssets(); }, [refreshAssets]);
  useEffect(() => { void refreshServices(); }, [refreshServices]);

  const dataContract = useMemo(() => {
    if (strategy === 'reuse_verified_existing') {
      return { strategy, provenance_ref: provenanceReceipt };
    }
    if (strategy === 'create_disposable') {
      return {
        strategy,
        write_approved: true,
        disposable_scope_ref: disposableScope,
        creation_receipt_ref: creationReceipt,
        cleanup_receipt_ref: cleanupReceipt,
      };
    }
    if (strategy === 'approved_fixture_setup') {
      return {
        strategy,
        write_approved: true,
        approved_fixture_ref: fixtureRef,
        fixture_receipt_ref: fixtureReceipt,
        creation_receipt_ref: creationReceipt,
        cleanup_receipt_ref: cleanupReceipt,
      };
    }
    return { strategy };
  }, [cleanupReceipt, creationReceipt, disposableScope, fixtureReceipt, fixtureRef, provenanceReceipt, strategy]);

  const enabledServices = useMemo(
    () => services.filter((service) => service.enabled !== false && asText(service.base_url)),
    [services],
  );
  const resolvedTargetBaseUrl = targetBaseUrl.trim() || asText(enabledServices[0]?.base_url);
  const configuredAuthCount = useMemo(
    () => services.filter((service) => hasConfiguredAuth(service)).length,
    [services],
  );
  const configuredDbCount = useMemo(
    () => services.filter((service) => hasConfiguredDb(service)).length,
    [services],
  );
  const readinessCards = [
    {
      label: '项目上下文',
      value: project ? '已选择' : '待选择',
      note: project || '请先选择客户项目',
      tone: project ? 'success' : 'warning',
    },
    {
      label: '服务目标',
      value: enabledServices.length > 0 ? `${enabledServices.length} 个已配置` : '待配置',
      note: enabledServices[0] ? `默认目标：${serviceDisplayName(enabledServices[0])}` : '请先到项目设置维护服务 URL',
      tone: enabledServices.length > 0 ? 'success' : 'warning',
    },
    {
      label: '鉴权账号',
      value: configuredAuthCount > 0 ? `${configuredAuthCount} 组已配置` : '待配置',
      note: configuredAuthCount > 0 ? '运行时会优先复用已保存鉴权信息' : '请先补齐账号、Token 或 API Key',
      tone: configuredAuthCount > 0 ? 'success' : 'warning',
    },
    {
      label: '数据库校验',
      value: configuredDbCount > 0 ? `${configuredDbCount} 组已配置` : '可选',
      note: configuredDbCount > 0 ? '可用于 DB 侧一致性验证' : '未配置数据库时仅执行接口/页面侧验证',
      tone: configuredDbCount > 0 ? 'success' : 'neutral',
    },
    {
      label: '来源资产',
      value: apiAssets.length > 0 ? `${apiAssets.length} 份可执行` : '可选',
      note: apiAssets.length > 0 ? '已可绑定 OpenAPI/来源哈希' : '无来源资产时仍可先执行标准扫描',
      tone: apiAssets.length > 0 ? 'success' : 'neutral',
    },
    {
      label: '统一入口',
      value: '运行中心',
      note: '标准扫描与受控 Campaign 都从这里启动',
      tone: 'primary',
    },
  ];

  useEffect(() => {
    if (!targetBaseUrl.trim() && enabledServices.length === 1) {
      setTargetBaseUrl(asText(enabledServices[0].base_url));
    }
  }, [enabledServices, targetBaseUrl]);

  const runStandardScan = useCallback(async () => {
    if (!project) {
      setError('请先选择客户项目。');
      return;
    }
    setStandardRunning(true);
    setStandardResult(null);
    setError('');
    try {
      const response = await runV12Scan(project, {
        base_url: resolvedTargetBaseUrl || undefined,
        scope_id: scopeId.trim() || undefined,
        environment_ref: environmentRef.trim() || undefined,
        source_id: selectedAsset?.source_id,
        source_hash: selectedAsset?.latest_source_hash,
      });
      setStandardResult(response);
      if (response.ok) {
        emitScanCompleted(project);
        toast.show(
          `标准扫描已完成：发现 ${response.total_findings || 0} 条问题，执行状态 ${response.execution_status || 'unknown'}`,
          response.total_findings ? 'warning' : 'success',
        );
      } else {
        toast.show(response.message || response.error || '标准扫描未成功执行', 'danger');
      }
    } catch (caught: unknown) {
      const message = caught instanceof Error ? caught.message : '标准扫描执行失败';
      setStandardResult({ ok: false, error: message });
      setError(message);
      toast.show(message, 'danger');
    } finally {
      setStandardRunning(false);
    }
  }, [environmentRef, project, resolvedTargetBaseUrl, scopeId, selectedAsset, toast]);

  const run = useCallback(async (mode: RunMode) => {
    if (!project) {
      setError('请先选择客户项目。');
      return;
    }
    if (!selectedAsset) {
      setError('请选择已登记的 API 来源资产。');
      return;
    }
    if (!scopeId.trim() || !environmentRef.trim()) {
      setError('范围 ID 和环境引用是 Campaign 的必填项。');
      return;
    }
    if (mode === 'execute' && (!resolvedTargetBaseUrl || !executionApprovalId.trim())) {
      setError('执行需要目标地址和由批准方签发的执行批准 ID。');
      return;
    }
    setRunning(mode);
    setError('');
    try {
      const response = await runEnterpriseScan({
        project_id: project,
        scope_id: scopeId.trim(),
        environment_ref: environmentRef.trim(),
        source_manifest: manifestFromAsset(selectedAsset),
        base_url: mode === 'execute' ? resolvedTargetBaseUrl : undefined,
        execution_approval_id: mode === 'execute' ? executionApprovalId.trim() : undefined,
        execution_mode: 'safe_read_only',
        test_data_contract: dataContract,
      });
      setResult(response);
      if (mode === 'execute') emitScanCompleted(project);
      const verdict = response.release_gate?.verdict || 'not_ready';
      toast.show(mode === 'execute' ? `受控运行完成：发布结论 ${verdict}` : `受控运行计划已生成：发布结论 ${verdict}`, verdict === 'fail' ? 'danger' : verdict === 'pass' ? 'success' : 'warning');
    } catch (caught: unknown) {
      const message = caught instanceof Error ? caught.message : 'Campaign 执行失败';
      setError(message);
      toast.show(message, 'danger');
    } finally {
      setRunning(null);
    }
  }, [dataContract, environmentRef, executionApprovalId, project, resolvedTargetBaseUrl, scopeId, selectedAsset, toast]);

  const issueApproval = useCallback(async () => {
    if (!project) {
      setError('请先选择客户项目。');
      return;
    }
    if (!selectedAsset) {
      setError('请选择已登记的 API 来源资产。');
      return;
    }
    const campaignId = asText(result?.campaign?.campaign_id);
    if (!campaignId) {
      setError('请先生成 Campaign 计划，再基于该 Campaign 签发只读执行审批。');
      return;
    }
    if (!scopeId.trim() || !environmentRef.trim() || !resolvedTargetBaseUrl) {
      setError('签发执行审批需要范围 ID、环境引用和目标地址。');
      return;
    }
    setRunning('issue_approval');
    setError('');
    try {
      const approval = await issueEnterpriseExecutionApproval({
        project_id: project,
        campaign_id: campaignId,
        scope_id: scopeId.trim(),
        environment_ref: environmentRef.trim(),
        source_hash: selectedAsset.latest_source_hash,
        target_base_url: resolvedTargetBaseUrl,
        execution_mode: 'safe_read_only',
        expires_at_utc: approvalExpiresAt.trim() || approvalExpiryDefault(),
      });
      setExecutionApprovalId(approval.approval_id);
      toast.show('只读执行审批已签发。', 'success');
    } catch (caught: unknown) {
      const message = caught instanceof Error ? caught.message : '执行审批签发失败';
      setError(message);
      toast.show(message, 'danger');
    } finally {
      setRunning(null);
    }
  }, [approvalExpiresAt, environmentRef, project, resolvedTargetBaseUrl, result, scopeId, selectedAsset, toast]);

  if (!project) {
    return <section className="state-panel"><div className="state-panel-badge">客户选择</div><h2>请先选择客户项目</h2><p>运行中心必须绑定真实客户上下文，才能把项目配置、资料、执行和结果回显串成同一条闭环。</p></section>;
  }

  return (
    <div>
      <div className="page-header">
        <div>
          <span className="panel-kicker">正式运行入口</span>
          <h1>运行中心</h1>
          <p>这里是项目配置、资料导入之后的唯一执行入口。先跑标准扫描建立真实结果，再按需要进入受控 Campaign 做补证或只读执行。</p>
        </div>
        <div className="settings-actions">
          <button type="button" className="btn btn-secondary" onClick={() => navigateToProjectPath('/settings', project)}>前往项目设置</button>
          <button type="button" className="btn btn-secondary" onClick={() => navigateToProjectPath('/materials', project)}>前往企业资料</button>
        </div>
      </div>

      <div className="customer-summary-grid mb-4">
        {readinessCards.map((item) => (
          <article key={item.label} className={`customer-summary-card tone-${item.tone}`}>
            <span>{item.label}</span>
            <strong>{item.value}</strong>
            <small>{item.note}</small>
          </article>
        ))}
      </div>

      <section className="card mb-4">
        <h2>1. 标准扫描</h2>
        <p className="muted">用于最小可商用闭环首轮验证。系统会优先复用当前项目的服务配置、企业资料和来源资产，并把结果统一回收到风险总览、行为验证和证据链页面。</p>
        <div className="settings-grid">
          <label className="form-field">
            <span>目标地址</span>
            <input value={targetBaseUrl} onChange={(event) => setTargetBaseUrl(event.target.value)} placeholder={enabledServices[0] ? asText(enabledServices[0].base_url) : 'https://已配置测试环境'} />
          </label>
          <label className="form-field">
            <span>范围 ID（可选）</span>
            <input value={scopeId} onChange={(event) => setScopeId(event.target.value)} placeholder="例如：结算流程、订单核心链路" />
          </label>
          <label className="form-field">
            <span>环境引用（可选）</span>
            <input value={environmentRef} onChange={(event) => setEnvironmentRef(event.target.value)} placeholder="例如：sit / uat / staging" />
          </label>
        </div>
        <div className="settings-grid">
          <div>
            <span className="muted">默认服务目标</span>
            <p>{enabledServices[0] ? `${serviceDisplayName(enabledServices[0])} · ${asText(enabledServices[0].base_url)}` : '当前没有已启用服务，后端将尝试从项目上下文自动推断。'}</p>
          </div>
          <div>
            <span className="muted">已配置鉴权</span>
            <p>{configuredAuthCount > 0 ? `${configuredAuthCount} 组可复用` : '当前未发现已配置鉴权账号或密钥'}</p>
          </div>
          <div>
            <span className="muted">来源资产绑定</span>
            <p>{selectedAsset ? `${selectedAsset.source_id} · ${selectedAsset.latest_source_hash.slice(0, 12)}…` : '可选；未绑定时仍允许先执行标准扫描'}</p>
          </div>
          <div>
            <span className="muted">执行结果</span>
            <p>{standardResult?.ok ? `执行状态 ${standardResult.execution_status || 'unknown'}` : '等待启动'}</p>
          </div>
        </div>
        <div className="settings-actions">
          <button type="button" className="btn btn-primary" onClick={() => void runStandardScan()} disabled={standardRunning}>
            {standardRunning ? '标准扫描执行中' : '执行标准扫描'}
          </button>
          <button type="button" className="btn btn-secondary" onClick={() => navigateToProjectPath('/dashboard', project)}>
            查看风险总览
          </button>
        </div>
        <p className="muted">标准扫描执行成功后，结果会自动刷新到总览、缺陷和证据链页面；不会另起一套展示口径。</p>
      </section>

      {standardResult && (
        <section className={`card mb-4 status-card status-${standardResult.ok ? (standardResult.total_findings ? 'warning' : 'success') : 'danger'}`}>
          <h2>标准扫描结果</h2>
          <div className="settings-grid">
            <div><span className="muted">扫描 ID</span><p>{standardResult.scan_id || '未生成'}</p></div>
            <div><span className="muted">执行状态</span><p>{standardResult.execution_status || 'unknown'}</p></div>
            <div><span className="muted">发现问题</span><p>{standardResult.total_findings ?? 0}</p></div>
            <div><span className="muted">耗时</span><p>{standardResult.total_ms ?? 0} ms</p></div>
          </div>
          <div className="settings-actions">
            <button type="button" className="btn btn-primary" onClick={() => navigateToProjectPath('/findings', project)}>查看客户缺陷</button>
            <button type="button" className="btn btn-secondary" onClick={() => navigateToProjectPath('/evidence', project)}>查看证据链</button>
            <button type="button" className="btn btn-secondary" onClick={() => navigateToProjectPath('/dashboard', project)}>查看风险总览</button>
          </div>
        </section>
      )}

      <section className="card mb-4">
        <div className="settings-actions">
          <h2>2. 受控 Campaign</h2>
          <button type="button" className="btn btn-secondary" onClick={() => void refreshAssets()} disabled={loadingAssets || loadingServices}>
            {loadingAssets || loadingServices ? '刷新中' : '刷新来源与配置'}
          </button>
        </div>
        <p className="muted">用于已登记来源资产、已审批执行和补证场景。只有在环境、来源哈希、审批和数据合同都满足时，系统才会发送真实只读流量。</p>
        <div className="settings-grid">
          <div><span className="muted">来源资产服务</span><p>{assetError ? `未连通：${assetError}` : loadingAssets ? '加载中...' : `${apiAssets.length} 份可执行来源`}</p></div>
          <div><span className="muted">项目服务配置</span><p>{serviceError ? `未连通：${serviceError}` : loadingServices ? '加载中...' : `${enabledServices.length} 个已启用服务`}</p></div>
          <div><span className="muted">默认目标地址</span><p>{resolvedTargetBaseUrl || '未配置，执行时需手工填写'}</p></div>
          <div><span className="muted">测试数据合同</span><p>{strategy}</p></div>
        </div>
      </section>

      <section className="card mb-4">
        <h2>2.1 选择不可变来源与 Campaign 边界</h2>
        <div className="settings-grid">
          <label className="form-field"><span>API 来源资产</span><select value={selectedSourceId} onChange={(event) => setSelectedSourceId(event.target.value)} disabled={loadingAssets}><option value="">{apiAssets.length ? '请选择已登记 API 来源' : '暂无已登记 API 来源'}</option>{apiAssets.map((asset) => <option key={asset.source_id} value={asset.source_id}>{asset.source_id} · v{asset.version_count}</option>)}</select></label>
          <label className="form-field"><span>Campaign 范围 ID</span><input value={scopeId} onChange={(event) => setScopeId(event.target.value)} placeholder="例如：结算流程或核心服务范围" /></label>
          <label className="form-field"><span>环境引用</span><input value={environmentRef} onChange={(event) => setEnvironmentRef(event.target.value)} placeholder="例如：批准的测试环境标识" /></label>
        </div>
        {selectedAsset ? <p className="muted">来源版本：{selectedAsset.latest_version_id}；哈希：{selectedAsset.latest_source_hash.slice(0, 16)}…</p> : <p className="muted">API 来源必须先在“企业资料”或“来源资产”中沉淀为可追溯资产。系统不会从临时文本猜测来源身份。</p>}
      </section>

      <section className="card mb-4">
        <h2>2.2 测试数据合同</h2>
        <p className="muted">回执 ID 必须由受信任的数据适配器在真实创建、清理或核验后签发；此页面不会生成伪造回执。</p>
        <div className="settings-grid">
          <label className="form-field"><span>策略</span><select value={strategy} onChange={(event) => setStrategy(event.target.value as DataStrategy)}><option value="blocked_with_testability_gap">尚未批准 / 仅计划</option><option value="reuse_verified_existing">复用已核验数据</option><option value="create_disposable">隔离一次性数据</option><option value="approved_fixture_setup">已批准 Fixture</option></select></label>
          {strategy === 'reuse_verified_existing' && <label className="form-field"><span>数据来源回执 ID</span><input value={provenanceReceipt} onChange={(event) => setProvenanceReceipt(event.target.value)} placeholder="tdr_..." /></label>}
          {strategy === 'create_disposable' && <><label className="form-field"><span>隔离数据范围</span><input value={disposableScope} onChange={(event) => setDisposableScope(event.target.value)} placeholder="隔离测试范围引用" /></label><label className="form-field"><span>创建回执 ID</span><input value={creationReceipt} onChange={(event) => setCreationReceipt(event.target.value)} placeholder="tdr_..." /></label><label className="form-field"><span>清理回执 ID</span><input value={cleanupReceipt} onChange={(event) => setCleanupReceipt(event.target.value)} placeholder="tdr_..." /></label></>}
          {strategy === 'approved_fixture_setup' && <><label className="form-field"><span>Fixture 引用</span><input value={fixtureRef} onChange={(event) => setFixtureRef(event.target.value)} placeholder="已批准 Fixture 引用" /></label><label className="form-field"><span>Fixture 回执 ID</span><input value={fixtureReceipt} onChange={(event) => setFixtureReceipt(event.target.value)} placeholder="tdr_..." /></label><label className="form-field"><span>创建回执 ID</span><input value={creationReceipt} onChange={(event) => setCreationReceipt(event.target.value)} placeholder="tdr_..." /></label><label className="form-field"><span>清理回执 ID</span><input value={cleanupReceipt} onChange={(event) => setCleanupReceipt(event.target.value)} placeholder="tdr_..." /></label></>}
        </div>
      </section>

      <section className="card mb-4">
        <h2>2.3 计划或受控只读执行</h2>
        <div className="settings-grid">
          <label className="form-field"><span>目标地址（仅执行时需要）</span><input value={targetBaseUrl} onChange={(event) => setTargetBaseUrl(event.target.value)} placeholder="https://已批准的测试环境" /></label>
          <label className="form-field"><span>审批过期时间 UTC</span><input value={approvalExpiresAt} onChange={(event) => setApprovalExpiresAt(event.target.value)} placeholder="2026-07-07T10:00:00Z" /></label>
          <label className="form-field"><span>执行批准 ID（仅执行时需要）</span><input value={executionApprovalId} onChange={(event) => setExecutionApprovalId(event.target.value)} placeholder="eap_..." /></label>
        </div>
        <div className="settings-actions">
          <button type="button" className="btn btn-secondary" onClick={() => void run('plan')} disabled={running !== null}>{running === 'plan' ? '生成中' : '仅生成 Campaign 计划'}</button>
          <button type="button" className="btn btn-secondary" onClick={() => void issueApproval()} disabled={running !== null || !asText(result?.campaign?.campaign_id)}>{running === 'issue_approval' ? '签发中' : '签发只读执行审批'}</button>
          <button type="button" className="btn btn-primary" onClick={() => void run('execute')} disabled={running !== null}>{running === 'execute' ? '执行中' : '执行已批准的只读 Campaign'}</button>
        </div>
        <p className="muted">执行会被后端目标白名单、来源哈希、Campaign、数据合同和批准有效期共同约束。没有批准不会产生网络流量。</p>
      </section>

      {error && <section className="state-panel"><div className="state-panel-badge">需要处理</div><h2>运行未启动</h2><p>{error}</p></section>}
      {result && <section className={`card mb-4 status-card status-${releaseTone(result)}`}>
        <h2>受控运行结果</h2>
        <div className="settings-grid"><div><span className="muted">Campaign</span><p>{asText(result.campaign.campaign_id) || '未创建'}</p></div><div><span className="muted">执行状态</span><p>{result.execution_status}</p></div><div><span className="muted">发布结论</span><p>{result.release_gate?.verdict || 'not_ready'}</p></div><div><span className="muted">证据包</span><p>{asText(result.evidence_bundle.bundle_id) || '未生成'}</p></div></div>
        {(result.release_gate?.reasons || []).length > 0 && <ul>{result.release_gate.reasons?.map((reason) => <li key={`${reason.code}-${reason.detail}`}>{reason.code}{reason.detail ? `：${reason.detail}` : ''}</li>)}</ul>}
        {(result.auto_har || result.execution_evidence_summary || result.ui_execution_summary) && <div className="mt-3">
          <h3>真实执行证据</h3>
          <div className="settings-grid">
            <div><span className="muted">HAR 状态</span><p>{asText(asRecord(result.auto_har).status) || 'not_reported'}</p></div>
            <div><span className="muted">API 请求</span><p>{harEntries(result.auto_har).length}</p></div>
            <div><span className="muted">UI 执行</span><p>{asText(asRecord(result.execution_evidence_summary || result.ui_execution_summary).status) || 'not_reported'}</p></div>
            <div><span className="muted">UI 证据</span><p>{asNumber(asRecord(result.execution_evidence_summary || result.ui_execution_summary).evidence_captured_count)}</p></div>
          </div>
          {harEntries(result.auto_har).length > 0 && <ul>{harEntries(result.auto_har).slice(0, 5).map((entry, index) => <li key={`${harRequestLabel(entry)}-${index}`}>{harRequestLabel(entry)}：HTTP {harStatusLabel(entry)}</li>)}</ul>}
        </div>}
        {result.scan_preflight_guide && <div className="mt-3">
          <div className="settings-grid">
            <div><span className="muted">前置向导状态</span><p><span className={`status status-${preflightTone(result.scan_preflight_guide.status || '')}`}>{result.scan_preflight_guide.status || 'unknown'}</span></p></div>
            <div><span className="muted">运行合同</span><p>{result.scan_preflight_guide.runtime_contract_status || 'unknown'}</p></div>
            <div><span className="muted">可声明健康</span><p>{result.scan_preflight_guide.healthy_claim_allowed ? '允许' : '不允许'}</p></div>
          </div>
          {(result.scan_preflight_guide.missing || []).length > 0 && <ul>{result.scan_preflight_guide.missing?.map((item) => <li key={item}>{item}</li>)}</ul>}
          {(result.scan_preflight_guide.checks || []).length > 0 && <ul>{result.scan_preflight_guide.checks?.map((check) => <li key={check.key || check.label || check.detail}>{check.label || check.key || 'check'}：{check.status || 'unknown'}{check.detail ? `；${check.detail}` : ''}</li>)}</ul>}
        </div>}
        <div className="settings-actions">
          <button type="button" className="btn btn-primary" onClick={() => navigateToProjectPath('/dashboard', project)}>查看风险总览</button>
          <button type="button" className="btn btn-secondary" onClick={() => navigateToProjectPath('/findings', project)}>查看客户缺陷</button>
          <button type="button" className="btn btn-secondary" onClick={() => navigateToProjectPath('/evidence', project)}>查看证据链</button>
        </div>
      </section>}
    </div>
  );
}

export default EnterpriseCampaigns;
