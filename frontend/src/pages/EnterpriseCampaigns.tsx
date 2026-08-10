import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { emitScanCompleted } from '../api/data';
import {
  getKnowledgeAsset,
  getScanPreflight,
  getServiceCredentials,
  type ScanPreflight,
  type V12ScanResult,
} from '../api/client';
import {
  listUploadFixtures,
  type UploadFixtureRecord,
} from '../api/upload-fixtures';
import { runV12ScanFromRunCenter } from '../api/run-center';
import { RunPreflightDecisionSnapshot } from '../components/run/RunPreflightDecisionSnapshot';
import {
  RunUploadFixtureSelector,
  type UploadScenarioRunState,
} from '../components/run/RunUploadFixtureSelector';
import { useToast } from '../components/useToast';
import { deriveRunPreflightPresentation } from '../lib/run-preflight-presentation';
import { usePageTitle } from '../lib/page-title';
import { useProjectNavigation } from '../lib/project-navigation';
import { asArray, asNum, asRecord, asText } from '../lib/value-guards';

type JsonRecord = Record<string, unknown>;
type SavedServiceConfig = { name?: string; base_url?: string; enabled?: boolean; auth?: JsonRecord; db?: JsonRecord };
type SourceSummary = { source_id: string; filename: string; source_type: string; status: string };

function textArray(value: unknown): string[] { return asArray(value).map(asText).filter(Boolean); }

function sameIdentitySet(left: string[], right: string[]): boolean {
  const normalizedLeft = [...new Set(left)].sort();
  const normalizedRight = [...new Set(right)].sort();
  return normalizedLeft.length === normalizedRight.length
    && normalizedLeft.every((value, index) => value === normalizedRight[index]);
}

function harEntries(value: unknown): JsonRecord[] {
  const entries = asRecord(value).entries;
  return (Array.isArray(entries) ? entries : []).filter((entry): entry is JsonRecord => Boolean(entry) && typeof entry === 'object');
}

function harRequestLabel(entry: JsonRecord): string {
  const request = asRecord(entry.request);
  const method = asText(request.method) || asText(entry.method) || 'GET';
  const url = asText(request.url) || asText(request.path) || asText(entry.url);
  return `${method} ${url || '/'}`;
}

