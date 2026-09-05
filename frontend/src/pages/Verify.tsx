import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import {
  cancelAgentTask,
  executeAgentTask,
  getAgentTaskBundle,
  groundAgentTask,
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
type WorkspaceSection = 'overview' | 'deliverables' | 'execution' | 'activity';
const WORKSPACE_SECTIONS: { id: WorkspaceSection; label: string }[] = [
  { id: 'overview', label: '任务概览' },
  { id: 'deliverables', label: '交付物' },
  { id: 'execution', label: '执行与条件' },
  { id: 'activity', label: '工作记录' },
];

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
  if (['passed', 'pass', 'completed', 'executed', 'ready', 'pinned'].includes(status)) return 'success';
  if (['failed', 'fail', 'blocked', 'error', 'failed_safe', 'hold', 'cancelled', 'pinned_stale'].includes(status)) return 'danger';
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
    ready: '已就绪',
    created: '任务已创建',
    understanding: '正在理解',
    planning: '正在规划',
    evaluating: '正在判断',
    cancelled: '任务已取消',
    pinned: '已固定',
    pinned_stale: '已固定但资料有更新',
    not_pinned: '未固定',
    not_requested: '未评估',
    not_required: '无需运行绑定',
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
    UNDERSTANDING_SNAPSHOT_PINNED: '已固定企业理解快照',
    UNDERSTANDING_SNAPSHOT_UNAVAILABLE: '没有可固定的理解快照',
    TEST_TARGET_SELECTION_EVALUATED: '已评估 Test Target 选择',
    ANALYSIS_CONTEXT_EVALUATED: '已评估分析上下文',
    RUNTIME_GROUNDING_EVALUATED: '已评估 Runtime Grounding',
    EXECUTION_CLAIMED: '已认领项目执行请求',
    SCAN_ID_RECORDED: '已记录 Scan 身份',
    EXECUTION_STARTED: 'Scan 主链已启动',
    EXECUTION_CANCEL_REQUESTED: '已请求在实验边界停止',
    EXECUTION_RESULT_RECORDED: '已记录 Scan 执行结果',
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
  const status = asText(event.detail.status);
  const scanId = asText(event.detail.scan_id);
  const executionStatus = asText(event.detail.execution_status);
  if (scanId) return `Scan · ${scanId}${executionStatus ? ` · ${executionStatus}` : ''}`;
  const blockingCodes = asArray(event.detail.blocking_codes).map(asText).filter(Boolean);
  const selectedCount = Number(event.detail.selected_target_count || 0);
  const runtimeBoundCount = Number(event.detail.runtime_bound_target_count || 0);
  const snapshotRef = asText(event.detail.snapshot_ref);
  if (blockingCodes.length) return `Blockers · ${blockingCodes.join(' · ')}`;
  if (snapshotRef) return `Snapshot · ${snapshotRef}`;
  if (selectedCount || runtimeBoundCount) return `Targets · ${selectedCount} / runtime-bound ${runtimeBoundCount}`;
  if (status) return `Status · ${status}`;
  if (intent) return `Intent · ${intent}`;
  if (previousStatus) return `Previous status · ${previousStatus}`;
  return '后端事件账本已记录；没有附加可展示详情。';
}

function hasEvent(events: AgentTaskEvent[], eventType: string): boolean {
  return events.some((event) => event.eventType === eventType);
}

export function Verify() {
  const [params] = useSearchParams();
  return <VerifyWorkspace key={`${params.get('project') || ''}:${params.get('task') || ''}`} />;
}

