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
type WorkState = 'done' | 'active' | 'pending' | 'attention';

type AgentMilestone = {
  key: string;
  label: string;
  detail: string;
  state: WorkState;
};

function toneForStatus(value: string): 'success' | 'warning' | 'danger' | 'neutral' {
  const status = value.toLowerCase();
  if (['passed', 'pass', 'completed', 'executed', 'ready'].includes(status)) return 'success';
  if (['failed', 'fail', 'blocked', 'error', 'failed_safe', 'hold'].includes(status)) return 'danger';
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
    hold: '建议暂缓',
    ready: '可以发布',
  };
  return map[normalized.toLowerCase()] || normalized;
}

function milestoneIcon(state: WorkState): string {
  if (state === 'done') return '✓';
  if (state === 'active') return '●';
  if (state === 'attention') return '!';
  return '○';
}

export function Verify() {
  usePageTitle('Live Workspace');
  const [params, setParams] = useSearchParams();
  const project = params.get('project')?.trim() || '';
  const goal = params.get('goal')?.trim() || '';
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
  const runtimeTone = toneForStatus(runtimeStatus);
  const releaseTone = toneForStatus(releaseStatus);

  const findings = useMemo(
    () => ((record.defects || record.risks || []) as Finding[]).filter(isCustomerReadyFinding),
    [record.defects, record.risks],
  );
  const clues = asArray(record.clues);
  const visibleTargets = intelligence?.obligations.slice(0, 12) || [];
  const evidenceBackedFindings = findings.filter((finding) => (finding.evidence_chain?.length || 0) > 0).length;
  const targetCount = intelligence?.summary.obligationCount || 0;
  const runtimeNormalized = runtimeStatus.toLowerCase();
  const runtimeActive = runtimeNormalized === 'running';
  const runtimeFailed = ['failed', 'fail', 'blocked', 'error', 'failed_safe'].includes(runtimeNormalized);
  const hasRuntimeReceipt = Boolean(runtimeStatus || latestRunAt);

  const milestones: AgentMilestone[] = [
    {
      key: 'understanding',
      label: 'Understanding',
      detail: intelligenceLoading
        ? '正在读取已有企业理解与验证目标'
        : intelligenceError
          ? '验证目标读取失败'
          : intelligence
            ? '已读取持久化 Test Intelligence'
            : '尚未形成可读取的验证上下文',
      state: intelligenceLoading ? 'active' : intelligenceError ? 'attention' : intelligence ? 'done' : 'pending',
    },
    {
      key: 'planning',
      label: 'Planning',
      detail: targetCount > 0 ? `${targetCount} 个 Test Targets 可供验证` : '当前没有已上报 Test Targets',
      state: targetCount > 0 ? 'done' : 'pending',
    },
    {
      key: 'acting',
      label: 'Acting',
      detail: runtimeActive
        ? '后端报告真实运行正在进行'
        : runtimeFailed
          ? `真实运行状态：${statusLabel(runtimeStatus)}`
          : hasRuntimeReceipt
            ? `最近运行：${statusLabel(runtimeStatus)}`
            : '尚无真实运行回执',
      state: runtimeActive ? 'active' : runtimeFailed ? 'attention' : hasRuntimeReceipt ? 'done' : 'pending',
    },
    {
      key: 'observing',
      label: 'Observing',
      detail: evidenceBackedFindings > 0
        ? `${evidenceBackedFindings} 个 Finding 已携带证据链`
        : '逐步骤 Observation / Live Surface 尚未由统一事件流上报',
      state: evidenceBackedFindings > 0 ? 'done' : 'pending',
    },
    {
      key: 'evaluating',
      label: 'Evaluating',
      detail: releaseStatus ? `Release Gate：${statusLabel(releaseStatus)}` : 'Release Gate 尚未形成明确回执',
      state: releaseStatus ? (releaseTone === 'danger' ? 'attention' : 'done') : 'pending',
    },
    {
      key: 'finding',
      label: 'Finding',
      detail: findings.length > 0 ? `${findings.length} 个问题已满足客户可交付证据边界` : '当前没有已确认 Finding；这不是安全结论',
      state: findings.length > 0 ? 'attention' : 'pending',
    },
  ];

  if (!project) {
    return (
      <StatePanel
        eyebrow="Live Workspace"
        title="选择项目后，让 QualiBug 开始工作"
        description="Knowledge 可以只基于企业资料工作；Live Workspace 只展示真实 Test Targets、真实运行回执、真实 Finding 和真实 Release Gate。"
      />
    );
  }

  if (activeView === 'run-control') {
    return (
      <div className="verify-workspace">
        <header className="verify-run-control-head">
          <div>
            <span className="panel-kicker">Live Workspace · Run Control</span>
            <h1>真实运行控制</h1>
            <p>继续复用现有 Preflight、服务配置、测试数据与扫描主链，不创建第二套 Agent 执行系统。</p>
          </div>
          <button type="button" className="btn btn-secondary" onClick={() => selectView('workspace')}>返回 Live Workspace</button>
        </header>
        <EnterpriseCampaigns />
      </div>
    );
  }

  if (loading && !data) {
    return (
      <div className="verify-workspace">
        <div className="verify-live-shell">
          <Skeleton h={560} br={18} />
          <Skeleton h={560} br={18} />
        </div>
      </div>
    );
  }

  if (error && !data) {
    return <StatePanel eyebrow="Live Workspace · 连接状态" title="无法读取当前项目运行结果" description={error} action={<button className="btn btn-primary" onClick={refetch}>重新连接</button>} />;
  }

  return (
    <div className="verify-workspace">
      <section className="verify-live-shell">
        <aside className="verify-agent-rail" aria-label="Agent 工作状态">
          <div className="verify-context-card">
            <span>Task context</span>
            <strong>{goal || '验证当前项目的真实质量状态'}</strong>
            <p>任务文本不会绕过资料 authority、Preflight 或执行安全边界。</p>
          </div>

          <div className="verify-agent-message">
            <div className="verify-agent-avatar">Q</div>
            <div>
              <strong>QualiBug</strong>
              <p>
                我会先消费已有企业理解与 Test Targets，再读取真实运行和证据。缺少 Runtime Grounding 或实时事件时会明确告诉你，而不是补造步骤。
              </p>
            </div>
          </div>

          <div className="verify-milestones">
            {milestones.map((milestone) => (
              <div key={milestone.key} className={`verify-milestone state-${milestone.state}`}>
                <span className="verify-milestone-icon">{milestoneIcon(milestone.state)}</span>
                <div><strong>{milestone.label}</strong><p>{milestone.detail}</p></div>
              </div>
            ))}
          </div>

          <div className="verify-agent-rail-actions">
            <Link className="btn btn-secondary" to={buildProjectPath('/analyze', project)}>查看 Knowledge</Link>
            <button type="button" className="btn btn-primary" onClick={() => selectView('run-control')}>运行控制</button>
          </div>
        </aside>

        <main className="verify-live-main">
          <header className="verify-goal-bar">
            <div>
              <span>Goal</span>
              <h1>{goal || '验证当前系统并形成证据化质量判断'}</h1>
            </div>
            <div className="verify-goal-status">
              <span className={`verify-live-dot tone-${runtimeTone}`} />
              {statusLabel(runtimeStatus)}
            </div>
          </header>

          <div className="verify-context-chips" aria-label="当前真实上下文">
            <span>Test Targets · {intelligenceLoading ? '…' : intelligence ? targetCount : '未上报'}</span>
            <span>Findings · {findings.length}</span>
            <span>Evidence-backed · {evidenceBackedFindings}</span>
            <span>Background clues · {clues.length}</span>
            <span>Decision · {statusLabel(releaseStatus)}</span>
          </div>

          <section className="verify-work-grid">
            <article className="verify-plan-panel">
              <div className="verify-panel-title">
                <div><span>Plan</span><strong>需要验证什么</strong></div>
                <Link to={buildProjectPath('/analyze', project, 'view=test-targets')}>查看全部</Link>
              </div>

              {intelligenceLoading ? (
                <div className="verify-empty"><span className="spinner" /><p>正在读取 Test Targets…</p></div>
              ) : intelligenceError ? (
                <div className="verify-empty danger"><strong>Test Targets 读取失败</strong><p>{intelligenceError}</p><button type="button" className="btn btn-secondary btn-sm" onClick={() => void loadIntelligence()}>重试</button></div>
              ) : visibleTargets.length > 0 ? (
                <div className="verify-plan-list">
                  {visibleTargets.map((target, index) => (
                    <div className="verify-plan-row" key={target.obligationId}>
                      <span>{String(index + 1).padStart(2, '0')}</span>
                      <div><strong>{target.title}</strong><p>{target.objective}</p></div>
                      <small>{target.runtimeLinkage || 'NOT_GROUNDED'}</small>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="verify-empty"><strong>还没有可展示的 Test Targets</strong><p>先让 Knowledge 从企业资料形成有来源依据的验证目标。</p></div>
              )}
            </article>

            <article className="verify-execution-panel">
              <div className="verify-panel-title">
                <div><span>Live execution</span><strong>真实运行与证据 Surface</strong></div>
                <button type="button" onClick={() => selectView('run-control')}>打开 Run Control</button>
              </div>

              <div className="verify-execution-status">
                <div>
                  <span>Runtime</span>
                  <strong className={`tone-${runtimeTone}`}>{statusLabel(runtimeStatus)}</strong>
                  <p>{latestRunAt ? `最近回执：${latestRunAt}` : '后端未上报最近运行时间'}</p>
                </div>
                <div>
                  <span>Release Gate</span>
                  <strong className={`tone-${releaseTone}`}>{statusLabel(releaseStatus)}</strong>
                  <p>没有明确 Gate 回执时不会显示“可以发布”。</p>
                </div>
              </div>

              <div className="verify-live-surface">
                <div className="verify-live-surface-tabs"><b>Execution Surface</b><span>Browser</span><span>API</span><span>Network</span><span>DB</span><span>Trace</span></div>
                <div className="verify-live-surface-body">
                  <div className="verify-surface-pulse" aria-hidden="true"><span /></div>
                  <strong>统一逐步骤 Agent Run / Live Surface 尚未上报</strong>
                  <p>
                    当前工作台不会用静态示意图冒充 Browser Live View，也不会把推测的 API 请求当作真实执行。
                    Runtime Grounding 接通后，这里将直接承载真实 Action → Observation → Oracle → Evidence 事件。
                  </p>
                </div>
              </div>
            </article>

            <aside className="verify-activity-panel">
              <div className="verify-panel-title"><div><span>Activity</span><strong>已上报事实</strong></div></div>
              {findings.length > 0 ? (
                <div className="verify-activity-list">
                  {findings.slice(0, 6).map((finding) => (
                    <Link key={finding.id} to={buildProjectPath(`/findings/${finding.id}`, project)}>
                      <span className={`severity-badge ${finding.severity.toLowerCase()}`}>{finding.severity}</span>
                      <strong>{finding.title}</strong>
                      <p>{finding.business_summary || finding.business_impact?.summary || '查看真实复现与证据'}</p>
                      <small>{finding.evidence_chain?.length || 0} 条证据</small>
                    </Link>
                  ))}
                </div>
              ) : (
                <div className="verify-empty"><strong>当前没有已确认 Finding</strong><p>Activity 只展示真实上报事件，不用模拟日志制造 Agent “忙碌感”。</p></div>
              )}
            </aside>
          </section>

          <footer className="verify-evidence-dock">
            <span>Evidence</span>
            <b>{evidenceBackedFindings} 个证据化 Finding</b>
            <Link to={buildProjectPath('/findings', project)}>Inspect findings</Link>
            <Link to={buildProjectPath('/release', project)}>Open decision</Link>
          </footer>
        </main>
      </section>
    </div>
  );
}

export default Verify;
