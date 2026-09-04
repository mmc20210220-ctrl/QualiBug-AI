import { useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { isCustomerReadyFinding, usePipelineData } from '../api/data';
import { StatePanel } from '../components/dashboard/DashboardPrimitives';
import { usePageTitle } from '../lib/page-title';
import { buildProjectPath, useProjectNavigation } from '../lib/project-navigation';
import { asRecord, asText } from '../lib/value-guards';
import type { Finding } from '../types';
import './AgentHome.css';

type TaskMode = 'analyze' | 'verify';

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

  const beginTask = (mode: TaskMode) => {
    if (!project) return;
    const next = new URLSearchParams();
    if (goal.trim()) next.set('goal', goal.trim());
    if (mode === 'analyze') next.set('from', 'agent-home');
    navigateToProjectPath(mode === 'analyze' ? '/analyze' : '/verify', project, next.toString());
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
          描述质量目标，QualiBug 会沿着“理解 → 计划 → 真实执行 → 证据 → 判断”的主链工作。
          当前自由文本作为工作目标上下文；真实执行范围仍由已连接资料、运行环境和 Preflight 决定，不会因为一句提示词绕过安全边界。
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
            <button type="button" className="btn btn-secondary" onClick={() => beginTask('analyze')}>先理解资料</button>
            <button type="button" className="btn btn-primary" onClick={() => beginTask('verify')}>进入验证工作台</button>
          </div>
        </div>

        <div className="agent-source-actions" aria-label="常用起点">
          <Link to={buildProjectPath('/analyze', project)}><span>＋</span> PRD / 企业资料</Link>
          <Link to={buildProjectPath('/integration', project)}><span>＋</span> API / GitHub / Connector</Link>
          <Link to={buildProjectPath('/verify', project, 'view=run-control')}><span>＋</span> 测试环境</Link>
        </div>
      </section>

      <section className="agent-intent-grid" aria-label="建议任务">
        <button type="button" onClick={() => beginTask('verify')}>
          <span>01</span><strong>找发布阻断</strong><p>基于当前真实运行、Finding 与 Gate 判断下一步。</p>
        </button>
        <button type="button" onClick={() => beginTask('analyze')}>
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
