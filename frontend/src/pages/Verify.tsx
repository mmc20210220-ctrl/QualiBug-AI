import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { getTestIntelligence, type TestIntelligenceAnalysis } from '../api/test-intelligence';
import { isCustomerReadyFinding, usePipelineData } from '../api/data';
import { EnterpriseCampaigns } from './EnterpriseCampaigns';
import { Skeleton, StatePanel } from '../components/dashboard/DashboardPrimitives';
import { buildProjectPath } from '../lib/project-navigation';
import { usePageTitle } from '../lib/page-title';
import { asArray, asRecord, asText } from '../lib/value-guards';
import type { Finding } from '../types';
import './Verify.css';

type VerifyView = 'workspace' | 'run-control';

function toneForStatus(value: string): 'success' | 'warning' | 'danger' | 'neutral' {
  const status = value.toLowerCase();
  if (['passed', 'pass', 'completed', 'executed', 'ready'].includes(status)) return 'success';
  if (['failed', 'fail', 'blocked', 'error', 'failed_safe'].includes(status)) return 'danger';
  if (['running', 'pending', 'partial', 'coverage_deferred', 'not_ready'].includes(status)) return 'warning';
  return 'neutral';
}

function statusLabel(value: string): string {
  const normalized = value.trim();
  if (!normalized) return '未上报';
  const map: Record<string, string> = {
    running: '运行中',
    completed: '已完成',
    executed: '已真实执行',
    passed: '已通过',
    pass: '已通过',
    failed: '执行失败',
    fail: '未通过',
    blocked: '已阻断',
    failed_safe: '检测异常',
    pending: '待处理',
    coverage_deferred: '部分范围待后续验证',
  };
  return map[normalized.toLowerCase()] || normalized;
}

