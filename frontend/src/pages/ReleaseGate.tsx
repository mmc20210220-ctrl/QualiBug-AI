import { useSearchParams } from 'react-router-dom';
import { getCommercialAssets, isCustomerReadyFinding, usePipelineData } from '../api/data';
import { ReleaseDecisionSnapshot } from '../components/release/ReleaseDecisionSnapshot';
import { evidenceDeepLinkSearch } from '../lib/evidence-presentation';
import { deriveFindingVerification } from '../lib/finding-verification';
import { usePageTitle } from '../lib/page-title';
import { useProjectNavigation } from '../lib/project-navigation';
import { deriveReleasePresentation } from '../lib/release-presentation';
import { FindingVerificationRunSummary } from '../components/findings/FindingVerificationRunSummary';
import { FindingVerificationStatus } from '../components/findings/FindingVerificationStatus';
import { FindingVerificationTimeline } from '../components/findings/FindingVerificationTimeline';
import { TermHint } from '../components/TermHint';
import { GLOSSARY } from '../lib/glossary';
import { asArray, asRecord } from '../lib/value-guards';
import type { Finding } from '../types';

type GateCheck = { name: string; status: 'pass' | 'fail' | 'pending'; detail: string };
type DecisionFact = { label: string; value: string; detail: string; tone: 'success' | 'warning' | 'danger' | 'neutral' };

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}
function bool(value: unknown): boolean {
  if (typeof value === 'boolean') return value;
  if (typeof value === 'number') return value !== 0;
  if (typeof value === 'string') return ['true', '1', 'yes'].includes(value.trim().toLowerCase());
  return false;
}

function releaseCheckFrom(value: unknown): GateCheck | null {
  const record = asRecord(value);
  const name = text(record.name);
  const status = text(record.status).toLowerCase();
  if (!name || !['pass', 'fail', 'pending'].includes(status)) return null;
  return {
    name,
    status: status as GateCheck['status'],
    detail: text(record.detail) || '后端项目级发布门禁未提供该检查项详情。',
  };
}

function getCustomerDeliveryGuard(raw: unknown) {
  const record = asRecord(raw);
  const direct = asRecord(record.customer_delivery_guard);
  if (!Object.keys(direct).length) return null;
  return {
    status: text(direct.status),
    customer_deliverable: bool(direct.customer_deliverable),
    safe_for_customer: bool(direct.safe_for_customer),
    block_reasons: Array.isArray(direct.block_reasons) ? direct.block_reasons.map(text).filter(Boolean) : [],
    honesty_rule: text(direct.honesty_rule),
  };
}

