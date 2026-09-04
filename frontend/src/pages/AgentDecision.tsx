import { Link, useSearchParams } from 'react-router-dom';
import { isCustomerReadyFinding, usePipelineData } from '../api/data';
import { Skeleton, StatePanel } from '../components/dashboard/DashboardPrimitives';
import { deriveReleasePresentation, type ReleasePresentationCheck } from '../lib/release-presentation';
import { buildProjectPath } from '../lib/project-navigation';
import { usePageTitle } from '../lib/page-title';
import { asArray, asRecord, asText } from '../lib/value-guards';
import type { Finding } from '../types';
import './AgentDecision.css';

function gateCheck(value: unknown): ReleasePresentationCheck | null {
  const record = asRecord(value);
  const name = asText(record.name);
  const status = asText(record.status).toLowerCase();
  if (!name || !['pass', 'fail', 'pending'].includes(status)) return null;
  return { name, status, detail: asText(record.detail) };
}

function decisionHeadline(label: string): string {
  if (label === '建议阻断' || label === '不建议发布') return '我建议暂缓这个版本的发布';
  if (label === '可以发布') return '当前证据支持发布这个版本';
  if (label === '有条件发布') return '这个版本还有已确认风险，暂不建议直接放行';
  return '当前证据还不足以明确放行这个版本';
}