export function Verify() {
  usePageTitle('验证');
  const [params, setParams] = useSearchParams();
  const project = params.get('project')?.trim() || '';
  const activeView: VerifyView = params.get('view') === 'run-control' ? 'run-control' : 'workspace';
  const { data, loading, error, refetch } = usePipelineData(project);
  const [intelligence, setIntelligence] = useState<TestIntelligenceAnalysis | null>(null);
  const [intelligenceLoading, setIntelligenceLoading] = useState(Boolean(project));
  const [intelligenceError, setIntelligenceError] = useState('');

  const selectView = (view: VerifyView) => {
    const next = new URLSearchParams(params);
    next.set('view', view);
    setParams(next, { replace: true });
  };

  const loadIntelligence = useCallback(async () => {
    if (!project) {
      setIntelligence(null);
      setIntelligenceError('');
      setIntelligenceLoading(false);
      return;
    }
    setIntelligenceLoading(true);
    setIntelligenceError('');
    try {
      setIntelligence(await getTestIntelligence(project));
    } catch (caught: unknown) {
      setIntelligence(null);
      setIntelligenceError(caught instanceof Error ? caught.message : '测试目标读取失败');
    } finally {
      setIntelligenceLoading(false);
    }
  }, [project]);

  useEffect(() => {
    void loadIntelligence();
  }, [loadIntelligence]);

  const record = asRecord(data);
  const campaign = asRecord(record.campaign);
  const scanMeta = asRecord(record.scan_meta);
  const pipelineHealth = asRecord(record.pipeline_health);
  const releaseGate = asRecord(record.release_gate);

  const runtimeStatus = asText(campaign.campaign_status)
    || asText(scanMeta.execution_status)
    || asText(pipelineHealth.status);
  const releaseStatus = asText(releaseGate.overall_status || releaseGate.verdict || releaseGate.status);
  const latestRunAt = asText(scanMeta.generated_at || scanMeta.completed_at || scanMeta.started_at);

  const findings = useMemo(
    () => ((record.defects || record.risks || []) as Finding[]).filter(isCustomerReadyFinding),
    [record.defects, record.risks],
  );
  const clues = asArray(record.clues);
  const visibleTargets = intelligence?.obligations.slice(0, 8) || [];
  const evidenceBackedFindings = findings.filter((finding) => (finding.evidence_chain?.length || 0) > 0).length;

  if (!project) {
    return (
      <StatePanel
        eyebrow="Verify · 真实验证"
        title="选择项目后进入 AI 验证工作台"
        description="Analyze 可以只基于企业资料工作；Verify 只展示真实项目运行、真实问题和真实证据。没有执行数据时不会伪造 Agent 步骤、浏览器画面或通过率。"
      />
    );
  }

  if (activeView === 'run-control') {
    return (
      <div className="verify-workspace">
        <header className="verify-header compact">
          <div>
            <span className="panel-kicker">Verify · Run Control</span>
            <h1>运行控制</h1>
            <p>继续复用现有 Preflight、服务配置、测试数据与真实扫描主链，不创建第二套运行系统。</p>
          </div>
          <button type="button" className="btn btn-secondary" onClick={() => selectView('workspace')}>返回验证工作台</button>
        </header>
        <EnterpriseCampaigns />
      </div>
    );
  }

  if (loading && !data) {
    return (
      <div className="verify-workspace">
        <div className="verify-header"><Skeleton h={18} w={120} br={5} /><Skeleton h={34} w="55%" br={7} /></div>
        <div className="verify-grid"><Skeleton h={420} br={16} /><Skeleton h={420} br={16} /><Skeleton h={420} br={16} /></div>
      </div>
    );
  }

  if (error && !data) {
    return <StatePanel eyebrow="Verify · 连接状态" title="无法读取当前项目运行结果" description={error} action={<button className="btn btn-primary" onClick={refetch}>重新连接</button>} />;
  }

  const runtimeTone = toneForStatus(runtimeStatus);
  const releaseTone = toneForStatus(releaseStatus);

  return (
    <div className="verify-workspace">
      <header className="verify-header">
        <div>
          <span className="panel-kicker">Verify · AI 原生验证工作台</span>
          <h1>把“应该怎样工作”连接到真实系统行为</h1>
          <p>
            左侧是 Analyze 已形成的验证目标；中间只展示后端真实上报的运行状态；右侧预留给 Browser / API / DB 的逐步骤执行视图。
            当前缺失的运行时 Grounding 会明确标记，不会用合成数据补齐。
          </p>
        </div>
        <div className="verify-header-actions">
          <button type="button" className="btn btn-primary" onClick={() => selectView('run-control')}>启动 / 管理运行</button>
          <Link className="btn btn-secondary" to={buildProjectPath('/analyze', project, 'view=test-targets')}>查看验证目标</Link>
        </div>
      </header>

      <section className="verify-summary" aria-label="当前验证摘要">
        <article>
          <span>验证目标</span>
          <strong>{intelligenceLoading ? '…' : intelligence ? intelligence.summary.obligationCount : '—'}</strong>
          <p>{intelligenceError || '来自 Test Intelligence 的证据化目标'}</p>
        </article>
        <article>
          <span>运行状态</span>
          <strong className={`tone-${runtimeTone}`}>{statusLabel(runtimeStatus)}</strong>
          <p>{latestRunAt ? `最近运行 ${latestRunAt}` : '后端未上报最近运行时间'}</p>
        </article>
        <article>
          <span>已确认问题</span>
          <strong>{findings.length}</strong>
          <p>{evidenceBackedFindings} 个带证据链 · {clues.length} 个后台补证线索</p>
        </article>
        <article>
          <span>Release Gate</span>
          <strong className={`tone-${releaseTone}`}>{statusLabel(releaseStatus)}</strong>
          <p>没有明确 Gate 回执时不会显示“可发布”</p>
        </article>
      </section>

      <section className="verify-grid" aria-label="验证工作台">
        <article className="verify-panel targets-panel">
          <div className="verify-panel-head">
            <div><span>01</span><div><strong>Test Targets</strong><small>必须验证什么</small></div></div>
            <Link to={buildProjectPath('/analyze', project, 'view=test-targets')}>全部目标</Link>
          </div>
          {intelligenceLoading ? (
            <div className="verify-panel-loading"><span className="spinner" /><p>正在读取验证目标…</p></div>
          ) : intelligenceError ? (
            <div className="verify-empty danger"><strong>验证目标读取失败</strong><p>{intelligenceError}</p><button className="btn btn-secondary btn-sm" onClick={() => void loadIntelligence()}>重试</button></div>
          ) : visibleTargets.length > 0 ? (
            <div className="verify-target-list">
              {visibleTargets.map((target) => (
                <div className="verify-target" key={target.obligationId}>
                  <div>
                    <strong>{target.title}</strong>
                    <p>{target.objective}</p>
                  </div>
                  <div className="verify-target-status">
                    <span>{target.obligationKind.replaceAll('_', ' ')}</span>
                    <small>{target.runtimeLinkage}</small>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="verify-empty"><strong>当前没有可交付验证目标</strong><p>先在 Analyze 中接入资料并形成有证据的 Test Targets。</p></div>
          )}
        </article>

        <article className="verify-panel execution-panel">
          <div className="verify-panel-head">
            <div><span>02</span><div><strong>Agent Execution</strong><small>真实运行状态</small></div></div>
            <button type="button" onClick={() => selectView('run-control')}>运行控制</button>
          </div>
          <div className={`verify-run-state tone-border-${runtimeTone}`}>
            <span>Current run</span>
            <strong>{statusLabel(runtimeStatus)}</strong>
            <p>{latestRunAt || '最近运行时间未上报'}</p>
          </div>
          <div className="verify-execution-truth">
            <strong>逐步骤 Agent Run 事件流尚未接入这个统一工作台</strong>
            <p>
              当前 Verify 只消费现有真实 Campaign / Pipeline 结果。后续 Runtime Grounding 接通后，这里应逐步显示 Action → Observation → Oracle → Evidence，
              但在后端提供真实事件前不会由前端构造步骤。
            </p>
          </div>
          <div className="verify-run-actions">
            <button type="button" className="btn btn-primary" onClick={() => selectView('run-control')}>进入真实运行控制</button>
            <Link className="btn btn-secondary" to={buildProjectPath('/release', project)}>查看发布结论</Link>
          </div>
        </article>

        <article className="verify-panel surface-panel">
          <div className="verify-panel-head">
            <div><span>03</span><div><strong>Execution Surface</strong><small>Browser / API / DB / Runtime</small></div></div>
          </div>
          <div className="verify-surface-placeholder">
            <div className="verify-surface-chrome"><span /><span /><span /><b>Live surface</b></div>
            <div className="verify-surface-body">
              <strong>当前运行未上报可嵌入的实时执行画面</strong>
              <p>这里只会展示真实 Browser 截图/视频、API 请求响应、DB 观察或 Trace；不会拿静态示意图冒充 Live View。</p>
            </div>
          </div>
          <div className="verify-evidence-types" aria-label="证据类型">
            <span>Screenshot</span><span>Video</span><span>Network</span><span>API</span><span>DB</span><span>Trace</span>
          </div>
        </article>
      </section>

      <section className="verify-findings">
        <div className="verify-findings-head">
          <div><span className="panel-kicker">Evidence-backed results</span><h2>本项目已确认问题</h2></div>
          <Link className="btn btn-secondary" to={buildProjectPath('/findings', project)}>查看全部问题</Link>
        </div>
        {findings.length > 0 ? (
          <div className="verify-finding-list">
            {findings.slice(0, 4).map((finding) => (
              <Link key={finding.id} to={buildProjectPath(`/findings/${finding.id}`, project)} className="verify-finding-card">
                <span className={`severity-badge ${finding.severity.toLowerCase()}`}>{finding.severity}</span>
                <div><strong>{finding.title}</strong><p>{finding.business_summary || finding.business_impact?.summary || '查看真实复现与证据'}</p></div>
                <small>{finding.evidence_chain?.length || 0} 条证据</small>
              </Link>
            ))}
          </div>
        ) : (
          <div className="verify-empty"><strong>当前没有已确认 Finding</strong><p>这不等于系统安全；请同时查看运行状态、覆盖边界与 Release Gate。</p></div>
        )}
      </section>
    </div>
  );
}

export default Verify;
