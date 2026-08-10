import { useSearchParams } from 'react-router-dom';
import { getCommercialAssets, isCustomerReadyFinding, usePipelineData, useReleaseData } from '../api/data';
import { evidenceDeepLinkSearch } from '../lib/evidence-presentation';
import { deriveFindingVerification } from '../lib/finding-verification';
import { usePageTitle } from '../lib/page-title';
import { useProjectNavigation } from '../lib/project-navigation';
import { deriveReleasePresentation } from '../lib/release-presentation';
import { TermHint } from '../components/TermHint';
import { GLOSSARY } from '../lib/glossary';
import { asRecord } from '../lib/value-guards';
import type { Finding } from '../types';

type GateCheck = { name: string; status: 'pass' | 'fail' | 'pending'; detail: string };

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}
function bool(value: unknown): boolean {
  if (typeof value === 'boolean') return value;
  if (typeof value === 'number') return value !== 0;
  if (typeof value === 'string') return ['true', '1', 'yes'].includes(value.trim().toLowerCase());
  return false;
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
  const { navigateToProjectPath } = useProjectNavigation();
  const { data: releaseData, loading } = useReleaseData(project);
  const { data: pipelineData, error: pipelineError, refetch: refetchPipeline } = usePipelineData(project);
  const commercialAssets = getCommercialAssets(pipelineData);
  const guard = getCustomerDeliveryGuard(pipelineData);

  const checks = (releaseData?.checks || []) as GateCheck[];
  const overall = releaseData?.overall || '';
  const passCount = checks.filter((check) => check.status === 'pass').length;
  const failCount = checks.filter((check) => check.status === 'fail').length;
  const pendingCount = checks.filter((check) => check.status === 'pending').length;
  const hasGateData = checks.length > 0;

  const pipelineRecord = asRecord(pipelineData);
  const customerFindings = ((pipelineRecord.defects || pipelineRecord.risks || []) as Finding[]).filter(isCustomerReadyFinding);
  const requestedFinding = requestedFindingId
    ? customerFindings.find((finding) => finding.id === requestedFindingId) || null
    : null;
  const requestedVerification = requestedFinding ? deriveFindingVerification(requestedFinding) : null;
  const findingContextSearch = evidenceDeepLinkSearch(requestedFindingId);
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
  const regressionGateStatus = text(asRecord(regressionCampaign.ci_feedback).gate_status).toLowerCase();
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

  const lightColor: 'red' | 'yellow' | 'green' = !project || loading ? 'yellow' : releasePresentation.color;
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
              ? '不建议发布：发布门禁存在明确阻断'
              : releasePresentation.label === '可以发布'
                ? '发布门禁已通过'
                : releasePresentation.incomplete
                  ? '暂不能形成完整发布结论'
                  : releasePresentation.label === '待处理'
                    ? '发布门禁仍有待处理事项'
                    : '发布结论待确认';

  const deliveryLabel = guard
    ? (guard.customer_deliverable && guard.safe_for_customer ? '交付已放行' : '交付未放行')
    : commercialAssets?.commercial_handoff.safe_for_customer ? '交付已放行' : '交付未放行';

  const nextAction = p0Count > 0
    ? { label: '查看 P0 验证', path: '/findings' }
    : regressionFailed
      ? customerFindings.length > 0
        ? { label: '查看失败验证', path: '/findings' }
        : { label: '查看回归闭环', path: '/dashboard' }
      : pipelineUnhealthy
        ? { label: '查看运行状态', path: '/campaigns' }
        : campaignBlocked
          ? { label: '处理阻断条件', path: '/settings' }
          : coverageDeferred
            ? { label: '继续检测剩余范围', path: '/campaigns' }
            : releasePresentation.color === 'red' && customerFindings.length > 0
              ? { label: '查看已确认问题', path: '/findings' }
              : !hasGateData
                ? { label: '启动检测', path: '/campaigns' }
                : { label: '返回价值总览', path: '/dashboard' };
  const nextActionSearch = nextAction.path === '/findings' && requestedFinding ? findingContextSearch : '';

  return (
    <div>
      <section className="release-traffic-light">
        <div className={`traffic-light-orb ${lightColor}`} />
        <h1>{conclusion}</h1>
        <p>
          {loading
            ? '正在读取本轮真实发布门禁与检测状态。'
            : hasGateData
              ? `${passCount}/${checks.length} 检查通过${failCount > 0 ? `，${failCount} 项阻塞` : ''}${pendingCount > 0 ? `，${pendingCount} 项待处理` : ''}`
              : '当前尚未取得完整发布门禁回执。'}
          {' · '}{releasePresentation.advice}
          {' · '}交付状态：{deliveryLabel}
        </p>
      </section>

      {requestedFindingId && !loading && pipelineData && (
        <section className={`card mb-4 status-card status-${requestedFinding ? 'warning' : 'neutral'}`} aria-label="当前发布评审问题上下文">
          <span className="panel-kicker">当前评审问题</span>
          {requestedFinding && requestedVerification ? (
            <>
              <h2><span className={`severity-badge ${requestedFinding.severity.toLowerCase()}`}>{requestedFinding.severity}</span> {requestedFinding.title}</h2>
              <div className="customer-secondary-meta mt-3">
                <span><em>QualiBug 验证</em><b className={requestedVerification.tone === 'neutral' ? '' : requestedVerification.tone}>{requestedVerification.label}</b></span>
                <span><em>最近回归</em><b>{requestedFinding.regression?.last_run_at || requestedVerification.latestRun?.generated_at || '尚未执行'}</b></span>
              </div>
              <p className="muted">发布门禁仍按整个项目的真实 Gate 判定；单条 Finding 的修复后验证状态只是发布依据之一，不会覆盖项目级门禁。</p>
              <div className="settings-actions">
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

      {loading && <div className="state-panel"><div className="spinner spinner-centered" /><p>评估发布就绪状态...</p></div>}

      {!loading && pipelineError && !pipelineData && (
        <section className="findings-empty-state danger">
          <span className="findings-empty-kicker">数据异常</span>
          <h3>发布依据暂时不可用</h3>
          <p>{pipelineError}</p>
          <button className="btn btn-primary" onClick={refetchPipeline}>重新读取</button>
        </section>
      )}

      <section className="release-checklist">
        <h2><TermHint label="发布门禁" hint={GLOSSARY.releaseGate} />检查清单</h2>
        {checks.length === 0 && !loading && (
          <div className="release-check-item">
            <span className="release-check-icon pending">!</span>
            <strong>暂无完整门禁数据</strong>
            <span className="check-detail">当前不能把 0 条门禁数据解释为“可以发布”；请完成检测后再查看发布结论。</span>
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
      </section>

      <section className="release-checklist">
        <h2><TermHint label="商业交付确认" hint="门禁通过只说明检查项通过；交付放行需要后端交付守卫（customer_delivery_guard）明确确认，两者独立判定。" /></h2>
        <div className="release-check-item">
          <span className={`release-check-icon ${deliveryLabel === '交付已放行' ? 'pass' : 'pending'}`}>
            {deliveryLabel === '交付已放行' ? '✓' : '!'}
          </span>
          <strong>{deliveryLabel}</strong>
          <span className="check-detail">
            {guard
              ? guard.customer_deliverable && guard.safe_for_customer
                ? 'customer_delivery_guard 已明确放行，可进入客户验收。'
                : guard.block_reasons.length > 0
                  ? `阻塞原因：${guard.block_reasons.join('、')}`
                  : guard.honesty_rule || '门禁通过不等于交付放行。'
              : '门禁通过本身不等于商业交付放行，需 Handoff 明确确认。'}
          </span>
        </div>
      </section>

      <div className="action-bar">
        <span className="action-bar-title">下一步：{nextAction.label}</span>
        <button className="btn btn-primary" onClick={() => navigateToProjectPath(nextAction.path, project, nextActionSearch)}>{nextAction.label}</button>
        {customerFindings.length > 0 && nextAction.path !== '/findings' && (
          <button className="btn btn-secondary" onClick={() => navigateToProjectPath('/findings', project, requestedFinding ? findingContextSearch : '')}>查看问题清单</button>
        )}
        {evidenceCount > 0 && <button className="btn btn-secondary" onClick={() => navigateToProjectPath('/evidence', project, requestedFindingHasEvidence ? findingContextSearch : '')}>查看证据</button>}
        {releasePresentation.incomplete && <button className="btn btn-secondary" onClick={() => navigateToProjectPath('/coverage', project)}>查看未覆盖范围</button>}
        {nextAction.path !== '/dashboard' && <button className="btn btn-secondary" onClick={() => navigateToProjectPath('/dashboard', project)}>返回价值总览</button>}
      </div>
    </div>
  );
}

export default ReleaseGate;