function VerifyWorkspace() {
  usePageTitle('Live Workspace');
  const [params, setParams] = useSearchParams();
  const project = params.get('project')?.trim() || '';
  const taskId = params.get('task')?.trim() || '';
  const goal = params.get('goal')?.trim() || '';
  const activeView: VerifyView = params.get('view') === 'run-control' ? 'run-control' : 'workspace';
  const activeSection = WORKSPACE_SECTIONS.find((item) => item.id === params.get('section'))?.id || 'overview';
  const selectSection = (section: WorkspaceSection) => {
    const next = new URLSearchParams(params);
    next.set('section', section);
    setParams(next);
  };
  const artifactSearch = (view: string) => {
    const next = new URLSearchParams();
    next.set('view', view);
    if (taskId) next.set('task', taskId);
    return next.toString();
  };
  const { data, loading, error, refetch } = usePipelineData(project);
  const [intelligence, setIntelligence] = useState<TestIntelligenceAnalysis | null>(null);
  const [intelligenceLoading, setIntelligenceLoading] = useState(Boolean(project && !taskId));
  const [intelligenceError, setIntelligenceError] = useState('');
  const [agentTask, setAgentTask] = useState<AgentTask | null>(null);
  const [agentEvents, setAgentEvents] = useState<AgentTaskEvent[]>([]);
  const [agentTaskLoading, setAgentTaskLoading] = useState(Boolean(project && taskId));
  const [agentTaskError, setAgentTaskError] = useState('');
  const [cancellingTask, setCancellingTask] = useState(false);
  const [groundingTask, setGroundingTask] = useState(false);
  const [executingTask, setExecutingTask] = useState(false);
  const [executionError, setExecutionError] = useState('');
  const [readOnly, setReadOnly] = useState(false);

  const selectView = (view: VerifyView) => {
    const next = new URLSearchParams(params);
    next.set('view', view);
    setParams(next, { replace: true });
  };

  const loadIntelligence = useCallback(async () => {
    if (!project || taskId) {
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
  }, [project, taskId]);

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

  const regroundCurrentTask = async () => {
    if (!project || !taskId || !agentTask || groundingTask || TERMINAL_TASK_STATUSES.has(agentTask.status)) return;
    setGroundingTask(true);
    try {
      await groundAgentTask(project, taskId, { testTargetIds: agentTask.selectedTestTargets });
      await loadAgentTask();
    } catch (caught: unknown) {
      setAgentTaskError(caught instanceof Error ? caught.message : 'Runtime Grounding 重新评估失败');
    } finally {
      setGroundingTask(false);
    }
  };

  const executeCurrentTask = async () => {
    if (!project || !taskId || executingTask) return;
    setExecutingTask(true);
    setExecutionError('');
    try {
      await executeAgentTask(project, taskId, readOnly);
    } catch (caught: unknown) {
      setExecutionError(caught instanceof Error ? caught.message : '执行请求未完成；请查看任务记录，不要重复创建任务。');
    } finally {
      await loadAgentTask();
      setExecutingTask(false);
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
  const evidenceBackedFindings = findings.filter((finding) => (finding.evidence_chain?.length || 0) > 0).length;
  const targetCount = intelligence?.summary.obligationCount || 0;
  const runtimeNormalized = runtimeStatus.toLowerCase();
  const runtimeActive = runtimeNormalized === 'running';
  const runtimeFailed = ['failed', 'fail', 'blocked', 'error', 'failed_safe'].includes(runtimeNormalized);
  const hasRuntimeReceipt = Boolean(runtimeStatus || latestRunAt);
  const taskGoal = agentTask?.goal || (taskId ? (agentTaskError ? '任务目标暂不可用' : '正在读取任务目标…') : goal || '选择要继续的任务');
  const taskTone = toneForStatus(agentTask?.status || '');
  const taskIsTerminal = Boolean(agentTask && TERMINAL_TASK_STATUSES.has(agentTask.status));
  const taskClaimed = Boolean(agentTask?.executionClaimStatus && agentTask.executionClaimStatus !== 'NOT_CLAIMED');
  const executionUncertain = Boolean(agentTask?.executionRecoveryRequired);
  const canExecute = Boolean(agentTask && !taskIsTerminal && !taskClaimed && agentTask.intent !== 'analyze_requirements');
  const requiresExplicitScope = Boolean(agentTask?.groundingBlockers.some((blocker) => blocker.code === 'CHANGE_SCOPE_NOT_GROUNDED'));
  const taskKnowledgeSearch = taskId
    ? `${requiresExplicitScope ? 'view=test-targets&' : ''}task=${encodeURIComponent(taskId)}${agentTask ? `&goal=${encodeURIComponent(agentTask.goal)}` : ''}`
    : 'view=test-targets';
  const pinnedTargets = agentTask?.selectedTargetSnapshots.slice(0, 12) || [];
  const fallbackTargets = intelligence?.obligations.slice(0, 12) || [];
  const taskHasObservation = hasEvent(agentEvents, 'OBSERVATION_RECORDED');
  const taskHasFinding = hasEvent(agentEvents, 'FINDING_CREATED');
  const taskHasDecision = hasEvent(agentEvents, 'DECISION_UPDATED');

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
      detail: agentTask
        ? `Understanding Snapshot · ${statusLabel(agentTask.sourceSnapshotStatus)}${agentTask.sourceRevisionState ? ` · ${agentTask.sourceRevisionState}` : ''}`
        : intelligenceLoading
          ? '正在读取已有企业理解与验证目标'
          : intelligenceError
            ? '验证目标读取失败'
            : intelligence
              ? '已读取持久化 Test Intelligence；当前未绑定 Agent Task Snapshot'
              : '尚未形成可读取的验证上下文',
      state: agentTask
        ? agentTask.sourceSnapshotStatus === 'PINNED' ? 'done' : 'attention'
        : intelligenceLoading ? 'active' : intelligenceError ? 'attention' : intelligence ? 'done' : 'pending',
    },
    {
      key: 'planning',
      label: 'Planning',
      detail: agentTask
        ? agentTask.intent === 'analyze_requirements'
          ? '分析型任务不伪造执行目标选择'
          : `${agentTask.groundingSummary.selectedTargetCount} 个 Test Targets 已固定到该任务`
        : targetCount > 0 ? `${targetCount} 个当前 Test Targets 可供后续规划` : '当前没有已上报 Test Targets',
      state: agentTask
        ? agentTask.intent === 'analyze_requirements' ? 'done' : agentTask.groundingSummary.selectedTargetCount > 0 ? 'done' : 'attention'
        : targetCount > 0 ? 'done' : 'pending',
    },
    {
      key: 'grounding',
      label: 'Grounding',
      detail: agentTask
        ? `Runtime Grounding · ${statusLabel(agentTask.runtimeGroundingStatus)} · Preflight ${agentTask.groundingSummary.preflightReady ? 'READY' : 'NOT READY'}`
        : '当前没有 Task-specific Runtime Grounding',
      state: agentTask
        ? agentTask.runtimeGroundingStatus === 'READY' || agentTask.runtimeGroundingStatus === 'NOT_REQUIRED'
          ? 'done'
          : agentTask.runtimeGroundingStatus === 'BLOCKED' ? 'attention' : 'pending'
        : 'pending',
    },
    {
      key: 'acting',
      label: 'Acting',
      detail: agentTask
        ? agentTask.executionRunId
          ? `已绑定真实 execution_run_id · ${agentTask.executionRunId}`
          : '尚未绑定 execution_run_id；现有 Campaign 状态不会冒充 Task 执行事件'
        : runtimeActive
          ? '项目级真实运行正在进行'
          : runtimeFailed
            ? `项目级运行状态：${statusLabel(runtimeStatus)}`
            : hasRuntimeReceipt ? `最近项目运行：${statusLabel(runtimeStatus)}` : '尚无真实运行回执',
      state: agentTask
        ? agentTask.executionRunId ? (agentTask.status === 'RUNNING' ? 'active' : 'done') : 'pending'
        : runtimeActive ? 'active' : runtimeFailed ? 'attention' : hasRuntimeReceipt ? 'done' : 'pending',
    },
    {
      key: 'observing',
      label: 'Observing',
      detail: agentTask
        ? taskHasObservation ? 'Task Event Ledger 已收到真实 Observation' : 'Task 尚无真实 Observation Event'
        : evidenceBackedFindings > 0 ? `${evidenceBackedFindings} 个项目 Finding 已携带证据链` : '尚无统一 Observation Event',
      state: agentTask ? (taskHasObservation ? 'done' : 'pending') : evidenceBackedFindings > 0 ? 'done' : 'pending',
    },
    {
      key: 'evaluating',
      label: 'Evaluating',
      detail: agentTask
        ? taskHasDecision ? 'Task Event Ledger 已形成 Decision 更新' : '尚无 Task-specific Decision Event'
        : releaseStatus ? `项目 Release Gate：${statusLabel(releaseStatus)}` : 'Release Gate 尚未形成明确回执',
      state: agentTask ? (taskHasDecision ? 'done' : 'pending') : releaseStatus ? (releaseTone === 'danger' ? 'attention' : 'done') : 'pending',
    },
    {
      key: 'finding',
      label: 'Finding',
      detail: agentTask
        ? taskHasFinding ? 'Task Event Ledger 已记录 Finding 创建' : '尚无 Task-specific Finding Event'
        : findings.length > 0 ? `${findings.length} 个项目问题已满足客户可交付证据边界` : '当前没有已确认 Finding；这不是安全结论',
      state: agentTask ? (taskHasFinding ? 'attention' : 'pending') : findings.length > 0 ? 'attention' : 'pending',
    },
  ];

  if (!project) {
    return (
      <StatePanel
        eyebrow="Live Workspace"
        title="选择项目后，让 QualiBug 开始工作"
        description="Knowledge 可以只基于企业资料工作；Live Workspace 只展示真实 Agent Task、固定 Snapshot、真实 Grounding、真实运行回执、真实 Finding 和真实 Release Gate。"
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
            <p>继续复用现有 Preflight、服务配置、测试数据与扫描主链。Agent Task Grounding 只评估是否具备运行条件，不创建第二套执行系统。</p>
          </div>
          <button type="button" className="btn btn-secondary" onClick={() => selectView('workspace')}>返回 Live Workspace</button>
        </header>
        <EnterpriseCampaigns />
      </div>
    );
  }

  if (!taskId && loading && !data) {
    return (
      <div className="verify-workspace">
        <div className="verify-live-shell">
          <Skeleton h={560} br={18} />
          <Skeleton h={560} br={18} />
        </div>
      </div>
    );
  }

  if (!taskId && error && !data) {
    return (
      <StatePanel
        eyebrow="Live Workspace · 连接状态"
        title="无法读取当前项目运行结果"
        description={`${error}。项目结果不可用时，任务记录和企业 Knowledge 仍可单独查看。`}
        action={(
          <>
            <button className="btn btn-primary" onClick={refetch}>重新连接</button>
            <Link className="btn btn-secondary" to={buildProjectPath('/dashboard', project)}>查看任务记录</Link>
            <Link className="btn btn-secondary" to={buildProjectPath('/analyze', project)}>查看 Knowledge</Link>
          </>
        )}
      />
    );
  }

  return (
    <div className="verify-workspace">
      <Link className="verify-back-link" to={buildProjectPath('/dashboard', project)}>← 所有任务</Link>
      {error && <div className="agent-state-error" role="alert"><strong>项目结果暂不可用</strong><details><summary>查看错误详情</summary><p>{error}</p></details><button className="btn btn-secondary btn-sm" onClick={refetch}>重试项目结果</button></div>}
      <section className="verify-live-shell">
        <details className="verify-agent-rail"><summary>任务管理与技术详情</summary>
          <div className="verify-context-card">
            <span>Agent Task</span>
            <strong>{taskGoal}</strong>
            <p>
              {agentTask
                ? `${agentTask.taskId} · ${statusLabel(agentTask.status)} · Snapshot ${statusLabel(agentTask.sourceSnapshotStatus)} · Grounding ${statusLabel(agentTask.runtimeGroundingStatus)}`
                : taskId
                  ? '正在读取后端 Agent Task。'
                  : '当前未绑定 Agent Task；可从 New Task 创建。'}
            </p>
          </div>

          <details className="verify-agent-message"><summary>工作方式与边界</summary>
            <div className="verify-agent-avatar">Q</div>
            <div>
              <strong>QualiBug</strong>
              <p>
                我只消费已经持久化的企业理解快照，并复用真实 Scan Preflight。资料状态与执行条件分别展示；知识缺口不自动禁止受控实验，实际执行仍由项目安全检查决定。
              </p>
            </div>
          </details>

          <details className="verify-milestone-details"><summary>查看任务阶段</summary><div className="verify-milestones">
            {milestones.map((milestone) => (
              <div key={milestone.key} className={`verify-milestone state-${milestone.state}`}>
                <span className="verify-milestone-icon">{milestoneIcon(milestone.state)}</span>
                <div><strong>{milestone.label}</strong><p>{milestone.detail}</p></div>
              </div>
            ))}
          </div></details>

          <div className="verify-agent-rail-actions">
            <Link className="btn btn-secondary" to={buildProjectPath('/analyze', project, taskKnowledgeSearch)}>查看 Knowledge</Link>
            {agentTask && !taskIsTerminal && !taskClaimed ? (
              <button type="button" className="btn btn-secondary" onClick={() => void regroundCurrentTask()} disabled={groundingTask}>
                {groundingTask ? '正在评估…' : '重新评估 Grounding'}
              </button>
            ) : null}
            {agentTask && !taskIsTerminal ? (
              <button type="button" className="btn btn-secondary" onClick={() => void cancelCurrentTask()} disabled={cancellingTask || executionUncertain || agentTask.executionCancelRequested}>
                {cancellingTask ? '正在提交…' : agentTask.executionCancelRequested ? '已请求安全停止' : taskClaimed ? '请求安全停止本次运行' : '取消 Agent Task'}
              </button>
            ) : null}
            <button type="button" className="btn btn-secondary" onClick={() => selectView('run-control')}>项目运行控制</button>
          </div>
          {agentTaskError && <div className="settings-inline-feedback" role="alert">{agentTaskError}</div>}
        </details>

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

          <nav className="verify-section-nav" aria-label="任务工作区">
            {WORKSPACE_SECTIONS.map((section) => <button key={section.id} type="button" aria-current={activeSection === section.id ? 'page' : undefined} onClick={() => selectSection(section.id)}>{section.label}</button>)}
          </nav>
          {(activeSection === 'overview' || activeSection === 'execution') && <section className="verify-next-step" aria-label="任务下一步">
            <span className="panel-kicker">下一步</span>
            <h2>{executionUncertain ? '执行结果待确认' : agentTaskLoading && !agentTask ? '正在读取任务' : !agentTask ? '任务暂不可用' : taskIsTerminal ? '回看这次工作的记录' : agentTask.executionRunId ? '跟踪真实执行进展' : agentTask.groundingBlockers.length ? '补充实验需要的条件' : agentTask.intent === 'analyze_requirements' ? '检查已固定的企业知识' : '检查范围，准备实验'}</h2>
            <p>{executionUncertain ? '原执行进程已不再持有该任务的运行锁，无法确认它是否发送过请求。系统不会自动重跑；请根据 Scan 身份检查已有证据。' : agentTask?.groundingBlockers[0]?.message || (agentTask?.executionRunId ? `已绑定运行：${agentTask.executionRunId}` : agentTask ? '查看已有知识、验证目标和事件，再决定下一步。项目运行控制仍负责实际测试；点击按项目范围执行后，系统会重新检查环境并启动原有 Scan 主链。' : agentTaskError || '等待后端任务记录。')}</p>
            <div className="verify-next-actions">
              {canExecute && activeSection === 'overview' && <button type="button" className="btn btn-primary" onClick={() => selectSection('execution')}>查看执行条件与范围</button>}
              {agentTask && <Link className="btn btn-secondary" to={buildProjectPath('/analyze', project, taskKnowledgeSearch)}>查看任务知识</Link>}
              {requiresExplicitScope && <Link className="btn btn-primary" to={buildProjectPath('/analyze', project, taskKnowledgeSearch)}>选择关注的验证目标</Link>}
              {agentTask && !taskIsTerminal && !taskClaimed && <button className="btn btn-secondary" onClick={() => void regroundCurrentTask()} disabled={groundingTask}>{groundingTask ? '正在检查…' : '重新检查条件'}</button>}
              {agentTaskError && <button className="btn btn-secondary" onClick={() => void loadAgentTask()}>重新读取任务</button>}
            </div>
            {canExecute && activeSection === 'execution' && <div className="verify-task-execute">
              <p>执行范围：当前项目。任务中的验证目标用于理解上下文，不代表只运行这些目标，也不代表已证明变更覆盖。每次实验仍由既有执行与安全规则决定。</p>
              <label><input type="checkbox" checked={readOnly} onChange={(event) => setReadOnly(event.target.checked)} disabled={executingTask} /> 仅执行只读实验</label>
              <button type="button" className="btn btn-primary" disabled={executingTask || groundingTask} onClick={() => void executeCurrentTask()}>{executingTask ? '正在提交执行请求…' : '按项目范围执行'}</button>
            </div>}
            {executionError && <div role="alert" className="settings-inline-feedback">{executionError}</div>}
            {agentTask?.executionSnapshotRef && <p>本次执行的知识版本：{agentTask.executionSnapshotRef}</p>}
            {Boolean(agentTask?.executionResult.error) && <p role="alert">执行诊断：{asText(agentTask?.executionResult.error)}</p>}
          </section>}
          {activeSection === 'execution' && <details className="verify-context-details"><summary>快照、绑定与项目上下文</summary>
          <div className="verify-context-chips" aria-label="当前真实上下文">
            <span>Snapshot · {agentTask ? statusLabel(agentTask.sourceSnapshotStatus) : '未绑定'}</span>
            <span>Selected Targets · {agentTask ? agentTask.groundingSummary.selectedTargetCount : '未绑定'}</span>
            <span>Runtime-bound · {agentTask ? agentTask.groundingSummary.runtimeBoundTargetCount : '未绑定'}</span>
            <span>Preflight · {agentTask ? (agentTask.groundingSummary.preflightReady ? 'READY' : 'NOT READY') : '未绑定'}</span>
            <span>Grounding · {agentTask ? statusLabel(agentTask.runtimeGroundingStatus) : '未绑定'}</span>
            <span>Task Events · {agentTask ? agentEvents.length : '未绑定'}</span>
            <span>Project Findings · {error || !data ? '未上报' : findings.length}</span>
            <span>Background clues · {error || !data ? '未上报' : clues.length}</span>
          </div></details>}

          {(activeSection === 'overview' || activeSection === 'deliverables') && <section className="verify-deliverables" aria-label="任务交付物">
            <div><h2>{activeSection === 'overview' ? '查看工作成果' : '交付物工作区'}</h2><p>进入对应工作区查看内容与来源。企业知识为项目共享资产；项目历史结果不代表本任务已执行。</p></div>
            <div className="verify-artifact-grid">
              <Link to={buildProjectPath('/analyze', project, artifactSearch('requirements'))}><strong>需求审查</strong><p>查看已有资料中的冲突、缺失与待澄清问题。</p><span>打开审查结果 →</span></Link>
              <Link to={buildProjectPath('/analyze', project, artifactSearch('test-targets'))}><strong>测试设计与数据要求</strong><p>查看来源支持的场景、预期结果和数据约束；结构化设计不代表可执行计划或已准备的数据。</p><span>打开测试设计 →</span></Link>
              <button type="button" onClick={() => selectSection('activity')}><strong>本任务执行记录</strong><p>{agentTask?.executionRunId ? '已关联运行，查看后端记录与诊断。' : '尚未关联真实运行，暂无本任务执行结果。'}</p><span>查看工作记录 →</span></button>
              <Link to={buildProjectPath('/findings', project)}><strong>项目缺陷与证据</strong><p>查看项目已确认的问题和复现证据，具体归属以运行记录为准。</p><span>查看项目缺陷 →</span></Link>
            </div>
          </section>}
          <section className="verify-work-grid">
            {activeSection === 'deliverables' && <article className="verify-plan-panel">
              <div className="verify-panel-title">
                <div><span>Plan</span><strong>{agentTask ? '该 Task 固定的 Test Targets' : '当前可用 Test Targets'}</strong></div>
                <Link to={buildProjectPath('/analyze', project, taskKnowledgeSearch)}>查看 Knowledge</Link>
              </div>

              {agentTask ? (
                pinnedTargets.length > 0 ? (
                  <div className="verify-plan-list">
                    {pinnedTargets.map((target, index) => (
                      <div className="verify-plan-row" key={target.obligationId}>
                        <span>{String(index + 1).padStart(2, '0')}</span>
                        <div><strong>{target.title || target.obligationId}</strong><p>{target.objective || target.operationRef || '来源快照未提供更多展示信息'}</p></div>
                        <small>{target.executionSurface || 'NOT_SELECTED'} · {target.actionBindingStatus || 'NOT_GROUNDED'}</small>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="verify-empty">
                    <strong>{agentTask.intent === 'analyze_requirements' ? '分析任务不需要固定执行目标' : '该 Task 尚未固定可执行 Test Targets'}</strong>
                    <p>{agentTask.groundingBlockers[0]?.message || '查看 Runtime Grounding blocker 了解原因。'}</p>
                    {requiresExplicitScope && <Link className="btn btn-primary" to={buildProjectPath('/analyze', project, taskKnowledgeSearch)}>选择关注的验证目标</Link>}
                  </div>
                )
              ) : intelligenceLoading ? (
                <div className="verify-empty"><span className="spinner" /><p>正在读取 Test Targets…</p></div>
              ) : intelligenceError ? (
                <div className="verify-empty danger"><strong>Test Targets 读取失败</strong><p>{intelligenceError}</p><button type="button" className="btn btn-secondary btn-sm" onClick={() => void loadIntelligence()}>重试</button></div>
              ) : fallbackTargets.length > 0 ? (
                <div className="verify-plan-list">
                  {fallbackTargets.map((target, index) => (
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
            </article>}

            {activeSection === 'execution' && <article className="verify-execution-panel">
              <div className="verify-panel-title">
                <div><span>Runtime Grounding</span><strong>执行前真实条件</strong></div>
                <button type="button" onClick={() => selectView('run-control')}>打开 Run Control</button>
              </div>

              <div className="verify-execution-status">
                <div>
                  <span>Task Grounding</span>
                  <strong className={`tone-${toneForStatus(agentTask?.runtimeGroundingStatus || '')}`}>{statusLabel(agentTask?.runtimeGroundingStatus || '')}</strong>
                  <p>{agentTask?.groundingEvaluatedAt ? `最近评估：${agentTask.groundingEvaluatedAt}` : '当前 Task 尚无 Grounding 回执'}</p>
                </div>
                <div>
                  <span>Project Runtime</span>
                  <strong className={`tone-${runtimeTone}`}>{statusLabel(runtimeStatus)}</strong>
                  <p>{latestRunAt ? `最近项目回执：${latestRunAt}` : '项目后端未上报最近运行时间'}</p>
                </div>
              </div>

              <div className="verify-live-surface">
                <div className="verify-live-surface-tabs"><b>实验条件检查</b></div>
                <div className="verify-live-surface-body">
                  {agentTask?.executionRunId ? (<><strong>已关联真实运行</strong><p>{agentTask.executionRunId} · {statusLabel(agentTask.status)}</p></>) : agentTask?.runtimeGroundingStatus === 'BLOCKED' ? (
                    <>
                      <strong>Runtime Grounding 被真实条件阻断</strong>
                      <p>{agentTask.groundingBlockers.slice(0, 4).map((blocker) => `${blocker.code}：${blocker.message}`).join('；') || '后端未提供 blocker 详情。'}</p>
                    </>
                  ) : agentTask?.runtimeGroundingStatus === 'READY' ? (
                    <>
                      <strong>Runtime Grounding 已就绪，尚未开始 Task-specific Execution</strong>
                      <p>Preflight 和目标绑定已满足当前 Grounding 合同；可以按项目范围启动现有 Scan，真实 Scan 身份和结果会写回本任务；就绪不等于已经执行。</p>
                    </>
                  ) : agentTask?.runtimeGroundingStatus === 'NOT_REQUIRED' ? (
                    <>
                      <strong>这是分析型 Agent Task</strong>
                      <p>该任务只消费固定企业理解快照，不触发 Runtime Preflight 或 Scan。</p>
                    </>
                  ) : (
                    <>
                      <strong>当前没有 Task-specific Runtime Grounding</strong>
                      <p>没有 Grounding 回执时不会拿项目级 Campaign 状态冒充这个 Agent Task 的执行准备度。</p>
                    </>
                  )}
                </div>
              </div>
            </article>}

            {activeSection === 'activity' && <aside className="verify-activity-panel">
              <div className="verify-panel-title"><div><span>Activity</span><strong>工作记录 · Agent Event Ledger</strong></div></div>
              {taskId ? (
                agentTaskLoading && !agentTask ? (
                  <div className="verify-empty"><span className="spinner" /><p>正在读取 Task Events…</p></div>
                ) : agentTaskError && !agentTask ? (
                  <div className="verify-empty danger"><strong>Agent Task 不可读取</strong><p>{agentTaskError}</p></div>
                ) : agentEvents.length > 0 ? (
                  <div className="verify-event-list">
                    {agentEvents.slice().reverse().map((event) => (
                      <div className="verify-event-row" key={event.eventId}>

                        <strong>{eventLabel(event.eventType)}</strong>
                        <details><summary>查看事件详情</summary><p>{eventDetail(event)}</p></details>
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
            </aside>}
          </section>

          <footer className="verify-evidence-dock">
            <span>项目历史</span>
            <b>{error || !data ? '项目证据暂未读取' : `${evidenceBackedFindings} 个项目级证据化 Finding`}</b>
            <Link to={buildProjectPath('/findings', project)}>查看缺陷</Link>
            <Link to={buildProjectPath('/release', project)}>查看发布判断</Link>
          </footer>
        </main>
      </section>
    </div>
  );
}

export default Verify;