export function ReleaseGate() {
  usePageTitle('发布门禁');
  const [params] = useSearchParams();
  const project = params.get('project')?.trim() || '';
  const requestedFindingId = params.get('finding')?.trim() || '';
  const requestedVerificationAt = params.get('verification_at')?.trim() || '';
  const { navigateToProjectPath } = useProjectNavigation();
  const { data: pipelineData, loading, error: pipelineError, refetch: refetchPipeline } = usePipelineData(project);
  const commercialAssets = getCommercialAssets(pipelineData);
  const guard = getCustomerDeliveryGuard(pipelineData);

  const pipelineRecord = asRecord(pipelineData);
  const backendGate = asRecord(pipelineRecord.release_gate);
  const checks = asArray(backendGate.checks).map(releaseCheckFrom).filter((value): value is GateCheck => value !== null);
  const explicitOverall = text(backendGate.overall_status || backendGate.verdict || backendGate.status).toLowerCase();
  const overall = ['pass', 'fail', 'pending'].includes(explicitOverall) ? explicitOverall : '';
  const hasGateData = Boolean(overall) || checks.length > 0;
  const passCount = checks.filter((check) => check.status === 'pass').length;
  const failChecks = checks.filter((check) => check.status === 'fail');
  const pendingChecks = checks.filter((check) => check.status === 'pending');
  const failCount = failChecks.length;
  const pendingCount = pendingChecks.length;

  const customerFindings = ((pipelineRecord.defects || pipelineRecord.risks || []) as Finding[]).filter(isCustomerReadyFinding);
  const requestedFinding = requestedFindingId
    ? customerFindings.find((finding) => finding.id === requestedFindingId) || null
    : null;
  const requestedVerification = requestedFinding ? deriveFindingVerification(requestedFinding) : null;
  const findingContextSearch = evidenceDeepLinkSearch(requestedFindingId, requestedVerificationAt);
  const requestedFindingHasEvidence = Boolean(requestedFinding && (requestedFinding.evidence_chain?.length || 0) > 0);
  const p0Count = customerFindings.filter((finding) => finding.severity === 'P0').length;
  const evidenceCount = customerFindings.filter((finding) => (finding.evidence_chain?.length || 0) > 0).length;

  const scanMeta = asRecord(pipelineRecord.scan_meta);
  const pipelineHealth = asRecord(pipelineRecord.pipeline_health);
  const pipelineHealthStatus = text(pipelineHealth.status) || text(scanMeta.pipeline_health_status);
  const campaign = asRecord(pipelineRecord.campaign);
  const campaignStatus = text(campaign.campaign_status).toLowerCase();
  const pipelineUnhealthy = ['FAILED_SAFE', 'BLOCKED'].includes(pipelineHealthStatus.toUpperCase());
  const campaignBlocked = campaignStatus === 'blocked';
  const coverageDeferred = campaignStatus === 'coverage_deferred';

  const regressionCampaign = Object.keys(asRecord(pipelineRecord.regression_campaign)).length > 0
    ? asRecord(pipelineRecord.regression_campaign)
    : Object.keys(asRecord(pipelineRecord.regression)).length > 0
      ? asRecord(pipelineRecord.regression)
      : asRecord(pipelineRecord.regression_result);
  const regressionRun = asRecord(pipelineRecord.regression_run);
  const regressionSummary = asRecord(pipelineRecord.regression_summary);
  const latestRegressionRun = asRecord(regressionSummary.latest_run);
  const regressionGateStatus = (
    text(asRecord(regressionCampaign.ci_feedback).gate_status)
    || text(regressionRun.gate_status)
    || text(latestRegressionRun.gate_status)
    || text(regressionCampaign.gate_status)
  ).toLowerCase();
  const regressionGeneratedAt = text(regressionRun.generated_at)
    || text(latestRegressionRun.generated_at)
    || text(asRecord(regressionCampaign.summary).generated_at)
    || text(regressionCampaign.generated_at);
  const regressionFailed = regressionGateStatus === 'failed';

  const releasePresentation = deriveReleasePresentation({
    p0Count,
    confirmedDefectCount: customerFindings.length,
    pipelineHealthStatus,
    campaignStatus,
    gateOverall: overall,
    gateChecks: checks,
    hasGateData,
    regressionGateStatus,
  });

  const conclusion = !project
    ? '请选择项目后查看发布建议'
    : loading
      ? '正在评估发布就绪状态'
      : pipelineError && !pipelineData
        ? '发布依据暂时不可读取'
        : releasePresentation.label === '建议阻断'
          ? `建议阻断发布：已确认 ${p0Count} 个 P0`
          : regressionFailed
            ? '不建议发布：最新回归门禁失败'
            : releasePresentation.label === '不建议发布'
              ? '不建议发布：项目级发布门禁存在明确阻断'
              : releasePresentation.label === '可以发布'
                ? '项目级发布门禁已明确通过'
                : releasePresentation.incomplete
                  ? '暂不能形成完整发布结论'
                  : releasePresentation.label === '待处理'
                    ? '项目级发布门禁仍有待处理事项'
                    : '发布结论待确认';

  const gateFact: DecisionFact = failChecks[0]
    ? {
        label: '真实项目级 Gate',
        value: `首个上报失败：${failChecks[0].name}`,
        detail: failChecks[0].detail,
        tone: 'danger',
      }
    : overall === 'fail'
      ? {
          label: '真实项目级 Gate',
          value: '整体 Gate 已失败',
          detail: '后端已明确返回 fail，但当前没有提供可展示的失败检查项；前端不会猜测具体根因。',
          tone: 'danger',
        }
      : pendingChecks[0]
        ? {
            label: '真实项目级 Gate',
            value: `首个待处理：${pendingChecks[0].name}`,
            detail: pendingChecks[0].detail,
            tone: 'warning',
          }
        : overall === 'pending'
          ? {
              label: '真实项目级 Gate',
              value: '整体 Gate 待处理',
              detail: '后端尚未形成明确放行结论。',
              tone: 'warning',
            }
          : overall === 'pass'
            ? {
                label: '真实项目级 Gate',
                value: '整体 Gate 已通过',
                detail: checks.length > 0 ? `${passCount}/${checks.length} 个已上报检查项通过。` : '后端已明确返回整体 pass。',
                tone: 'success',
              }
            : checks.length > 0
              ? {
                  label: '真实项目级 Gate',
                  value: '检查项已上报，整体结论未明确',
                  detail: `${passCount}/${checks.length} 项通过；没有明确 overall=pass 时前端不会放行。`,
                  tone: 'warning',
                }
              : {
                  label: '真实项目级 Gate',
                  value: '尚未上报',
                  detail: '当前没有真实项目级 release_gate 回执；0 个问题不能替代 Gate。',
                  tone: 'warning',
                };

  const regressionFact: DecisionFact = regressionGateStatus === 'failed'
    ? {
        label: '最新修复后回归',
        value: '回归 Gate 失败',
        detail: `${regressionGeneratedAt ? `最近回归：${regressionGeneratedAt}。` : ''}该失败状态直接参与当前发布结论。`,
        tone: 'danger',
      }
    : regressionGateStatus === 'passed'
      ? {
          label: '最新修复后回归',
          value: '回归 Gate 通过',
          detail: `${regressionGeneratedAt ? `最近回归：${regressionGeneratedAt}。` : ''}单次回归通过不等于项目级 Gate 放行；没有前一版发布快照时，前端也不会声称“发布结论已被改变”。`,
          tone: 'success',
        }
      : ['pending', 'not_ready', 'manual_approval_required'].includes(regressionGateStatus)
        ? {
            label: '最新修复后回归',
            value: '回归尚未闭合',
            detail: `${regressionGeneratedAt ? `最近回归：${regressionGeneratedAt}。` : ''}该状态参与当前发布判断，但不会覆盖项目级 Gate。`,
            tone: 'warning',
          }
        : {
            label: '最新修复后回归',
            value: '未上报明确 Gate 状态',
            detail: '当前没有可确认的最新回归 Gate 结果；前端不会把“未上报”解释为通过。',
            tone: 'neutral',
          };

  const nextAction = p0Count > 0
    ? { label: '查看 P0 验证', detail: '先核对已确认 P0 的当前真实验证状态。', path: '/findings', anchor: '' }
    : regressionFailed
      ? customerFindings.length > 0
        ? { label: '查看失败验证', detail: '定位仍失败或重新打开的真实验证项。', path: '/findings', anchor: '' }
        : { label: '查看回归闭环', detail: '回到价值总览核对项目级回归结果。', path: '/dashboard', anchor: '' }
      : pipelineUnhealthy
        ? { label: '查看运行阻断', detail: '先恢复检测链路，再讨论发布。', path: '/campaigns', anchor: '' }
        : campaignBlocked
          ? { label: '查看运行阻断', detail: 'Campaign 尚未形成完整可发布结论。', path: '/campaigns', anchor: '' }
          : coverageDeferred
            ? { label: '查看未覆盖范围', detail: '剩余范围未完成前不能形成完整发布结论。', path: '/coverage', anchor: '' }
            : failCount > 0 || pendingCount > 0 || overall === 'fail' || overall === 'pending'
              ? { label: '查看真实 Gate 详情', detail: '先核对后端已经上报的失败或待处理检查项。', path: '', anchor: 'release-gate-checklist' }
              : !hasGateData
                ? { label: '启动检测', detail: '先形成真实项目级发布门禁回执。', path: '/campaigns', anchor: '' }
                : customerFindings.length > 0
                  ? { label: '查看验证状态', detail: 'Gate 已有结论，继续核对已确认问题的修复后验证。', path: '/findings', anchor: '' }
                  : { label: '返回价值总览', detail: '项目级 Gate 已明确通过，可返回结果总览核对本轮边界。', path: '/dashboard', anchor: '' };

  const nextActionSearch = nextAction.path === '/findings' && requestedFinding ? findingContextSearch : '';
  const handleNextAction = () => {
    if (nextAction.anchor) {
      document.getElementById(nextAction.anchor)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      return;
    }
    navigateToProjectPath(nextAction.path, project, nextActionSearch);
  };

  const deliveryLabel = guard
    ? (guard.customer_deliverable && guard.safe_for_customer ? '交付已放行' : '交付未放行')
    : commercialAssets?.commercial_handoff.safe_for_customer ? '交付已放行' : '交付未放行';

  if (!project) {
    return (
      <section className="state-panel">
        <div className="state-panel-badge">发布门禁</div>
        <h2>请先选择客户项目</h2>
        <p>选择项目后，QualiBug 才能读取真实项目级 Gate、回归状态和当前已确认问题。</p>
      </section>
    );
  }

  if (loading) {
    return <div className="state-panel"><div className="spinner spinner-centered" /><p>评估发布就绪状态...</p></div>;
  }

  if (pipelineError && !pipelineData) {
    return (
      <section className="findings-empty-state danger">
        <span className="findings-empty-kicker">数据异常</span>
        <h3>发布依据暂时不可用</h3>
        <p>{pipelineError}</p>
        <button className="btn btn-primary" onClick={refetchPipeline}>重新读取</button>
      </section>
    );
  }

  return (
    <div>
      <ReleaseDecisionSnapshot
        presentation={releasePresentation}
        conclusion={conclusion}
        gateFact={gateFact}
        regressionFact={regressionFact}
        nextAction={{ label: nextAction.label, detail: nextAction.detail }}
        onNextAction={handleNextAction}
      />

      {requestedFindingId && pipelineData && (
        <section className={`card mb-4 status-card status-${requestedFinding ? 'warning' : 'neutral'}`} aria-label="当前发布评审问题上下文">
          <span className="panel-kicker">当前评审问题</span>
          {requestedFinding && requestedVerification ? (
            <>
              <h2><span className={`severity-badge ${requestedFinding.severity.toLowerCase()}`}>{requestedFinding.severity}</span> {requestedFinding.title}</h2>
              <div className="mt-3"><FindingVerificationStatus finding={requestedFinding} /></div>
              <div className="customer-secondary-meta mt-3">
                <span><em>最近验证</em><b>{requestedFinding.regression?.last_run_at || requestedVerification.latestRun?.generated_at || '尚未执行'}</b></span>
                <span><em>验证下一步</em><b>{requestedVerification.nextActionLabel}</b></span>
              </div>
              <p className="muted">发布门禁仍按整个项目的真实 Gate 判定；单条 Finding 的修复后验证状态只是发布依据之一，不会覆盖项目级门禁。</p>
              <FindingVerificationRunSummary finding={requestedFinding} generatedAt={requestedVerificationAt} />
              <FindingVerificationTimeline finding={requestedFinding} compact focusGeneratedAt={requestedVerificationAt} />
              <div className="settings-actions mt-3">
                <button className="btn btn-secondary" onClick={() => navigateToProjectPath('/findings', project, findingContextSearch)}>返回这条问题</button>
                {requestedFindingHasEvidence && <button className="btn btn-secondary" onClick={() => navigateToProjectPath('/evidence', project, findingContextSearch)}>查看这条证据</button>}
              </div>
            </>
          ) : (
            <>
              <h2>原问题已不在当前已确认结果中</h2>
              <p className="muted">它可能来自旧扫描，也可能在重新验证后退出当前已确认列表。仅凭“列表中消失”不能断言已修复；发布页不会按标题猜测替代问题，项目级门禁仍按当前真实数据展示。</p>
              <button className="btn btn-secondary" onClick={() => navigateToProjectPath('/findings', project)}>查看当前问题清单</button>
            </>
          )}
        </section>
      )}

      <details className="release-checklist" id="release-gate-checklist" open={failCount > 0 || pendingCount > 0 || overall === 'fail' || overall === 'pending'}>
        <summary><strong><TermHint label="项目级发布门禁" hint={GLOSSARY.releaseGate} /> · 完整检查清单</strong></summary>
        <div className="mt-3">
          {checks.length === 0 && (
            <div className="release-check-item">
              <span className="release-check-icon pending">!</span>
              <strong>暂无真实项目级 Gate 检查项</strong>
              <span className="check-detail">当前不能把 0 条 Gate 数据解释为“可以发布”；前端不会用 P0 数量、安全分类或 DB 数量自行补出通过检查项。</span>
            </div>
          )}
          {checks.map((check, index) => (
            <div key={`${check.name}-${index}`} className="release-check-item">
              <span className={`release-check-icon ${check.status}`}>
                {check.status === 'pass' ? '✓' : check.status === 'fail' ? '✗' : '⏳'}
              </span>
              <strong>{check.name}</strong>
              <span className="check-detail">{check.detail}</span>
            </div>
          ))}
        </div>
      </details>

      <details className="release-checklist">
        <summary><strong>交付守卫（独立于发布 Gate）</strong></summary>
        <div className="release-check-item mt-3">
          <span className={`release-check-icon ${deliveryLabel === '交付已放行' ? 'pass' : 'pending'}`}>
            {deliveryLabel === '交付已放行' ? '✓' : '!'}
          </span>
          <strong>{deliveryLabel}</strong>
          <span className="check-detail">
            {guard
              ? guard.customer_deliverable && guard.safe_for_customer
                ? 'customer_delivery_guard 已明确放行；这是交付守卫事实，不会反向覆盖项目级 Release Gate。'
                : guard.block_reasons.length > 0
                  ? `阻塞原因：${guard.block_reasons.join('、')}`
                  : guard.honesty_rule || '门禁通过不等于交付放行。'
              : '当前未取得 customer_delivery_guard 明确放行；项目级 Gate 与交付守卫保持独立。'}
          </span>
        </div>
      </details>

      <details className="card mb-4 dashboard-more-actions">
        <summary><strong>更多发布核对操作</strong> <span className="muted">首屏只保留当前最高价值动作</span></summary>
        <div className="settings-actions mt-3">
          {customerFindings.length > 0 && (
            <button className="btn btn-secondary" onClick={() => navigateToProjectPath('/findings', project, requestedFinding ? findingContextSearch : '')}>查看问题清单</button>
          )}
          {evidenceCount > 0 && <button className="btn btn-secondary" onClick={() => navigateToProjectPath('/evidence', project, requestedFindingHasEvidence ? findingContextSearch : '')}>查看证据</button>}
          {releasePresentation.incomplete && <button className="btn btn-secondary" onClick={() => navigateToProjectPath('/coverage', project)}>查看未覆盖范围</button>}
          <button className="btn btn-secondary" onClick={() => navigateToProjectPath('/dashboard', project)}>返回价值总览</button>
        </div>
      </details>
    </div>
  );
}

export default ReleaseGate;