function harStatusLabel(entry: JsonRecord): string {
  const response = asRecord(entry.response);
  const status = asNum(response.status) || asNum(response.status_code) || asNum(entry.status);
  return status ? String(status) : '—';
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

function resultTone(result: V12ScanResult | null): 'success' | 'warning' | 'danger' | 'neutral' {
  if (!result) return 'neutral';
  if (!result.ok) return 'danger';
  const status = asText(result.execution_status).toLowerCase();
  const campaignStatus = asText(result.campaign?.campaign_status).toLowerCase();
  if (['blocked', 'failed', 'error'].includes(status) || campaignStatus === 'blocked') return 'danger';
  if (['plan_only', 'partial', 'partial_coverage', 'coverage_deferred', 'not_executed'].includes(status) || campaignStatus === 'coverage_deferred') return 'warning';
  const testDataStatus = asText(result.test_data_plan?.status).toLowerCase();
  if (testDataStatus && testDataStatus !== 'ready') return 'warning';
  return (result.total_findings || 0) > 0 ? 'warning' : 'success';
}

function executionStatusLabel(status: string): string {
  const map: Record<string, string> = {
    executed: '已真实执行',
    completed: '已完成真实验证',
    plan_only: '仅生成计划（未执行）',
    partial: '部分执行',
    partial_coverage: '部分覆盖',
    blocked: '已阻断',
    not_executed: '未执行',
    coverage_deferred: '覆盖已递延',
    failed: '执行失败',
  };
  return map[status.toLowerCase()] || status || 'unknown';
}

function activeApprovedFixtures(value: UploadFixtureRecord[]): UploadFixtureRecord[] {
  return value.filter((fixture) => (
    fixture.status === 'active'
    && fixture.authority === 'approved_copy'
    && Boolean(asText(fixture.binding_ref))
  ));
}

export function EnterpriseCampaigns() {
  usePageTitle('运行中心');
  const [params] = useSearchParams();
  const toast = useToast();
  const { navigateToProjectPath } = useProjectNavigation();
  const project = params.get('project')?.trim() || '';

  const [preflight, setPreflight] = useState<ScanPreflight | null>(null);
  const [loadingPreflight, setLoadingPreflight] = useState(false);
  const [preflightError, setPreflightError] = useState('');
  const [services, setServices] = useState<SavedServiceConfig[]>([]);
  const [serviceError, setServiceError] = useState('');
  const [sources, setSources] = useState<SourceSummary[]>([]);
  const [sourceError, setSourceError] = useState('');
  const [approvedFixtures, setApprovedFixtures] = useState<UploadFixtureRecord[]>([]);
  const [selectedFixtureRefs, setSelectedFixtureRefs] = useState<string[]>([]);
  const [fixtureError, setFixtureError] = useState('');
  const [loadingFixtures, setLoadingFixtures] = useState(false);
  const [scenarioState, setScenarioState] = useState<UploadScenarioRunState>({ refs: [], loading: true, error: '' });

  // Exceptional operator overrides. Empty values keep backend project authority.
  const [targetBaseUrl, setTargetBaseUrl] = useState('');
  const [scopeId, setScopeId] = useState('');
  const [environmentRef, setEnvironmentRef] = useState('');
  const [selectedSourceId, setSelectedSourceId] = useState('');
  const [forceReadOnly, setForceReadOnly] = useState(false);

  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<V12ScanResult | null>(null);
  const [error, setError] = useState('');
  const [lastRunScenarioRefs, setLastRunScenarioRefs] = useState<string[]>([]);
  const [lastRunFixtureRefs, setLastRunFixtureRefs] = useState<string[]>([]);

  const refreshContext = useCallback(async () => {
    if (!project) {
      setPreflight(null);
      setPreflightError('');
      setServices([]);
      setSources([]);
      setApprovedFixtures([]);
      setSelectedFixtureRefs([]);
      setScenarioState({ refs: [], loading: false, error: '' });
      return;
    }
    setLoadingPreflight(true);
    setLoadingFixtures(true);
    setPreflightError('');
    setError('');
    const [preflightResult, serviceResult, knowledgeResult, fixtureResult] = await Promise.allSettled([
      getScanPreflight(project),
      getServiceCredentials(project),
      getKnowledgeAsset(project),
      listUploadFixtures(project, false),
    ]);
    if (preflightResult.status === 'fulfilled') {
      setPreflight(preflightResult.value);
      setPreflightError('');
    } else {
      setPreflight(null);
      setPreflightError(preflightResult.reason instanceof Error ? preflightResult.reason.message : '运行前检查失败');
    }
    if (serviceResult.status === 'fulfilled') {
      const payload = asRecord(serviceResult.value);
      setServices(asArray(payload.services).map((item) => asRecord(item) as SavedServiceConfig));
      setServiceError('');
    } else {
      setServices([]);
      setServiceError(serviceResult.reason instanceof Error ? serviceResult.reason.message : '服务配置读取失败');
    }
    if (knowledgeResult.status === 'fulfilled') {
      const asset = asRecord(asRecord(knowledgeResult.value).knowledge_asset);
      setSources(asArray(asset.sources || asset.source_inventory).map((value) => {
        const source = asRecord(value);
        return {
          source_id: asText(source.source_id) || asText(source.id),
          filename: asText(source.filename) || asText(source.original_name) || asText(source.name),
          source_type: asText(source.source_type) || asText(source.type),
          status: asText(source.status) || 'active',
        };
      }).filter((source) => source.source_id && source.status !== 'deleted'));
      setSourceError('');
    } else {
      setSources([]);
      setSourceError(knowledgeResult.reason instanceof Error ? knowledgeResult.reason.message : '资料读取失败');
    }
    if (fixtureResult.status === 'fulfilled') {
      const fixtures = activeApprovedFixtures(fixtureResult.value.fixtures);
      const activeRefs = new Set(fixtures.map((fixture) => asText(fixture.binding_ref)));
      setApprovedFixtures(fixtures);
      setSelectedFixtureRefs((current) => current.filter((bindingRef) => activeRefs.has(bindingRef)));
      setFixtureError('');
    } else {
      setApprovedFixtures([]);
      setSelectedFixtureRefs([]);
      setFixtureError(fixtureResult.reason instanceof Error ? fixtureResult.reason.message : '上传 Fixture 读取失败');
    }
    setLoadingPreflight(false);
    setLoadingFixtures(false);
  }, [project]);

  useEffect(() => { void refreshContext(); }, [refreshContext]);

  const enabledServices = useMemo(
    () => services.filter((service) => service.enabled !== false && asText(service.base_url)),
    [services],
  );
  const activeSources = useMemo(
    () => sources.filter((source) => source.status.trim().toLowerCase() === 'active'),
    [sources],
  );
  const configuredAuthCount = useMemo(() => enabledServices.filter(hasConfiguredAuth).length, [enabledServices]);
  const configuredDbCount = useMemo(() => enabledServices.filter(hasConfiguredDb).length, [enabledServices]);
  const apiSources = useMemo(() => {
    const apiTypes = new Set(['openapi', 'openapi3', 'swagger', 'postman', 'api_spec']);
    return activeSources.filter((source) => apiTypes.has((source.source_type || '').toLowerCase()));
  }, [activeSources]);
  const resolvedTargetBaseUrl = targetBaseUrl.trim() || asText(enabledServices[0]?.base_url);
  const resolvedSourceId = selectedSourceId || apiSources[0]?.source_id || '';
  const blockers = preflight?.reasons || [];
  const preflightReady = Boolean(preflight?.ready);
  const runBlockedByPreflight = !loadingPreflight && !preflightReady;
  const runBlockedByScenario = !forceReadOnly && (scenarioState.loading || Boolean(scenarioState.error));
  const runDisabled = running || loadingPreflight || loadingFixtures || runBlockedByPreflight || runBlockedByScenario;
  const preflightPresentation = deriveRunPreflightPresentation({
    preflight,
    loadingPreflight,
    preflightError,
    enabledServiceCount: enabledServices.length,
    serviceError,
    configuredAuthCount,
    activeSourceCount: activeSources.length,
    totalSourceCount: sources.length,
    sourceError,
    forceReadOnly,
    scenarioLoading: scenarioState.loading,
    scenarioError: scenarioState.error,
    scenarioCount: scenarioState.refs.length,
  });

  const toggleFixture = useCallback((bindingRef: string) => {
    const normalized = bindingRef.trim();
    if (!normalized) return;
    setSelectedFixtureRefs((current) => (
      current.includes(normalized)
        ? current.filter((item) => item !== normalized)
        : [...current, normalized]
    ));
  }, []);

  const handleScenarioStateChange = useCallback((state: UploadScenarioRunState) => {
    setScenarioState(state);
  }, []);

  const runStandardScan = useCallback(async () => {
    if (!project) { setError('请先选择客户项目。'); return; }
    if (!preflightReady) {
      setError(blockers.length > 0 ? `运行前检查仍有 ${blockers.length} 项阻断，请先处理后重新检查。` : '运行前检查尚未通过，请重新检查运行条件。');
      return;
    }
    if (!forceReadOnly && scenarioState.loading) {
      setError('审批上传场景仍在可信同步中，请同步完成后再运行。');
      return;
    }
    if (!forceReadOnly && scenarioState.error) {
      setError(`审批上传场景未完成可信同步：${scenarioState.error}`);
      return;
    }
    const scenarioRefs = forceReadOnly ? [] : [...scenarioState.refs];
    const fixtureRefs = forceReadOnly ? [] : [...selectedFixtureRefs];
    setLastRunScenarioRefs(scenarioRefs);
    setLastRunFixtureRefs(fixtureRefs);
    setRunning(true);
    setResult(null);
    setError('');
    try {
      const response = await runV12ScanFromRunCenter(project, {
        base_url: resolvedTargetBaseUrl || undefined,
        scope_id: scopeId.trim() || undefined,
        environment_ref: environmentRef.trim() || undefined,
        source_id: resolvedSourceId || undefined,
        execution_mode: forceReadOnly ? 'safe_read_only' : undefined,
        ui_upload_fixture_ids: fixtureRefs,
        ui_upload_scenario_ids: scenarioRefs,
      });
      setResult(response);
      if (response.ok) {
        emitScanCompleted(project);
        void refreshContext();
        const tone = resultTone(response);
        toast.show(
          `验证完成：${executionStatusLabel(asText(response.execution_status))}，发现 ${response.total_findings || 0} 条`,
          tone === 'danger' ? 'danger' : tone === 'success' ? 'success' : 'warning',
        );
      } else {
        toast.show(response.message || response.error || '验证未成功执行', 'danger');
      }
    } catch (caught: unknown) {
      const message = caught instanceof Error ? caught.message : '验证执行失败';
      setResult({ ok: false, error: message });
      setError(message);
      toast.show(message, 'danger');
    } finally {
      setRunning(false);
    }
  }, [blockers.length, environmentRef, forceReadOnly, preflightReady, project, refreshContext, resolvedSourceId, resolvedTargetBaseUrl, scenarioState, scopeId, selectedFixtureRefs, toast]);

  if (!project) {
    return <section className="state-panel"><div className="state-panel-badge">客户选择</div><h2>请先选择客户项目</h2><p>选择客户后，系统会自动读取接入信息、企业资料和历史运行上下文。</p></section>;
  }

  const coverageGaps = asArray(result?.coverage_gaps);
  const testDataStatus = asText(result?.test_data_plan?.status);
  const testDataMissing = result?.test_data_plan?.missing_requirements || [];
  const runtimeContract = asRecord(result?.runtime_contract);
  const runtimeScenarioSummary = asRecord(runtimeContract.ui_upload_scenario_binding_summary);
  const runtimeScenarioRefs = textArray(runtimeScenarioSummary.scenario_refs);
  const runtimeFixtureSummary = asRecord(runtimeContract.ui_upload_fixture_binding_summary);
  const runtimeFixtureRefs = textArray(runtimeFixtureSummary.binding_refs);
  const scenarioBindingMismatch = Boolean(result && !sameIdentitySet(lastRunScenarioRefs, runtimeScenarioRefs));
  const missingExtraFixtures = lastRunFixtureRefs.filter((ref) => !runtimeFixtureRefs.includes(ref));
  const fixtureBindingMismatch = Boolean(result && missingExtraFixtures.length > 0);

  const reviewRunBlocker = () => {
    const targetId = preflightReady && !forceReadOnly && scenarioState.error
      ? 'run-safety-overrides'
      : 'run-blocker-details';
    document.getElementById(targetId)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  return (
    <div>
      <div className="page-header">
        <div>
          <span className="panel-kicker">自主验证入口</span>
          <h1>运行中心</h1>
          <p>先看运行前检查结论，再决定是否启动。系统接入、企业资料和测试凭据只负责解释上下文，最终是否可运行始终由后端 Preflight 决定。</p>
        </div>
        <div className="settings-actions">
          <button type="button" className="btn btn-secondary" onClick={() => navigateToProjectPath('/materials', project)}>企业资料</button>
          <button type="button" className="btn btn-secondary" onClick={() => navigateToProjectPath('/settings', project)}>接入信息</button>
        </div>
      </div>

      <RunPreflightDecisionSnapshot
        presentation={preflightPresentation}
        running={running}
        runDisabled={runDisabled}
        onRun={() => void runStandardScan()}
        onRefresh={() => void refreshContext()}
        onReview={reviewRunBlocker}
      />

      {!loadingPreflight && !preflightError && !preflightReady && blockers.length > 0 && (
        <section className="card mb-4 status-card status-warning" id="run-blocker-details">
          <span className="panel-kicker">运行阻断详情</span>
          <h2>后端报告 {blockers.length} 项真实阻断</h2>
          <p className="muted">首屏只突出第一个上报阻断；这里保留全部后端原因。前端不会根据代码名称自行判断哪个资料或配置一定是根因。</p>
          <ul>{blockers.map((reason) => <li key={`${reason.code}-${reason.message}`}>{reason.message}</li>)}</ul>
          <details className="mt-3">
            <summary>查看技术原因代码</summary>
            <ul>{blockers.map((reason) => <li key={`code-${reason.code}-${reason.message}`}><code>{reason.code || 'UNSPECIFIED'}</code></li>)}</ul>
          </details>
          <div className="settings-actions">
            <button type="button" className="btn btn-secondary" onClick={() => navigateToProjectPath('/materials', project)}>核对企业资料</button>
            <button type="button" className="btn btn-secondary" onClick={() => navigateToProjectPath('/settings', project)}>核对接入信息</button>
            <button type="button" className="btn btn-secondary" onClick={() => void refreshContext()} disabled={loadingPreflight}>重新检查</button>
          </div>
        </section>
      )}

      <details className="card mb-4">
        <summary>
          <strong>本次自动选择与安全边界</strong>
          <span className="muted">用于核对，不参与替代 Preflight 结论</span>
        </summary>
        <div className="mt-3">
          <p className="muted">后台会自动选择目标服务、有效资料快照、登录方式、测试数据方案和可执行场景；以下内容只是本次上下文说明。</p>
          <div className="settings-grid">
            <div><span className="muted">自动目标</span><p>{enabledServices[0] ? `${serviceDisplayName(enabledServices[0])} · ${asText(enabledServices[0].base_url)}` : (serviceError ? `读取失败：${serviceError}` : '由后台从项目上下文解析')}</p></div>
            <div><span className="muted">自动资料</span><p>{sourceError ? `读取失败：${sourceError}` : resolvedSourceId ? `${apiSources.find((source) => source.source_id === resolvedSourceId)?.filename || resolvedSourceId}` : `${activeSources.length} 份 active 资料可供后台自动选择`}</p></div>
            <div><span className="muted">自动场景</span><p>{forceReadOnly ? '本次只读熔断，跳过全部上传场景' : scenarioState.loading ? '正在同步审批场景…' : scenarioState.error ? `同步失败：${scenarioState.error}` : scenarioState.refs.length > 0 ? `${scenarioState.refs.length} 个已审批 UI 场景由后台自动纳入` : '普通接口、页面与只读验证由后台自动生成'}</p></div>
            <div><span className="muted">自动观察</span><p>{configuredDbCount > 0 ? `接口、页面及 ${configuredDbCount} 组数据库观察自动编排` : '接口与页面观察自动编排；数据库为可选增强'}</p></div>
            <div><span className="muted">安全边界</span><p>{forceReadOnly ? '强制只读已开启，后台不会发送写请求' : '环境类型、审批、before/after 与 cleanup 由后台门禁控制'}</p></div>
          </div>
          <div className="settings-actions">
            <button type="button" className="btn btn-secondary" onClick={() => navigateToProjectPath('/dashboard', project)}>查看系统总览</button>
          </div>
        </div>
      </details>

      <details className="card mb-4" id="run-safety-overrides">
        <summary><strong>异常覆盖与安全熔断</strong> <span className="muted">仅在后台识别错误或需要紧急只读时使用</span></summary>
        <p className="muted mt-3">正常运行不需要维护以下字段。填写后只覆盖本次执行，不改变后台的长期自动理解责任。</p>
        <div className="settings-grid">
          <label className="form-field"><span>临时目标地址</span><input value={targetBaseUrl} onChange={(event) => setTargetBaseUrl(event.target.value)} placeholder="留空则自动使用项目接入地址" /></label>
          <label className="form-field"><span>临时范围</span><input value={scopeId} onChange={(event) => setScopeId(event.target.value)} placeholder="留空则自动使用项目范围" /></label>
          <label className="form-field"><span>临时环境引用</span><input value={environmentRef} onChange={(event) => setEnvironmentRef(event.target.value)} placeholder="留空则自动读取环境配置" /></label>
          <label className="form-field"><span>临时资料源</span><select value={selectedSourceId} onChange={(event) => setSelectedSourceId(event.target.value)}><option value="">后台自动选择</option>{apiSources.map((source) => <option key={source.source_id} value={source.source_id}>{source.filename || source.source_id} · {source.source_type}</option>)}</select></label>
        </div>
        <label className="settings-enable-toggle mt-3"><input type="checkbox" checked={forceReadOnly} onChange={(event) => setForceReadOnly(event.target.checked)} />强制只读熔断：本次验证禁止任何写入</label>

        <RunUploadFixtureSelector
          fixtures={approvedFixtures}
          selectedRefs={selectedFixtureRefs}
          loading={loadingFixtures}
          error={fixtureError}
          onToggle={toggleFixture}
          onScenarioStateChange={handleScenarioStateChange}
          onOpenSettings={() => navigateToProjectPath('/settings', project)}
          onRefresh={() => void refreshContext()}
        />
      </details>

      {error && <section className="state-panel"><div className="state-panel-badge">需要处理</div><h2>验证未启动</h2><p>{error}</p></section>}

      {result && (
        <section className={`card mb-4 status-card status-${resultTone(result)}`}>
          <span className="panel-kicker">真实运行回执</span>
          <h2>受控运行结果</h2>
          <div className="settings-grid">
            <div><span className="muted">执行状态</span><p>{executionStatusLabel(asText(result.execution_status))}</p></div>
            <div><span className="muted">发现问题</span><p>{result.total_findings ?? 0}</p></div>
            <div><span className="muted">耗时</span><p>{asNum(result.total_ms)} ms</p></div>
            <div><span className="muted">覆盖度</span><p>{asNum(result.coverage)}</p></div>
            <div><span className="muted">真实执行证据</span><p>{harEntries(result.auto_har).length} 条真实请求</p></div>
            <div><span className="muted">结果评级</span><p>{result.grade || '未评级'}</p></div>
            <div><span className="muted">审批上传场景</span><p>{runtimeScenarioRefs.length}/{lastRunScenarioRefs.length} 已注入</p></div>
            <div><span className="muted">运行 Fixture 总数</span><p>{runtimeFixtureRefs.length}</p></div>
          </div>

          {!result.ok && <p className="settings-inline-feedback">失败原因：{result.message || result.error || '未知错误'}</p>}
          {scenarioBindingMismatch && <p className="settings-inline-feedback" role="alert">请求的审批上传场景与运行合同不一致。请求 {lastRunScenarioRefs.length} 个，后端确认 {runtimeScenarioRefs.length} 个；请检查撤销、来源/角色漂移及 Registry 状态。</p>}
          {fixtureBindingMismatch && <p className="settings-inline-feedback" role="alert">额外补充 Fixture 中有 {missingExtraFixtures.length} 个未进入运行合同；请检查撤销、文件漂移和项目范围。</p>}
          {!scenarioBindingMismatch && lastRunScenarioRefs.length > 0 && <p className="muted">运行合同已精确确认全部 {lastRunScenarioRefs.length} 个审批上传场景；是否完成上传、提交和业务恢复，请以浏览器步骤与 cleanup receipt 为准。</p>}

          {testDataStatus && testDataStatus !== 'ready' && testDataMissing.length > 0 && <div className="mt-3"><h3>后台仍需补齐的测试数据条件</h3><ul>{testDataMissing.map((item) => <li key={item}>{item}</li>)}</ul></div>}
          {coverageGaps.length > 0 && <div className="mt-3"><h3>本次未覆盖范围（{coverageGaps.length}）</h3><ul>{coverageGaps.slice(0, 8).map((gap, index) => { const row = asRecord(gap); return <li key={`${asText(row.code)}-${index}`}>{asText(row.kind) || asText(row.code) || '覆盖缺口'}{asText(row.message) ? `：${asText(row.message)}` : ''}</li>; })}</ul></div>}

          <details className="settings-auth-section mt-3">
            <summary><strong>运行技术回执</strong> <span className="muted">用于审计和排查，不需要日常维护</span></summary>
            <div className="settings-grid mt-3">
              <div><span className="muted">扫描 ID</span><p>{result.scan_id || '未生成'}</p></div>
              <div><span className="muted">Campaign 状态</span><p>{asText(result.campaign?.campaign_status) || '未报告'}</p></div>
              <div><span className="muted">测试数据合同</span><p>{testDataStatus || '未报告'}</p></div>
              <div><span className="muted">自动 UI 场景</span><p>{forceReadOnly ? '只读熔断已跳过' : `${runtimeScenarioRefs.length}/${lastRunScenarioRefs.length} 已确认`}</p></div>
              <div><span className="muted">额外 Fixture</span><p>{lastRunFixtureRefs.length > 0 ? `${lastRunFixtureRefs.length - missingExtraFixtures.length}/${lastRunFixtureRefs.length} 已确认` : '未使用额外绑定'}</p></div>
            </div>
            <h3>真实 HTTP 请求 · HAR 状态</h3>
            {harEntries(result.auto_har).length === 0 ? <p className="muted">本次没有捕获到 HTTP 请求，通常表示目标未联通或运行被安全门禁阻断。</p> : <ul>{harEntries(result.auto_har).slice(0, 50).map((entry, index) => <li key={`${harRequestLabel(entry)}-${index}`}><code>{harRequestLabel(entry)}</code> · HTTP {harStatusLabel(entry)}</li>)}</ul>}
          </details>

          <div className="settings-actions">
            <button type="button" className="btn btn-primary" onClick={() => navigateToProjectPath('/dashboard', project)}>查看系统总览</button>
            {(result.total_findings || 0) > 0 && <button type="button" className="btn btn-secondary" onClick={() => navigateToProjectPath('/findings', project)}>查看问题清单</button>}
            {(result.total_findings || 0) > 0 && <button type="button" className="btn btn-secondary" onClick={() => navigateToProjectPath('/evidence', project)}>查看证据链</button>}
            {coverageGaps.length > 0 && <button type="button" className="btn btn-secondary" onClick={() => navigateToProjectPath('/coverage', project)}>查看未覆盖范围</button>}
          </div>
        </section>
      )}
    </div>
  );
}

export default EnterpriseCampaigns;
