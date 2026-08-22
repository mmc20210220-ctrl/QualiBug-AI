import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { emitScanCompleted } from '../api/data';
import {
  getScanPreflight,
  getServiceCredentials,
  getKnowledgeAsset,
  type ScanPreflight,
  type V12ScanResult,
} from '../api/client';
import { runV12ScanFromRunCenter } from '../api/run-center';
import { useToast } from '../components/useToast';
import { usePageTitle } from '../lib/page-title';
import { useProjectNavigation } from '../lib/project-navigation';
import { asArray, asRecord, asText } from '../lib/value-guards';
import { hasConfiguredAuthMaterial, type SavedServiceConfig } from '../lib/settings-utils';

type SourceSummary = { source_id: string; filename: string; status: string };

function extractServices(payload: unknown): SavedServiceConfig[] {
  const root = asRecord(payload);
  return Array.isArray(root.services) ? root.services.map((item) => asRecord(item) as SavedServiceConfig) : [];
}

function extractSources(payload: unknown): SourceSummary[] {
  const asset = asRecord(asRecord(payload).knowledge_asset);
  return asArray(asset.sources || asset.source_inventory).map((value) => {
    const source = asRecord(value);
    return {
      source_id: asText(source.source_id) || asText(source.id),
      filename: asText(source.filename) || asText(source.original_name) || asText(source.name),
      status: asText(source.status) || 'active',
    };
  }).filter((source) => source.source_id && source.status !== 'deleted');
}

function serviceDisplayName(service: SavedServiceConfig): string {
  return asText(service.name) || asText(service.base_url) || '未命名服务';
}

