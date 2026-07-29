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
import { RunUploadFixtureSelector } from '../components/run/RunUploadFixtureSelector';
import { useToast } from '../components/useToast';
import { usePageTitle } from '../lib/page-title';
import { useProjectNavigation } from '../lib/project-navigation';

type JsonRecord = Record<string, unknown>;
type DataStrategy = 'safe_read_only' | 'reuse_verified_existing' | 'create_disposable' | 'approved_fixture_setup';
type SavedServiceConfig = { name?: string; base_url?: string; enabled?: boolean; auth?: JsonRecord; db?: JsonRecord };
type SourceSummary = { source_id: string; filename: string; source_type: string; status: string };

function asText(value: unknown): string { return typeof value === 'string' ? value.trim() : ''; }
function asRecord(value: unknown): JsonRecord { return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as JsonRecord : {}; }
function asArray(value: unknown): unknown[] { return Array.isArray(value) ? value : []; }
function asNumber(value: unknown): number { const parsed = Number(value); return Number.isFinite(parsed) ? parsed : 0; }

// Real runtime execution evidence surfaced verbatim from the backend scan
// response so the one-click run is observable and reproducible from the product
// itself — never a synthesized placeholder.
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
  const status = asNumber(response.status) || asNumber(response.status_code) || asNumber(entry.status);
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

