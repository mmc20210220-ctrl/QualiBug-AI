import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  listEnterpriseSourceAssets,
  runEnterpriseScan,
  type EnterpriseScanResult,
  type SourceAssetSummary,
  type SourceManifest,
} from '../api/enterprise';
import { useToast } from '../components/useToast';
import { usePageTitle } from '../lib/page-title';

const API_SOURCE_TYPES = new Set(['openapi', 'api_document', 'api_spec']);

type RunMode = 'plan' | 'execute';
type DataStrategy = 'blocked_with_testability_gap' | 'reuse_verified_existing' | 'create_disposable' | 'approved_fixture_setup';

function asText(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
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

export function EnterpriseCampaigns() {
  usePageTitle('受控 Campaign');
  const [params] = useSearchParams();
  const toast = useToast();
  const project = params.get('project')?.trim() || '';
  const [assets, setAssets] = useState<SourceAssetSummary[]>([]);
  const [loadingAssets, setLoadingAssets] = useState(false);
  const [selectedSourceId, setSelectedSourceId] = useState('');
  const [scopeId, setScopeId] = useState('');
  const [environmentRef, setEnvironmentRef] = useState('');
  const [targetBaseUrl, setTargetBaseUrl] = useState('');
  const [executionApprovalId, setExecutionApprovalId] = useState('');
  const [strategy, setStrategy] = useState<DataStrategy>('blocked_with_testability_gap');
  const [provenanceReceipt, setProvenanceReceipt] = useState('');
  const [creationReceipt, setCreationReceipt] = useState('');
  const [cleanupReceipt, setCleanupReceipt] = useState('');
  const [disposableScope, setDisposableScope] = useState('');
  const [fixtureRef, setFixtureRef] = useState('');
  const [fixtureReceipt, setFixtureReceipt] = useState('');
  const [running, setRunning] = useState<RunMode | null>(null);
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
      return;
    }
    setLoadingAssets(true);
    try {
      const next = await listEnterpriseSourceAssets(project);
      setAssets(next);
      const apiSources = next.filter((asset) => API_SOURCE_TYPES.has(asset.source_type.toLowerCase()));
      setSelectedSourceId((previous) => apiSources.some((asset) => asset.source_id === previous) ? previous : (apiSources.length === 1 ? apiSources[0].source_id : ''));
    } catch (caught: unknown) {
      const message = caught instanceof Error ? caught.message : '来源资产加载失败';
      setError(message);
    } finally {
      setLoadingAssets(false);
    }
  }, [project]);

  useEffect(() => { void refreshAssets(); }, [refreshAssets]);

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
    if (mode === 'execute' && (!targetBaseUrl.trim() || !executionApprovalId.trim())) {
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
        base_url: mode === 'execute' ? targetBaseUrl.trim() : undefined,
        execution_approval_id: mode === 'execute' ? executionApprovalId.trim() : undefined,
        execution_mode: 'safe_read_only',
        test_data_contract: dataContract,
      });
      setResult(response);
      const verdict = response.release_gate?.verdict || 'not_ready';
      toast.show(mode === 'execute' ? `受控扫描完成：发布结论 ${verdict}` : `Campaign 计划已生成：发布结论 ${verdict}`, verdict === 'fail' ? 'danger' : verdict === 'pass' ? 'success' : 'warning');
    } catch (caught: unknown) {
      const message = caught instanceof Error ? caught.message : 'Campaign 执行失败';
      setError(message);
      toast.show(message, 'danger');
    } finally {
      setRunning(null);
    }
  }, [dataContract, environmentRef, executionApprovalId, project, scopeId, selectedAsset, targetBaseUrl, toast]);

  if (!project) {
    return <section className="state-panel"><div className="state-panel-badge">客户选择</div><h2>请先选择客户项目</h2><p>Campaign 必须绑定项目资料、范围、环境和执行批准，不能脱离客户上下文创建。</p></section>;
  }

  return (
    <div>
      <div className="page-header">
        <div><h1>受控 Campaign</h1><p>从已登记来源生成行为计划；只有获得环境批准后才允许发送只读流量。</p></div>
        <button type="button" className="btn btn-secondary" onClick={() => void refreshAssets()} disabled={loadingAssets}>{loadingAssets ? '刷新中' : '刷新来源资产'}</button>
      </div>

      <section className="card mb-4">
        <h2>1. 选择不可变来源与 Campaign 边界</h2>
        <div className="settings-grid">
          <label className="form-field"><span>API 来源资产</span><select value={selectedSourceId} onChange={(event) => setSelectedSourceId(event.target.value)} disabled={loadingAssets}><option value="">{apiAssets.length ? '请选择已登记 API 来源' : '暂无已登记 API 来源'}</option>{apiAssets.map((asset) => <option key={asset.source_id} value={asset.source_id}>{asset.source_id} · v{asset.version_count}</option>)}</select></label>
          <label className="form-field"><span>Campaign 范围 ID</span><input value={scopeId} onChange={(event) => setScopeId(event.target.value)} placeholder="例如：结算流程或核心服务范围" /></label>
          <label className="form-field"><span>环境引用</span><input value={environmentRef} onChange={(event) => setEnvironmentRef(event.target.value)} placeholder="例如：批准的测试环境标识" /></label>
        </div>
        {selectedAsset ? <p className="muted">来源版本：{selectedAsset.latest_version_id}；哈希：{selectedAsset.latest_source_hash.slice(0, 16)}…</p> : <p className="muted">API 来源必须先在“企业资料”或正式来源 API 中登记。系统不会从临时文本猜测来源身份。</p>}
      </section>

      <section className="card mb-4">
        <h2>2. 测试数据合同</h2>
        <p className="muted">回执 ID 必须由受信任的数据适配器在真实创建、清理或核验后签发；此页面不会生成伪造回执。</p>
        <div className="settings-grid">
          <label className="form-field"><span>策略</span><select value={strategy} onChange={(event) => setStrategy(event.target.value as DataStrategy)}><option value="blocked_with_testability_gap">尚未批准 / 仅计划</option><option value="reuse_verified_existing">复用已核验数据</option><option value="create_disposable">隔离一次性数据</option><option value="approved_fixture_setup">已批准 Fixture</option></select></label>
          {strategy === 'reuse_verified_existing' && <label className="form-field"><span>数据来源回执 ID</span><input value={provenanceReceipt} onChange={(event) => setProvenanceReceipt(event.target.value)} placeholder="tdr_..." /></label>}
          {strategy === 'create_disposable' && <><label className="form-field"><span>隔离数据范围</span><input value={disposableScope} onChange={(event) => setDisposableScope(event.target.value)} placeholder="隔离测试范围引用" /></label><label className="form-field"><span>创建回执 ID</span><input value={creationReceipt} onChange={(event) => setCreationReceipt(event.target.value)} placeholder="tdr_..." /></label><label className="form-field"><span>清理回执 ID</span><input value={cleanupReceipt} onChange={(event) => setCleanupReceipt(event.target.value)} placeholder="tdr_..." /></label></>}
          {strategy === 'approved_fixture_setup' && <><label className="form-field"><span>Fixture 引用</span><input value={fixtureRef} onChange={(event) => setFixtureRef(event.target.value)} placeholder="已批准 Fixture 引用" /></label><label className="form-field"><span>Fixture 回执 ID</span><input value={fixtureReceipt} onChange={(event) => setFixtureReceipt(event.target.value)} placeholder="tdr_..." /></label><label className="form-field"><span>创建回执 ID</span><input value={creationReceipt} onChange={(event) => setCreationReceipt(event.target.value)} placeholder="tdr_..." /></label><label className="form-field"><span>清理回执 ID</span><input value={cleanupReceipt} onChange={(event) => setCleanupReceipt(event.target.value)} placeholder="tdr_..." /></label></>}
        </div>
      </section>

      <section className="card mb-4">
        <h2>3. 计划或受控只读执行</h2>
        <div className="settings-grid">
          <label className="form-field"><span>目标地址（仅执行时需要）</span><input value={targetBaseUrl} onChange={(event) => setTargetBaseUrl(event.target.value)} placeholder="https://已批准的测试环境" /></label>
          <label className="form-field"><span>执行批准 ID（仅执行时需要）</span><input value={executionApprovalId} onChange={(event) => setExecutionApprovalId(event.target.value)} placeholder="eap_..." /></label>
        </div>
        <div className="settings-actions">
          <button type="button" className="btn btn-secondary" onClick={() => void run('plan')} disabled={running !== null}>{running === 'plan' ? '生成中' : '仅生成 Campaign 计划'}</button>
          <button type="button" className="btn btn-primary" onClick={() => void run('execute')} disabled={running !== null}>{running === 'execute' ? '执行中' : '执行已批准的只读 Campaign'}</button>
        </div>
        <p className="muted">执行会被后端目标白名单、来源哈希、Campaign、数据合同和批准有效期共同约束。没有批准不会产生网络流量。</p>
      </section>

      {error && <section className="state-panel"><div className="state-panel-badge">需要处理</div><h2>Campaign 未启动</h2><p>{error}</p></section>}
      {result && <section className={`card mb-4 status-card status-${releaseTone(result)}`}>
        <h2>本轮 Campaign 结果</h2>
        <div className="settings-grid"><div><span className="muted">Campaign</span><p>{asText(result.campaign.campaign_id) || '未创建'}</p></div><div><span className="muted">执行状态</span><p>{result.execution_status}</p></div><div><span className="muted">发布结论</span><p>{result.release_gate?.verdict || 'not_ready'}</p></div><div><span className="muted">证据包</span><p>{asText(result.evidence_bundle.bundle_id) || '未生成'}</p></div></div>
        {(result.release_gate?.reasons || []).length > 0 && <ul>{result.release_gate.reasons?.map((reason) => <li key={`${reason.code}-${reason.detail}`}>{reason.code}{reason.detail ? `：${reason.detail}` : ''}</li>)}</ul>}
      </section>}
    </div>
  );
}

export default EnterpriseCampaigns;
