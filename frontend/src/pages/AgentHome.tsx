import { useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { createAgentTask, type AgentTaskIntent } from '../api/agent-tasks';
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
  usePageTitle('新任务');
  const [params] = useSearchParams();
  const { navigateToProjectPath } = useProjectNavigation();
  const project = params.get('project')?.trim() || '';
  const { data, loading, error, refetch } = usePipelineData(project);
  const [goal, setGoal] = useState('');
  const [creatingTask, setCreatingTask] = useState(false);
  const [taskError, setTaskError] = useState('');

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
      navigateToProjectPath(mode === 'analyze' ? '/analyze' : '/verify', project, next.toString());
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
        <div className="agent-home-orb" aria-hidden="true">Q</div>
        <span className="agent-home-kicker">Your AI Quality Engineer</span>
        <h1>今天要我帮你验证什么？</h1>
        <p>
          描述质量目标后，QualiBug 会先创建一个持久化 Agent Task，再沿着“理解 → 计划 → 真实执行 → 证据 → 判断”的主链工作。
          Goal 是任务上下文，不是执行授权；真实执行范围仍由已连接资料、运行环境、Runtime Grounding 和 Preflight 决定。
        </p>

        <div className="agent-goal-composer">
          <textarea
            value={goal}
            onChange={(event) => setGoal(event.target.value)}
            placeholder="例如：检查这次版本变更是否存在发布阻断风险；或者先分析我刚上传的 PRD。"
            rows={3}
            aria-label="质量验证目标"
          />
          <div className="agent-goal-actions">
            <button type="button" className="btn btn-secondary" onClick={() => void beginTask('analyze')} disabled={creatingTask}>
              {creatingTask ? '正在创建任务…' : '先理解资料'}
            </button>
            <button type="button" className="btn btn-primary" onClick={() => void beginTask('verify')} disabled={creatingTask}>
              {creatingTask ? '正在创建任务…' : '开始 Agent Task'}
            </button>
          </div>
          {taskError && <div className="settings-inline-feedback" role="alert">{taskError}</div>}
        </div>

        <div className="agent-source-actions" aria-label="常用起点">
          <Link to={buildProjectPath('/analyze', project)}><span>＋</span> PRD / 企业资料</Link>
          <Link to={buildProjectPath('/integration', project)}><span>＋</span> API / GitHub / Connector</Link>
          <Link to={buildProjectPath('/verify', project, 'view=run-control')}><span>＋</span> 测试环境</Link>
        </div>
      </section>

      <section className="agent-intent-grid" aria-label="建议任务">
        <button type="button" onClick={() => void beginTask('verify', 'find_blockers')} disabled={creatingTask}>
          <span>01</span><strong>找发布阻断</strong><p>创建真实 Agent Task，并基于当前运行、Finding 与 Gate 判断下一步。</p>
        </button>
        <button type="button" onClick={() => void beginTask('analyze', 'analyze_requirements')} disabled={creatingTask}>
          <span>02</span><strong>分析需求与风险</strong><p>只给 PRD 也可以开始，先理解应该怎样工作。</p>
        </button>
        <Link to={buildProjectPath('/findings', project)}>
          <span>03</span><strong>调查已确认问题</strong><p>从业务影响一路追到 Expected / Actual 与原始证据。</p>
        </Link>
        <Link to={buildProjectPath('/release', project)}>
          <span>04</span><strong>做发布判断</strong><p>查看为什么建议发布、暂缓或继续补证。</p>
        </Link>
      </section>

      <section className="agent-current-work">
        <div className="agent-section-heading">
          <div><span>Current work</span><h2>这个客户现在是什么状态</h2></div>
          <Link to={buildProjectPath('/verify', project)}>打开 Live Workspace →</Link>
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
              <strong>{loading && !data ? '…' : findings.length}</strong>
              <p>没有已确认 Finding 不等于系统安全。</p>
            </article>
            <article>
              <span>Decision</span>
              <strong>{loading && !data ? '正在读取…' : visibleStatus(releaseStatus)}</strong>
              <p>没有明确 Release Gate 回执时保持未上报。</p>
            </article>
          </div>
        )}
      </section>
    </div>
  );
}

export default AgentHome;
