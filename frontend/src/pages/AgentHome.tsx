import { useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { createAgentTask, listAgentTasks, type AgentTask, type AgentTaskIntent } from '../api/agent-tasks';
import { isCustomerReadyFinding, usePipelineData } from '../api/data';
import { StatePanel } from '../components/dashboard/DashboardPrimitives';
import { usePageTitle } from '../lib/page-title';
import { buildProjectPath, useProjectNavigation } from '../lib/project-navigation';
import { asRecord, asText } from '../lib/value-guards';
import type { Finding } from '../types';
import './AgentHome.css';

type TaskMode = 'analyze' | 'verify';

const DEFAULT_GOALS: Record<AgentTaskIntent, string> = {
  release_readiness: '检查当前版本是否存在发布阻断风险，并基于真实证据形成发布判断。',
  find_blockers: '找出当前最严重、最可能阻断发布的已验证质量问题。',
  verify_changes: '验证当前版本变更影响到的关键行为，并保留真实证据。',
  analyze_requirements: '分析当前企业资料中的需求、业务规则、缺口和验证目标。',
};

function visibleStatus(value: string): string {
  const normalized = value.trim().toLowerCase();
  if (!normalized) return '未上报';
  const labels: Record<string, string> = {
    created: '已创建',
    understanding: '理解中',
    planning: '规划中',
    evaluating: '判断中',
    cancelled: '已取消',
    running: '正在验证',
    completed: '最近验证已完成',
    executed: '最近验证已执行',
    passed: '已通过',
    failed: '未通过',
    blocked: '已阻断',
    failed_safe: '检测异常',
    coverage_deferred: '部分范围待验证',
    ready: '已就绪',
    hold: '建议暂缓发布',
  };
  return labels[normalized] || value;
}

export function AgentHome() {
  const [params] = useSearchParams();
  return <AgentHomeWorkspace key={params.get('project') || ''} />;
}

function AgentHomeWorkspace() {
  usePageTitle('新任务');
  const [params] = useSearchParams();
  const { navigateToProjectPath } = useProjectNavigation();
  const project = params.get('project')?.trim() || '';
  const { data, loading, error, refetch } = usePipelineData(project);
  const [goal, setGoal] = useState('');
  const [creatingTask, setCreatingTask] = useState(false);
  const [taskError, setTaskError] = useState('');
  const [intent, setIntent] = useState<AgentTaskIntent>('release_readiness');
  const [tasks, setTasks] = useState<AgentTask[]>([]);
  const [tasksLoading, setTasksLoading] = useState(true);
  const [tasksError, setTasksError] = useState('');
  const [taskRefresh, setTaskRefresh] = useState(0);

  useEffect(() => {
    let disposed = false;
    if (!project) return;
    setTasksLoading(true);
    setTasksError('');
    void listAgentTasks(project).then((items) => {
      if (!disposed) setTasks(items);
    }).catch((caught: unknown) => {
      if (!disposed) setTasksError(caught instanceof Error ? caught.message : '任务读取失败');
    }).finally(() => {
      if (!disposed) setTasksLoading(false);
    });
    return () => { disposed = true; };
  }, [project, taskRefresh]);

  const record = asRecord(data);
  const campaign = asRecord(record.campaign);
  const scanMeta = asRecord(record.scan_meta);
  const pipelineHealth = asRecord(record.pipeline_health);
  const releaseGate = asRecord(record.release_gate);
  const runtimeStatus = asText(campaign.campaign_status)
    || asText(scanMeta.execution_status)
    || asText(pipelineHealth.status);
  const releaseStatus = asText(releaseGate.overall_status || releaseGate.verdict || releaseGate.status);
  const findings = useMemo(
    () => ((record.defects || record.risks || []) as Finding[]).filter(isCustomerReadyFinding),
    [record.defects, record.risks],
  );

  const beginTask = async (
    mode: TaskMode,
    intent: AgentTaskIntent = mode === 'analyze' ? 'analyze_requirements' : 'release_readiness',
  ) => {
    if (!project || creatingTask) return;
    setCreatingTask(true);
    setTaskError('');
    try {
      const task = await createAgentTask(project, {
        goal: goal.trim() || DEFAULT_GOALS[intent],
        intent,
      });
      const next = new URLSearchParams();
      next.set('task', task.taskId);
      next.set('goal', task.goal);
      if (mode === 'analyze') next.set('from', 'agent-home');
      navigateToProjectPath('/verify', project, next.toString());
    } catch (caught: unknown) {
      setTaskError(caught instanceof Error ? caught.message : 'Agent Task 创建失败');
    } finally {
      setCreatingTask(false);
    }
  };

  if (!project) {
    return (
      <div className="agent-home">
        <StatePanel
          eyebrow="QualiBug · AI Quality Engineer"
          title="先选择一个客户工作区"
          description="选择客户后，你可以直接给出质量目标、上传企业资料或进入真实验证。QualiBug 不要求先维护测试用例目录。"
        />
      </div>
    );
  }

  return (
    <div className="agent-home">
      <section className="agent-home-hero">
        <span className="agent-home-kicker">QualiBug / 工作空间</span>
        <h1>今天要我帮你验证什么？</h1>
        <p>给出目标，从已有知识开始。一起追踪实验、检查证据，决定下一步。</p>

        <form className="agent-goal-composer" onSubmit={(event) => {
          event.preventDefault();
          if (goal.trim()) void beginTask(intent === 'analyze_requirements' ? 'analyze' : 'verify', intent);
        }}>
          <label className="agent-composer-label" htmlFor="quality-goal">我想验证…</label>
          <textarea
            id="quality-goal"
            value={goal}
            onChange={(event) => setGoal(event.target.value)}
            placeholder="描述你关心的行为、这次变更，或想弄清的问题。"
            rows={3}
            maxLength={4000}
            required
            aria-label="质量验证目标"
            disabled={creatingTask}
          />
          <div className="agent-goal-actions">
            <label className="agent-intent-select">任务方向
              <select value={intent} onChange={(event) => setIntent(event.target.value as AgentTaskIntent)} disabled={creatingTask}>
                <option value="release_readiness">评估发布风险</option>
                <option value="verify_changes">验证版本变更</option>
                <option value="find_blockers">调查阻断问题</option>
                <option value="analyze_requirements">理解企业资料</option>
              </select>
            </label>
            <button type="submit" className="btn btn-primary" disabled={creatingTask || !goal.trim()}>
              {creatingTask ? '正在创建任务…' : '开始任务 ↗'}
            </button>
          </div>
          {taskError && <div className="settings-inline-feedback" role="alert">{taskError}</div>}
        </form>

        <div className="agent-intent-grid" aria-label="建议任务">
          <button type="button" disabled={creatingTask} onClick={() => { setIntent('verify_changes'); setGoal(DEFAULT_GOALS.verify_changes); }}>验证这次变更 ↗</button>
          <button type="button" disabled={creatingTask} onClick={() => { setIntent('find_blockers'); setGoal(DEFAULT_GOALS.find_blockers); }}>找出发布阻断 ↗</button>
          <button type="button" disabled={creatingTask} onClick={() => { setIntent('analyze_requirements'); setGoal(DEFAULT_GOALS.analyze_requirements); }}>理解现有资料 ↗</button>
        </div>
        <div className="agent-source-actions" aria-label="任务上下文">
          <span>补充上下文</span>
          <Link to={buildProjectPath('/analyze', project)}>企业知识</Link>
          <Link to={buildProjectPath('/integration', project)}>连接资料与系统</Link>
          <Link to={buildProjectPath('/verify', project, 'view=run-control')}>测试环境</Link>
        </div>
        <details className="agent-authority-note">
          <summary>任务如何开始工作</summary>
          <p>Goal 是任务上下文，不是执行授权；执行范围由已连接资料、运行环境、Runtime Grounding 和 Preflight 决定。任务复用已有理解快照，创建任务后可查看条件检查与真实事件。</p>
        </details>
      </section>

      <section className="agent-task-history" aria-labelledby="task-history-title">
        <div className="agent-section-heading">
          <div><span>继续工作</span><h2 id="task-history-title">你的任务</h2></div>
          <button className="btn btn-secondary btn-sm" type="button" onClick={() => setTaskRefresh((value) => value + 1)} disabled={tasksLoading}>刷新任务</button>
        </div>
        {tasksError ? <div className="agent-state-error" role="alert"><strong>无法读取任务</strong><p>{tasksError}</p></div>
          : tasksLoading ? <p role="status">正在读取已保存的任务…</p>
          : tasks.length ? <div className="agent-task-list">{tasks.map((task) => (
            <Link key={task.taskId} to={buildProjectPath('/verify', project, `task=${encodeURIComponent(task.taskId)}`)} className="agent-task-row">
              <span className={`agent-task-indicator status-${task.status.toLowerCase()}`} aria-hidden="true" />
              <div><strong>{task.goal}</strong><p>{task.groundingBlockers[0]?.message || (task.executionRunId ? '已关联真实运行 · 打开查看进展' : task.sourceSnapshotStatus === 'PINNED' ? '已有理解快照 · 打开查看下一步' : '打开任务查看上下文与条件')}</p></div>
              <span className="agent-task-meta">{visibleStatus(task.status)}<time dateTime={task.updatedAt}>{task.updatedAt ? new Date(task.updatedAt).toLocaleString('zh-CN') : '时间未上报'}</time></span>
              <span aria-hidden="true">↗</span>
            </Link>
          ))}</div> : <div className="agent-tasks-empty"><strong>从一个问题开始</strong><p>创建的任务会保存在这里。随时回来，沿着原有目标和证据继续。</p></div>}
      </section>

      <details className="agent-current-work" open={Boolean(error)}>
        <summary>项目证据与发布状态</summary>
        <div className="agent-section-heading">
          <div><span>Current work</span><h2>项目级结果</h2></div>
          <Link to={buildProjectPath('/verify', project)}>查看工作台 →</Link>
        </div>

        {error && !data ? (
          <div className="agent-state-error" role="alert">
            <strong>无法读取当前项目状态</strong>
            <p>{error}</p>
            <button type="button" className="btn btn-secondary btn-sm" onClick={refetch}>重新连接</button>
          </div>
        ) : (
          <div className="agent-current-grid">
            <article>
              <span>Agent / Runtime</span>
              <strong>{loading && !data ? '正在读取…' : visibleStatus(runtimeStatus)}</strong>
              <p>只展示后端实际上报的 Campaign / Pipeline 状态。</p>
            </article>
            <article>
              <span>Confirmed findings</span>
              <strong>{loading && !data ? '…' : !data ? '未上报' : findings.length}</strong>
              <p>没有已确认 Finding 不等于系统安全。</p>
            </article>
            <article>
              <span>Decision</span>
              <strong>{loading && !data ? '正在读取…' : visibleStatus(releaseStatus)}</strong>
              <p>没有明确 Release Gate 回执时保持未上报。</p>
            </article>
          </div>
        )}
      </details>
    </div>
  );
}

export default AgentHome;
