import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import {
  cancelAgentTask,
  getAgentTaskBundle,
  type AgentTask,
  type AgentTaskEvent,
} from '../api/agent-tasks';
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

const TERMINAL_TASK_STATUSES = new Set(['COMPLETED', 'FAILED', 'CANCELLED']);

function toneForStatus(value: string): 'success' | 'warning' | 'danger' | 'neutral' {
  const status = value.toLowerCase();
  if (['passed', 'pass', 'completed', 'executed', 'ready'].includes(status)) return 'success';
  if (['failed', 'fail', 'blocked', 'error', 'failed_safe', 'hold', 'cancelled'].includes(status)) return 'danger';
  if (['running', 'pending', 'partial', 'coverage_deferred', 'not_ready', 'created', 'planning', 'understanding', 'evaluating'].includes(status)) return 'warning';
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
    created: '任务已创建',
    understanding: '正在理解',
    planning: '正在规划',
    evaluating: '正在判断',
    cancelled: '任务已取消',
  };
  return map[normalized.toLowerCase()] || normalized;
}

function milestoneIcon(state: WorkState): string {
  if (state === 'done') return '✓';
  if (state === 'active') return '●';
  if (state === 'attention') return '!';
  return '○';
}

function eventLabel(eventType: string): string {
  const labels: Record<string, string> = {
    TASK_CREATED: 'Agent Task 已创建',
    TASK_CANCELLED: 'Agent Task 已取消',
    UNDERSTANDING_STARTED: '开始理解上下文',
    UNDERSTANDING_COMPLETED: '上下文理解完成',
    PLANNING_STARTED: '开始规划验证',
    PLAN_CREATED: '验证计划已形成',
    PREFLIGHT_STARTED: '开始运行前检查',
    PREFLIGHT_PASSED: '运行前检查通过',
    PREFLIGHT_BLOCKED: '运行前检查阻断',
    EXECUTION_STARTED: '真实执行开始',
    OBSERVATION_RECORDED: '收到真实 Observation',
    ORACLE_EVALUATED: 'Oracle 已判定',
    FINDING_CREATED: '已形成 Finding',
    DECISION_UPDATED: '发布判断已更新',
  };
  return labels[eventType] || eventType;
}

function eventDetail(event: AgentTaskEvent): string {
  const intent = asText(event.detail.intent);
  const previousStatus = asText(event.detail.previous_status);
  if (intent) return `Intent · ${intent}`;
  if (previousStatus) return `Previous status · ${previousStatus}`;
  return '后端事件账本已记录；没有附加可展示详情。';
}

