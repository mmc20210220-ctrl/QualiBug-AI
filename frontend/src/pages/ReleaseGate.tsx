import { useSearchParams } from 'react-router-dom';
import { getCommercialAssets, isCustomerReadyFinding, usePipelineData, useReleaseData } from '../api/data';
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
  const { navigateToProjectPath } = useProjectNavigation();
  const { data: releaseData, loading } = useReleaseData(project);
  const { data: pipelineData, error: pipelineError, refetch: refetchPipeline } = usePipelineData(project);
  const commercialAssets = getCommercialAssets(pipelineData);
  const guard = getCustomerDeliveryGuard(pipelineData);

  const checks = (releaseData?.checks || []) as GateCheck[];
  const overall = releaseData?.overall || '';
  const passCount = checks.filter(c => c.status === 'pass').length;
  const failCount = checks.filter(c => c.status === 'fail').length;
  const pendingCount = checks.filter(c => c.status === 'pending').length;
  const hasGateData = checks.length > 0;

  const pipelineRecord = asRecord(pipelineData);
  const customerFindings = ((pipelineRecord.defects || pipelineRecord.risks || []) as Finding[]).filter(isCustomerReadyFinding);
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
    ? { label: '处理 P0 问题', path: '/findings' }
    : regressionFailed
      ? customerFindings.length > 0
        ? { label: '处理回归失败', path: '/findings' }
        : { label: '查看回归闭环', path: '/dashboard' }
      : pipelineUnhealthy
        ? { label: '查看运行状态', path: '/campaigns' }
        : campaignBlocked
          ? { label: '处理阻断条件', path: '/settings' }
          : coverageDeferred
            ? { label: '继续检测剩余范围', path: '/campaigns' }
            : releasePresentation.color === 'red' && customerFindings.length > 0
              ? { label: '处理已确认问题', path: '/findings' }
              : !hasGateData
                ? { label: '启动检测', path: '/campaigns' }
                : { label: '返回价值总览', path: '/dashboard' };

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
        {checks.map((c, i) => (
          <div key={`${c.name}-${i}`} className="release-check-item">
            <span className={`release-check-icon ${c.status}`}>
              {c.status === 'pass' ? '✓' : c.status === 'fail' ? '✗' : '⏳'}
            </span>
            <strong>{c.name}</strong>
            <span className="check-detail">{c.detail}</span>
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
        <button className="btn btn-primary" onClick={() => navigateToProjectPath(nextAction.path, project)}>{nextAction.label}</button>
        {customerFindings.length > 0 && nextAction.path !== '/findings' && (
          <button className="btn btn-secondary" onClick={() => navigateToProjectPath('/findings', project)}>查看问题清单</button>
        )}
        {evidenceCount > 0 && <button className="btn btn-secondary" onClick={() => navigateToProjectPath('/evidence', project)}>查看证据</button>}
        {releasePresentation.incomplete && <button className="btn btn-secondary" onClick={() => navigateToProjectPath('/coverage', project)}>查看未覆盖范围</button>}
        {nextAction.path !== '/dashboard' && <button className="btn btn-secondary" onClick={() => navigateToProjectPath('/dashboard', project)}>返回价值总览</button>}
      </div>
    </div>
  );
}

export default ReleaseGate;