export function AgentDecision() {
  usePageTitle('Decision');
  const [params] = useSearchParams();
  const project = params.get('project')?.trim() || '';
  const { data, loading, error, refetch } = usePipelineData(project);

  if (!project) {
    return <StatePanel eyebrow="Decision" title="选择项目后查看 QualiBug 的发布建议" description="发布判断只消费真实 Finding、真实运行状态、回归结果和后端 Release Gate；没有明确放行回执时不会显示绿色结论。" />;
  }

  if (loading && !data) {
    return (
      <div className="agent-decision">
        <Skeleton h={300} br={24} />
        <Skeleton h={320} br={18} />
      </div>
    );
  }

  if (error && !data) {
    return <StatePanel eyebrow="Decision · 连接状态" title="无法读取发布依据" description={error} action={<button type="button" className="btn btn-primary" onClick={refetch}>重新连接</button>} />;
  }

  const record = asRecord(data);
  const backendGate = asRecord(record.release_gate);
  const checks = asArray(backendGate.checks).map(gateCheck).filter((value): value is ReleasePresentationCheck => value !== null);
  const gateOverall = asText(backendGate.overall_status || backendGate.verdict || backendGate.status).toLowerCase();
  const hasGateData = Boolean(gateOverall) || checks.length > 0;
  const findings = ((record.defects || record.risks || []) as Finding[]).filter(isCustomerReadyFinding);
  const p0Count = findings.filter((finding) => finding.severity === 'P0').length;
  const evidenceBacked = findings.filter((finding) => (finding.evidence_chain?.length || 0) > 0).length;
  const scanMeta = asRecord(record.scan_meta);
  const pipelineHealth = asRecord(record.pipeline_health);
  const pipelineHealthStatus = asText(pipelineHealth.status) || asText(scanMeta.pipeline_health_status);
  const campaign = asRecord(record.campaign);
  const campaignStatus = asText(campaign.campaign_status).toLowerCase();
  const regressionCampaign = Object.keys(asRecord(record.regression_campaign)).length > 0
    ? asRecord(record.regression_campaign)
    : Object.keys(asRecord(record.regression)).length > 0
      ? asRecord(record.regression)
      : asRecord(record.regression_result);
  const regressionRun = asRecord(record.regression_run);
  const regressionSummary = asRecord(record.regression_summary);
  const latestRegressionRun = asRecord(regressionSummary.latest_run);
  const regressionGateStatus = (
    asText(asRecord(regressionCampaign.ci_feedback).gate_status)
    || asText(regressionRun.gate_status)
    || asText(latestRegressionRun.gate_status)
    || asText(regressionCampaign.gate_status)
  ).toLowerCase();

  const presentation = deriveReleasePresentation({
    p0Count,
    confirmedDefectCount: findings.length,
    pipelineHealthStatus,
    campaignStatus,
    gateOverall,
    gateChecks: checks,
    hasGateData,
    regressionGateStatus,
  });

  const failingChecks = checks.filter((check) => String(check.status || '').toLowerCase() === 'fail');
  const pendingChecks = checks.filter((check) => String(check.status || '').toLowerCase() === 'pending');
  const reasons: string[] = [];
  if (p0Count > 0) reasons.push(`${p0Count} 个已确认 P0 仍然存在。`);
  for (const check of failingChecks.slice(0, 3)) reasons.push(`${check.name}${check.detail ? `：${check.detail}` : '：Release Gate 已明确失败。'}`);
  if (regressionGateStatus === 'failed') reasons.push('最新修复后回归 Gate 明确失败。');
  if (['failed_safe', 'blocked'].includes(pipelineHealthStatus.toLowerCase())) reasons.push(`验证链路状态为 ${pipelineHealthStatus}，当前结果不能被解释为安全。`);
  if (campaignStatus === 'blocked') reasons.push('本轮验证被阻断，尚未形成完整可发布结论。');
  if (campaignStatus === 'coverage_deferred') reasons.push('本轮存在明确未覆盖范围。');
  for (const check of pendingChecks.slice(0, Math.max(0, 3 - reasons.length))) reasons.push(`${check.name} 仍待处理。`);
  if (reasons.length === 0 && presentation.label === '可以发布') reasons.push('后端项目级 Release Gate 已明确通过，且当前没有更高优先级的已知阻断状态覆盖它。');
  if (reasons.length === 0) reasons.push('目前没有足够的项目级放行证据，QualiBug 保持保守结论。');

  const nextAction = p0Count > 0 || regressionGateStatus === 'failed' || failingChecks.length > 0
    ? { label: '先调查阻断问题', path: '/findings' }
    : presentation.incomplete || pendingChecks.length > 0
      ? { label: '继续真实验证', path: '/verify' }
      : presentation.label === '可以发布'
        ? { label: '查看完整 Gate 依据', path: '/release/details' }
        : findings.length > 0
          ? { label: '评估已确认风险', path: '/findings' }
          : { label: '继续真实验证', path: '/verify' };

  return (
    <div className="agent-decision">
      <section className={`agent-decision-hero tone-${presentation.color}`}>
        <div className="agent-decision-icon" aria-hidden="true">◆</div>
        <span className="agent-decision-kicker">QualiBug recommends</span>
        <h1>{decisionHeadline(presentation.label)}</h1>
        <p>{presentation.advice}</p>
        <div className="agent-decision-verdict">
          <span>Decision</span>
          <strong>{presentation.label}</strong>
        </div>
      </section>

      <section className="agent-decision-facts">
        <article>
          <span>Confirmed findings</span>
          <strong>{findings.length}</strong>
          <p>{p0Count} 个 P0 · {evidenceBacked} 个带证据链</p>
        </article>
        <article>
          <span>Release Gate</span>
          <strong>{gateOverall || '未上报'}</strong>
          <p>{checks.length > 0 ? `${checks.length} 个真实检查项` : '没有真实 Gate 回执时不会默认通过'}</p>
        </article>
        <article>
          <span>Regression</span>
          <strong>{regressionGateStatus || '未上报'}</strong>
          <p>最新修复后验证状态直接参与发布判断</p>
        </article>
        <article>
          <span>Runtime</span>
          <strong>{pipelineHealthStatus || campaignStatus || '未上报'}</strong>
          <p>验证链不完整时不会把 0 个问题解释成安全</p>
        </article>
      </section>

      <section className="agent-decision-reasons">
        <div className="agent-decision-section-head">
          <div><span>Why</span><h2>为什么 QualiBug 给出这个判断</h2></div>
          <Link to={buildProjectPath('/release/details', project)}>查看完整 Gate 详情</Link>
        </div>
        <ol>
          {reasons.slice(0, 5).map((reason, index) => (
            <li key={`${index}:${reason}`}><span>{index + 1}</span><p>{reason}</p></li>
          ))}
        </ol>
      </section>

      <section className="agent-decision-next">
        <div>
          <span>Recommended next action</span>
          <h2>{nextAction.label}</h2>
          <p>下一步动作来自当前真实 Finding、回归、运行和 Gate 状态，不由前端编造任务完成度。</p>
        </div>
        <div>
          <Link className="btn btn-primary" to={buildProjectPath(nextAction.path, project)}>{nextAction.label}</Link>
          <Link className="btn btn-secondary" to={buildProjectPath('/verify', project)}>返回 Live Workspace</Link>
        </div>
      </section>
    </div>
  );
}

export default AgentDecision;