export function Verify() {
  usePageTitle('Live Workspace');
  const [params, setParams] = useSearchParams();
  const project = params.get('project')?.trim() || '';
  const taskId = params.get('task')?.trim() || '';
  const goal = params.get('goal')?.trim() || '';
  const activeView: VerifyView = params.get('view') === 'run-control' ? 'run-control' : 'workspace';
  const { data, loading, error, refetch } = usePipelineData(project);
  const [intelligence, setIntelligence] = useState<TestIntelligenceAnalysis | null>(null);
  const [intelligenceLoading, setIntelligenceLoading] = useState(Boolean(project));
  const [intelligenceError, setIntelligenceError] = useState('');
  const [agentTask, setAgentTask] = useState<AgentTask | null>(null);
  const [agentEvents, setAgentEvents] = useState<AgentTaskEvent[]>([]);
  const [agentTaskLoading, setAgentTaskLoading] = useState(Boolean(project && taskId));
  const [agentTaskError, setAgentTaskError] = useState('');
  const [cancellingTask, setCancellingTask] = useState(false);

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

  const loadAgentTask = useCallback(async () => {
    if (!project || !taskId) {
      setAgentTask(null);
      setAgentEvents([]);
      setAgentTaskError('');
      setAgentTaskLoading(false);
      return;
    }
    setAgentTaskLoading(true);
    try {
      const bundle = await getAgentTaskBundle(project, taskId);
      setAgentTask(bundle.task);
      setAgentEvents(bundle.events);
      setAgentTaskError('');
    } catch (caught: unknown) {
      setAgentTask(null);
      setAgentEvents([]);
      setAgentTaskError(caught instanceof Error ? caught.message : 'Agent Task 读取失败');
    } finally {
      setAgentTaskLoading(false);
    }
  }, [project, taskId]);

  useEffect(() => {
    void loadIntelligence();
  }, [loadIntelligence]);

  useEffect(() => {
    void loadAgentTask();
    if (!project || !taskId) return undefined;
    const timer = window.setInterval(() => {
      void loadAgentTask();
    }, 5_000);
    return () => window.clearInterval(timer);
  }, [project, taskId, loadAgentTask]);

  const cancelCurrentTask = async () => {
    if (!project || !taskId || !agentTask || cancellingTask || TERMINAL_TASK_STATUSES.has(agentTask.status)) return;
    setCancellingTask(true);
    try {
      await cancelAgentTask(project, taskId);
      await loadAgentTask();
    } catch (caught: unknown) {
      setAgentTaskError(caught instanceof Error ? caught.message : 'Agent Task 取消失败');
    } finally {
      setCancellingTask(false);
    }
  };

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
  const taskGoal = agentTask?.goal || goal || '验证当前系统并形成证据化质量判断';
  const taskTone = toneForStatus(agentTask?.status || '');
  const taskIsTerminal = Boolean(agentTask && TERMINAL_TASK_STATUSES.has(agentTask.status));

  const milestones: AgentMilestone[] = [
    {
      key: 'task',
      label: 'Task',
      detail: agentTaskLoading
        ? '正在读取 Agent Task'
        : agentTaskError
          ? 'Agent Task 读取失败'
          : agentTask
            ? `后端任务 ${agentTask.taskId} · ${statusLabel(agentTask.status)}`
            : '当前工作区没有绑定 Agent Task',
      state: agentTaskLoading ? 'active' : agentTaskError ? 'attention' : agentTask ? (taskIsTerminal && agentTask.status !== 'COMPLETED' ? 'attention' : 'done') : 'pending',
    },
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
        : '逐步骤 Observation / Live Surface 尚未由真实执行事件上报',
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
        description="Knowledge 可以只基于企业资料工作；Live Workspace 只展示真实 Agent Task、真实 Test Targets、真实运行回执、真实 Finding 和真实 Release Gate。"
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
            <p>继续复用现有 Preflight、服务配置、测试数据与扫描主链。Agent Task 只负责持久化目标和编排状态，不创建第二套执行系统。</p>
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
            <span>Agent Task</span>
            <strong>{taskGoal}</strong>
            <p>
              {agentTask
                ? `${agentTask.taskId} · ${statusLabel(agentTask.status)} · Runtime Grounding ${agentTask.runtimeGroundingStatus || '未上报'}`
                : taskId
                  ? '正在读取后端 Agent Task。'
                  : '当前未绑定 Agent Task；可从 New Task 创建。'}
            </p>
          </div>

          <div className="verify-agent-message">
            <div className="verify-agent-avatar">Q</div>
            <div>
              <strong>QualiBug</strong>
              <p>
                Agent Task 已成为后端持久化对象。Event Ledger 只记录后端真正发生的工作事件；
                Runtime Grounding、Preflight 和 Scan 尚未绑定到该 Task 时，不会补造 Planning / Acting 日志。
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
            <Link className="btn btn-secondary" to={buildProjectPath('/analyze', project, taskId ? `task=${encodeURIComponent(taskId)}&goal=${encodeURIComponent(taskGoal)}` : '')}>查看 Knowledge</Link>
            {agentTask && !taskIsTerminal ? (
              <button type="button" className="btn btn-secondary" onClick={() => void cancelCurrentTask()} disabled={cancellingTask}>
                {cancellingTask ? '正在取消…' : '取消 Agent Task'}
              </button>
            ) : null}
            <button type="button" className="btn btn-primary" onClick={() => selectView('run-control')}>运行控制</button>
          </div>
          {agentTaskError && <div className="settings-inline-feedback" role="alert">{agentTaskError}</div>}
        </aside>

        <main className="verify-live-main">
          <header className="verify-goal-bar">
            <div>
              <span>Goal</span>
              <h1>{taskGoal}</h1>
            </div>
            <div className="verify-goal-status">
              <span className={`verify-live-dot tone-${agentTask ? taskTone : runtimeTone}`} />
              {agentTask ? `Task · ${statusLabel(agentTask.status)}` : statusLabel(runtimeStatus)}
            </div>
          </header>

          <div className="verify-context-chips" aria-label="当前真实上下文">
            <span>Agent Task · {agentTaskLoading ? '…' : agentTask ? agentTask.status : '未绑定'}</span>
            <span>Task Events · {agentTask ? agentEvents.length : '未绑定'}</span>
            <span>Test Targets · {intelligenceLoading ? '…' : intelligence ? targetCount : '未上报'}</span>
            <span>Findings · {findings.length}</span>
            <span>Evidence-backed · {evidenceBackedFindings}</span>
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
                  <strong>Agent Task 已接通，真实 Execution Event Stream 尚未绑定</strong>
                  <p>
                    当前已经能看到真实 Task Event Ledger，但 `execution_run_id` 和 Runtime Grounding 仍未接入 Agent Task。
                    因此这里继续不冒充 Browser Live View，也不会把现有 Campaign 状态改写成某个 Task 的执行事件。
                  </p>
                </div>
              </div>
            </article>

            <aside className="verify-activity-panel">
              <div className="verify-panel-title"><div><span>Activity</span><strong>Agent Event Ledger</strong></div></div>
              {taskId ? (
                agentTaskLoading && !agentTask ? (
                  <div className="verify-empty"><span className="spinner" /><p>正在读取 Task Events…</p></div>
                ) : agentTaskError && !agentTask ? (
                  <div className="verify-empty danger"><strong>Agent Task 不可读取</strong><p>{agentTaskError}</p></div>
                ) : agentEvents.length > 0 ? (
                  <div className="verify-event-list">
                    {agentEvents.slice().reverse().map((event) => (
                      <div className="verify-event-row" key={event.eventId}>
                        <span>{event.eventType}</span>
                        <strong>{eventLabel(event.eventType)}</strong>
                        <p>{eventDetail(event)}</p>
                        <small>{event.occurredAt || '时间未上报'}</small>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="verify-empty"><strong>Task 尚无事件</strong><p>前端不会用模拟日志填充 Event Ledger。</p></div>
                )
              ) : findings.length > 0 ? (
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
                <div className="verify-empty"><strong>当前没有 Agent Task Event</strong><p>从 New Task 创建任务后，这里才会显示后端真实事件；不会模拟 Agent “忙碌感”。</p></div>
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