// Never dress a blocked/plan-only/partial run up as a clean success. The Run
// Center must reflect the backend execution_status + campaign status verbatim.
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
  const [services, setServices] = useState<SavedServiceConfig[]>([]);
  const [serviceError, setServiceError] = useState('');
  const [sources, setSources] = useState<SourceSummary[]>([]);
  const [sourceError, setSourceError] = useState('');
  const [approvedFixtures, setApprovedFixtures] = useState<UploadFixtureRecord[]>([]);
  const [selectedFixtureRefs, setSelectedFixtureRefs] = useState<string[]>([]);
  const [fixtureError, setFixtureError] = useState('');
  const [loadingFixtures, setLoadingFixtures] = useState(false);

  const [targetBaseUrl, setTargetBaseUrl] = useState('');
  const [scopeId, setScopeId] = useState('');
  const [environmentRef, setEnvironmentRef] = useState('');
  const [strategy, setStrategy] = useState<DataStrategy>('safe_read_only');
  const [selectedSourceId, setSelectedSourceId] = useState('');

  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<V12ScanResult | null>(null);
  const [error, setError] = useState('');

  const refreshContext = useCallback(async () => {
    if (!project) {
      setPreflight(null);
      setServices([]);
      setSources([]);
      setApprovedFixtures([]);
      setSelectedFixtureRefs([]);
      return;
    }
    setLoadingPreflight(true);
    setLoadingFixtures(true);
    setError('');
    const [preflightResult, serviceResult, knowledgeResult, fixtureResult] = await Promise.allSettled([
      getScanPreflight(project),
      getServiceCredentials(project),
      getKnowledgeAsset(project),
      listUploadFixtures(project, false),
    ]);
    if (preflightResult.status === 'fulfilled') setPreflight(preflightResult.value);
    else { setPreflight(null); setError(preflightResult.reason instanceof Error ? preflightResult.reason.message : '运行前检查失败'); }
    if (serviceResult.status === 'fulfilled') {
      const payload = asRecord(serviceResult.value);
      setServices(asArray(payload.services).map((item) => asRecord(item) as SavedServiceConfig));
      setServiceError('');
    } else { setServices([]); setServiceError(serviceResult.reason instanceof Error ? serviceResult.reason.message : '服务配置读取失败'); }
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
    } else { setSources([]); setSourceError(knowledgeResult.reason instanceof Error ? knowledgeResult.reason.message : '资料读取失败'); }
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

  const enabledServices = useMemo(() => services.filter((service) => service.enabled !== false && asText(service.base_url)), [services]);
  const resolvedTargetBaseUrl = targetBaseUrl.trim() || asText(enabledServices[0]?.base_url);
  const configuredAuthCount = useMemo(() => services.filter(hasConfiguredAuth).length, [services]);
  const configuredDbCount = useMemo(() => services.filter(hasConfiguredDb).length, [services]);

  useEffect(() => {
    if (!targetBaseUrl.trim() && enabledServices.length === 1) setTargetBaseUrl(asText(enabledServices[0].base_url));
  }, [enabledServices, targetBaseUrl]);

  // Filter registered sources to only show OpenAPI-type (executable) sources.
  const apiSources = useMemo(() => {
    const apiTypes = new Set(['openapi', 'openapi3', 'swagger', 'postman', 'api_spec']);
    return sources.filter((source) => apiTypes.has((source.source_type || '').toLowerCase()));
  }, [sources]);

  // Auto-select first OpenAPI source when sources load and none is selected.
  useEffect(() => {
    if (!selectedSourceId && apiSources.length > 0) {
      setSelectedSourceId(apiSources[0].source_id);
    }
  }, [apiSources, selectedSourceId]);

  const blockers = preflight?.reasons || [];
  const preflightReady = Boolean(preflight?.ready);

  const readinessCards = [
    { label: '项目上下文', value: project ? '已选择' : '待选择', note: project || '请先选择客户项目', tone: project ? 'success' : 'warning' },
    { label: '服务目标', value: enabledServices.length > 0 ? `${enabledServices.length} 个已配置` : '待配置', note: enabledServices[0] ? `默认目标：${serviceDisplayName(enabledServices[0])}` : '请到项目设置维护服务 URL', tone: enabledServices.length > 0 ? 'success' : 'warning' },
    { label: '鉴权账号', value: configuredAuthCount > 0 ? `${configuredAuthCount} 组已配置` : '待配置', note: configuredAuthCount > 0 ? '运行时优先复用已保存鉴权' : '请补齐账号 / Token / API Key', tone: configuredAuthCount > 0 ? 'success' : 'warning' },
    { label: '数据库校验', value: configuredDbCount > 0 ? `${configuredDbCount} 组已配置` : '可选', note: configuredDbCount > 0 ? '可用于 DB 侧一致性验证' : '未配置数据库时仅执行接口/页面侧验证', tone: configuredDbCount > 0 ? 'success' : 'neutral' },
    { label: '来源资料', value: sources.length > 0 ? `${sources.length} 份已入库` : '待导入', note: sources.length > 0 ? '扫描时自动绑定最近入库来源快照' : '请在企业资料导入 PRD / OpenAPI 等', tone: sources.length > 0 ? 'success' : 'warning' },
    { label: '上传 Fixture', value: loadingFixtures ? '读取中' : selectedFixtureRefs.length > 0 ? `已选 ${selectedFixtureRefs.length} 个` : `${approvedFixtures.length} 个可选`, note: selectedFixtureRefs.length > 0 ? '本次运行将请求受控上传写模式' : '不自动选择；按场景需要显式勾选', tone: loadingFixtures ? 'neutral' : selectedFixtureRefs.length > 0 ? 'warning' : approvedFixtures.length > 0 ? 'success' : 'neutral' },
    { label: '运行就绪', value: loadingPreflight ? '检查中' : preflightReady ? '已就绪' : `${blockers.length} 项待补齐`, note: loadingPreflight ? '正在读取运行前检查' : preflightReady ? '可以一键运行检测' : '补齐下方阻断项后再运行', tone: loadingPreflight ? 'neutral' : preflightReady ? 'success' : 'danger' },
  ];

  const buildTestDataContract = useCallback((): JsonRecord => {
    if (strategy === 'safe_read_only') return { strategy };
    return { strategy, write_approved: false };
  }, [strategy]);

  const toggleFixture = useCallback((bindingRef: string) => {
    const normalized = bindingRef.trim();
    if (!normalized) return;
    setSelectedFixtureRefs((current) => (
      current.includes(normalized)
        ? current.filter((item) => item !== normalized)
        : [...current, normalized]
    ));
  }, []);

  const runStandardScan = useCallback(async () => {
    if (!project) { setError('请先选择客户项目。'); return; }
    setRunning(true);
    setResult(null);
    setError('');
    try {
      const response = await runV12ScanFromRunCenter(project, {
        base_url: resolvedTargetBaseUrl || undefined,
        scope_id: scopeId.trim() || undefined,
        environment_ref: environmentRef.trim() || undefined,
        source_id: selectedSourceId || undefined,
        execution_mode: selectedFixtureRefs.length > 0 ? 'approved_sandbox_write' : undefined,
        test_data_contract: buildTestDataContract(),
        ui_upload_fixture_ids: selectedFixtureRefs,
      });
      setResult(response);
      if (response.ok) {
        emitScanCompleted(project);
        void refreshContext();
        const status = asText(response.execution_status).toLowerCase();
        const cleanExecuted = status === 'executed';
        toast.show(
          `一键运行完成：${executionStatusLabel(asText(response.execution_status))}，发现 ${response.total_findings || 0} 条`,
          !cleanExecuted ? 'warning' : (response.total_findings ? 'warning' : 'success'),
        );
      } else {
        toast.show(response.message || response.error || '扫描未成功执行', 'danger');
      }
    } catch (caught: unknown) {
      const message = caught instanceof Error ? caught.message : '扫描执行失败';
      setResult({ ok: false, error: message });
      setError(message);
      toast.show(message, 'danger');
    } finally {
      setRunning(false);
    }
  }, [buildTestDataContract, environmentRef, project, refreshContext, resolvedTargetBaseUrl, scopeId, selectedFixtureRefs, selectedSourceId, toast]);

  if (!project) {
    return <section className="state-panel"><div className="state-panel-badge">客户选择</div><h2>请先选择客户项目</h2><p>运行中心必须绑定真实客户上下文，才能把项目配置、资料、执行和结果回显串成同一条闭环。</p></section>;
  }

  const coverageGaps = asArray(result?.coverage_gaps);
  const testDataStatus = asText(result?.test_data_plan?.status);
  const testDataMissing = result?.test_data_plan?.missing_requirements || [];
  const runtimeContract = asRecord(result?.runtime_contract);
  const runtimeFixtureSummary = asRecord(runtimeContract.ui_upload_fixture_binding_summary);
  const runtimeFixtureCount = asNumber(runtimeFixtureSummary.binding_count);
  const fixtureBindingMismatch = Boolean(
    result
    && selectedFixtureRefs.length > 0
    && runtimeFixtureCount !== selectedFixtureRefs.length,
  );

  return (
    <div>
      <div className="page-header">
        <div>
          <span className="panel-kicker">正式运行入口</span>
          <h1>运行中心</h1>
          <p>这里是项目配置、资料导入之后的唯一执行入口。点击「执行标准扫描」即可一键运行检测：系统会自动读取当前项目的服务 URL、鉴权、来源资料快照、范围与环境，真实执行后把结果统一回收到风险总览、行为验证与证据链。</p>
        </div>
        <div className="settings-actions">
          <button type="button" className="btn btn-secondary" onClick={() => navigateToProjectPath('/settings', project)}>前往项目设置</button>
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

      {!loadingPreflight && !preflightReady && blockers.length > 0 && (
        <section className="card mb-4 status-card status-warning">
          <h2>运行前检查：还差 {blockers.length} 项</h2>
          <p className="muted">系统不会在缺少条件时沉默失败。请先补齐以下阻断项，再运行检测：</p>
          <ul>
            {blockers.map((reason) => (
              <li key={reason.code}><strong>{reason.code}</strong>：{reason.message}</li>
            ))}
          </ul>
          <div className="settings-actions">
            <button type="button" className="btn btn-secondary" onClick={() => navigateToProjectPath('/settings', project)}>补齐服务与鉴权</button>
            <button type="button" className="btn btn-secondary" onClick={() => navigateToProjectPath('/settings', project)}>补齐企业资料</button>
            <button type="button" className="btn btn-secondary" onClick={() => void refreshContext()} disabled={loadingPreflight}>重新检查</button>
          </div>
        </section>
      )}

      <section className="card mb-4">
        <h2>一键运行检测</h2>
        <p className="muted">用于最小可商用闭环首轮验证。系统会优先复用当前项目的服务配置、企业资料和来源快照；不填写的字段由后端从项目上下文自动推断。</p>
        <div className="settings-grid">
          <label className="form-field">
            <span>目标地址（可选，缺省用已配置服务）</span>
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
          <label className="form-field">
            <span>测试数据策略</span>
            <select value={strategy} onChange={(event) => setStrategy(event.target.value as DataStrategy)}>
              <option value="safe_read_only">安全只读（默认，不写入被测系统）</option>
              <option value="reuse_verified_existing">复用已核验数据</option>
              <option value="create_disposable">隔离一次性数据（需审批回执）</option>
              <option value="approved_fixture_setup">已批准 Fixture（需审批回执）</option>
            </select>
          </label>
          <label className="form-field">
            <span>API 接口文档源（可选，默认自动选择最新 OpenAPI 源）</span>
            <select value={selectedSourceId} onChange={(event) => setSelectedSourceId(event.target.value)}>
              <option value="">自动推断（从已注册源中选择）</option>
              {apiSources.map((source) => (
                <option key={source.source_id} value={source.source_id}>{source.filename || source.source_id} · {source.source_type}</option>
              ))}
            </select>
            {apiSources.length === 0 && sources.length > 0 && (
              <small className="muted" style={{ color: 'var(--warning-color, #d97706)' }}>⚠ 已入库资料中未检测到 API 接口规范（OpenAPI / Swagger / Postman），扫描可能无法生成可执行探针。请上传接口文档后再运行。</small>
            )}
          </label>
        </div>

        <RunUploadFixtureSelector
          fixtures={approvedFixtures}
          selectedRefs={selectedFixtureRefs}
          loading={loadingFixtures}
          error={fixtureError}
          onToggle={toggleFixture}
          onOpenSettings={() => navigateToProjectPath('/settings', project)}
          onRefresh={() => void refreshContext()}
        />

        <div className="settings-grid">
          <div><span className="muted">默认服务目标</span><p>{enabledServices[0] ? `${serviceDisplayName(enabledServices[0])} · ${asText(enabledServices[0].base_url)}` : (serviceError ? `未连通：${serviceError}` : '当前没有已启用服务，后端将尝试从项目上下文推断')}</p></div>
          <div><span className="muted">来源资料</span><p>{sourceError ? `未连通：${sourceError}` : sources.length > 0 ? `${sources.length} 份已入库，扫描时自动绑定最近来源快照` : '尚未导入来源资料'}</p></div>
          <div><span className="muted">已配置鉴权</span><p>{configuredAuthCount > 0 ? `${configuredAuthCount} 组可复用` : '当前未发现已配置鉴权账号或密钥'}</p></div>
          <div><span className="muted">运行前检查</span><p>{loadingPreflight ? '检查中…' : preflightReady ? '已就绪' : `${blockers.length} 项待补齐`}</p></div>
        </div>
        <div className="settings-actions">
          <button type="button" className="btn btn-primary" onClick={() => void runStandardScan()} disabled={running || loadingPreflight || loadingFixtures}>
            {running ? '正在运行检测…' : '执行标准扫描'}
          </button>
          <button type="button" className="btn btn-secondary" onClick={() => navigateToProjectPath('/dashboard', project)}>查看风险总览</button>
        </div>
        <p className="muted">运行成功后，结果会自动刷新到总览、缺陷和证据链页面；不会另起一套展示口径。阻断 / 仅计划 / 部分覆盖等状态会如实展示，不会伪装成通过。选择上传 Fixture 只提供受控文件绑定，实际上传必须由来源 UI 合同明确声明。</p>
      </section>

      {error && <section className="state-panel"><div className="state-panel-badge">需要处理</div><h2>运行未启动</h2><p>{error}</p></section>}

      {result && (
        <section className={`card mb-4 status-card status-${resultTone(result)}`}>
          <h2>受控运行结果</h2>
          <div className="settings-grid">
            <div><span className="muted">扫描 ID</span><p>{result.scan_id || '未生成'}</p></div>
            <div><span className="muted">执行状态</span><p>{executionStatusLabel(asText(result.execution_status))}</p></div>
            <div><span className="muted">发现问题</span><p>{result.total_findings ?? 0}</p></div>
            <div><span className="muted">耗时</span><p>{asNumber(result.total_ms)} ms</p></div>
            <div><span className="muted">结果评级</span><p>{result.grade || '未评级'}</p></div>
            <div><span className="muted">覆盖度</span><p>{asNumber(result.coverage)}</p></div>
            <div><span className="muted">Campaign 状态</span><p>{asText(result.campaign?.campaign_status) || '未报告'}</p></div>
            <div><span className="muted">测试数据合同</span><p>{testDataStatus || '未报告'}</p></div>
            <div><span className="muted">上传 Fixture 绑定</span><p>{selectedFixtureRefs.length > 0 ? `${runtimeFixtureCount}/${selectedFixtureRefs.length} 已注入运行合同` : '本次未选择'}</p></div>
          </div>
          {!result.ok && <p className="muted">失败原因：{result.message || result.error || '未知错误'}</p>}
          {fixtureBindingMismatch && (
            <p className="settings-inline-feedback" role="alert">本次选择了 {selectedFixtureRefs.length} 个 Fixture，但运行合同仅确认 {runtimeFixtureCount} 个。请检查审批是否撤销、文件是否漂移以及项目范围是否一致。</p>
          )}
          {selectedFixtureRefs.length > 0 && !fixtureBindingMismatch && (
            <p className="muted">运行合同已确认 {runtimeFixtureCount} 个上传绑定；是否真正执行 set_input_files，请以浏览器步骤回执和 cleanup receipt 为准。</p>
          )}
          <div className="mt-3">
            <h3>真实执行证据</h3>
            <p className="muted">HAR 状态：共 {harEntries(result.auto_har).length} 条真实请求记录{Object.keys(asRecord((result as JsonRecord).execution_evidence_summary)).length ? '，已附带执行证据摘要' : ''}。</p>
            {harEntries(result.auto_har).length === 0 ? (
              <p className="muted">本次运行未捕获到真实 HTTP 请求记录——通常表示目标未联通或运行被门禁拦截，请核对服务配置、来源绑定与执行审批。</p>
            ) : (
              <ul>
                {harEntries(result.auto_har).slice(0, 50).map((entry, index) => (
                  <li key={`${harRequestLabel(entry)}-${index}`}><code>{harRequestLabel(entry)}</code> · HTTP {harStatusLabel(entry)}</li>
                ))}
              </ul>
            )}
          </div>
          {testDataStatus && testDataStatus !== 'ready' && testDataMissing.length > 0 && (
            <div className="mt-3"><h3>测试数据缺口</h3><ul>{testDataMissing.map((item) => <li key={item}>{item}</li>)}</ul></div>
          )}
          {coverageGaps.length > 0 && (
            <div className="mt-3">
              <h3>未覆盖范围（{coverageGaps.length}）</h3>
              <ul>{coverageGaps.slice(0, 8).map((gap, index) => {
                const row = asRecord(gap);
                return <li key={`${asText(row.code)}-${index}`}>{asText(row.kind) || asText(row.code) || '覆盖缺口'}{asText(row.message) ? `：${asText(row.message)}` : ''}</li>;
              })}</ul>
            </div>
          )}
          <div className="settings-actions">
            <button type="button" className="btn btn-primary" onClick={() => navigateToProjectPath('/dashboard', project)}>查看风险总览</button>
            <button type="button" className="btn btn-secondary" onClick={() => navigateToProjectPath('/findings', project)}>查看客户缺陷</button>
            <button type="button" className="btn btn-secondary" onClick={() => navigateToProjectPath('/evidence', project)}>查看证据链</button>
          </div>
        </section>
      )}
    </div>
  );
}

export default EnterpriseCampaigns;