export function Integration() {
  usePageTitle('接入');
  const [params] = useSearchParams();
  const project = params.get('project')?.trim() || '';
  const toast = useToast();
  const { navigateToProjectPath } = useProjectNavigation();

  const [preflight, setPreflight] = useState<ScanPreflight | null>(null);
  const [loadingPreflight, setLoadingPreflight] = useState(false);
  const [preflightError, setPreflightError] = useState('');
  const [services, setServices] = useState<SavedServiceConfig[]>([]);
  const [sources, setSources] = useState<SourceSummary[]>([]);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<V12ScanResult | null>(null);
  const [error, setError] = useState('');

  const refreshContext = useCallback(async () => {
    if (!project) {
      setPreflight(null);
      setPreflightError('');
      setServices([]);
      setSources([]);
      return;
    }
    setLoadingPreflight(true);
    setPreflightError('');
    const [preflightResult, serviceResult, knowledgeResult] = await Promise.allSettled([
      getScanPreflight(project),
      getServiceCredentials(project),
      getKnowledgeAsset(project),
    ]);
    if (preflightResult.status === 'fulfilled') {
      setPreflight(preflightResult.value);
    } else {
      setPreflight(null);
      setPreflightError(preflightResult.reason instanceof Error ? preflightResult.reason.message : '运行前检查失败');
    }
    setServices(serviceResult.status === 'fulfilled' ? extractServices(serviceResult.value) : []);
    setSources(knowledgeResult.status === 'fulfilled' ? extractSources(knowledgeResult.value) : []);
    setLoadingPreflight(false);
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
  const configuredAuthCount = useMemo(
    () => enabledServices.filter((service) => hasConfiguredAuthMaterial(service)).length,
    [enabledServices],
  );

  const startVerify = useCallback(async () => {
    if (!project) { setError('请先选择客户项目。'); return; }
    if (!preflight?.ready) {
      setError(preflight?.reasons?.length
        ? `运行前检查仍有 ${preflight.reasons.length} 项阻断，请先处理后重新检查。`
        : '运行前检查尚未通过，请重新检查运行条件。');
      return;
    }
    setConfirmOpen(false);
    setRunning(true);
    setResult(null);
    setError('');
    try {
      const response = await runV12ScanFromRunCenter(project, {});
      setResult(response);
      if (response.ok) {
        emitScanCompleted(project);
        void refreshContext();
        toast.show(
          `验证完成，发现 ${response.total_findings || 0} 条问题`,
          (response.total_findings || 0) > 0 ? 'warning' : 'success',
        );
      } else {
        toast.show(response.message || response.error || '验证未成功执行', 'danger');
      }
    } catch (caught: unknown) {
      const message = caught instanceof Error ? caught.message : '验证执行失败';
      setError(message);
      toast.show(message, 'danger');
    } finally {
      setRunning(false);
    }
  }, [preflight, project, refreshContext, toast]);

  if (!project) {
    return (
      <section className="state-panel">
        <div className="state-panel-badge">客户选择</div>
        <h2>请先选择客户项目</h2>
        <p>选择客户后，这里会展示系统地址、测试账号、企业资料的接入状态，并提供一键开始验证。</p>
      </section>
    );
  }

  const confirmTarget = enabledServices[0];
  const confirmLines = [
    { label: '系统', value: confirmTarget ? serviceDisplayName(confirmTarget) : '未接入' },
    { label: '地址', value: confirmTarget ? asText(confirmTarget.base_url) : '未接入' },
    { label: '测试身份', value: `${configuredAuthCount} 组` },
    { label: '企业资料', value: `${activeSources.length} 个来源` },
  ];

  return (
    <div>
      <div className="page-header">
        <div>
          <span className="panel-kicker">一次接入 · 一键验证</span>
          <h1>接入</h1>
          <p>把系统地址、测试账号和企业资料交给 QualiBug，其余理解与执行由后端自动完成。</p>
        </div>
        <div className="settings-actions">
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => setConfirmOpen(true)}
            disabled={running || loadingPreflight}
          >
            {running ? '正在验证…' : '开始验证'}
          </button>
        </div>
      </div>

      <section className="customer-summary-grid" aria-label="接入状态">
        <article className={`customer-summary-card tone-${enabledServices.length > 0 ? 'success' : 'warning'}`}>
          <span>系统</span>
          <strong>{enabledServices.length > 0 ? `${enabledServices.length} 个服务` : '待接入'}</strong>
          <small>{enabledServices[0] ? `${serviceDisplayName(enabledServices[0])} · ${asText(enabledServices[0].base_url)}` : '至少接入一个测试环境地址，真实执行才能开始。'}</small>
          <div className="settings-mt-10">
            <button type="button" className="btn btn-secondary settings-btn-mini" onClick={() => navigateToProjectPath('/settings', project)}>{enabledServices.length > 0 ? '管理地址' : '接入系统'}</button>
          </div>
        </article>
        <article className={`customer-summary-card tone-${configuredAuthCount > 0 ? 'success' : 'warning'}`}>
          <span>测试身份</span>
          <strong>{configuredAuthCount > 0 ? `${configuredAuthCount} 组` : '待配置'}</strong>
          <small>{configuredAuthCount > 0 ? '账号、Token 或 API Key 已可用于真实登录。' : '缺少鉴权材料时，登录后业务链可能被阻断。'}</small>
          <div className="settings-mt-10">
            <button type="button" className="btn btn-secondary settings-btn-mini" onClick={() => navigateToProjectPath('/settings', project)}>{configuredAuthCount > 0 ? '管理凭据' : '补充账号'}</button>
          </div>
        </article>
        <article className={`customer-summary-card tone-${activeSources.length > 0 ? 'success' : 'warning'}`}>
          <span>企业资料</span>
          <strong>{activeSources.length > 0 ? `${activeSources.length} 个可用来源` : '待连接'}</strong>
          <small>{activeSources.length > 0 ? `${sources.length} 份资料已接入。` : '连接企业资料后，QualiBug 才能理解业务规则与不变量。'}</small>
          <div className="settings-mt-10">
            <button type="button" className="btn btn-secondary settings-btn-mini" onClick={() => navigateToProjectPath('/materials', project)}>{activeSources.length > 0 ? '管理资料' : '连接资料'}</button>
          </div>
        </article>
      </section>

      {error && (
        <section className="state-panel">
          <div className="state-panel-badge">需要处理</div>
          <h2>验证未启动</h2>
          <p>{error}</p>
        </section>
      )}

      {result && (
        <section className={`card mb-4 status-card ${result.ok ? 'status-warning' : 'status-danger'}`}>
          <span className="panel-kicker">验证回执</span>
          <h2>{result.ok ? `验证完成，发现 ${result.total_findings || 0} 条问题` : '验证未成功执行'}</h2>
          <div className="settings-grid">
            <div><span className="muted">执行状态</span><p>{asText(result.execution_status) || '未报告'}</p></div>
            <div><span className="muted">发现问题</span><p>{result.total_findings ?? 0}</p></div>
            <div><span className="muted">耗时</span><p>{result.total_ms != null ? `${result.total_ms} ms` : '未报告'}</p></div>
            <div><span className="muted">覆盖度</span><p>{result.coverage != null ? `${result.coverage}%` : '未报告'}</p></div>
          </div>
          {!result.ok && <p className="settings-inline-feedback">失败原因：{result.message || result.error || '未知错误'}</p>}
          <div className="settings-actions">
            <button type="button" className="btn btn-primary" onClick={() => navigateToProjectPath('/findings', project)}>查看问题</button>
            <button type="button" className="btn btn-secondary" onClick={() => navigateToProjectPath('/dashboard', project)}>返回总览</button>
          </div>
        </section>
      )}

      {confirmOpen && (
        <section className="card mb-4 status-card" aria-label="确认开始验证">
          <span className="panel-kicker">确认</span>
          <h2>开始验证</h2>
          <p className="muted">本次验证将使用以下已接入的企业上下文自动执行：</p>
          <div className="settings-grid">
            {confirmLines.map((line) => (
              <div key={line.label}><span className="muted">{line.label}</span><p>{line.value}</p></div>
            ))}
          </div>
          <div className="settings-actions">
            <button type="button" className="btn btn-primary" onClick={() => void startVerify()}>开始验证</button>
            <button type="button" className="btn btn-secondary" onClick={() => setConfirmOpen(false)}>取消</button>
          </div>
        </section>
      )}

      {loadingPreflight && <p className="muted">正在核对运行前条件…</p>}
      {preflightError && <p className="settings-inline-feedback" role="alert">{preflightError}</p>}
    </div>
  );
}

export default Integration;
