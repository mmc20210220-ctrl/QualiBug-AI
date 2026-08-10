/**
 * Regression-backed validation closure surfaces for the dashboard.
 *
 * Backend field names remain regression_* because they are contract facts.
 * Customer-facing language stays focused on QualiBug verification rather than
 * enterprise development workflow.
 */
import {
  asNum, asRecord, asText, formatScanTime,
  regressionGateLabel, regressionTrendLabel, releaseRecommendationLabel,
  type JsonRecord,
} from '../../lib/dashboard-utils';

type Props = {
  record: JsonRecord;
  regressionRunningMode: string;
  regressionEligible: boolean;
  onRunRegression: (mode: 'smoke' | 'release') => void;
};

export function RegressionClosurePanel({ record, regressionRunningMode, regressionEligible, onRunRegression }: Props) {
  const regressionSummary = asRecord(record.regression_summary);
  const regressionCovered = asNum(regressionSummary.covered_defect_count);
  const regressionFailed = asNum(regressionSummary.failed_defect_count);
  const regressionPending = asNum(regressionSummary.pending_defect_count);
  const regressionPassed = asNum(regressionSummary.passed_defect_count);
  const latestRun = asRecord(regressionSummary.latest_run);
  const regressionRunAt = asText(latestRun.generated_at);
  const regressionGate = regressionGateLabel(asText(latestRun.gate_status));
  const regressionHasLinkedDefects =
    regressionCovered > 0 || regressionPassed > 0 || regressionFailed > 0 || regressionPending > 0;
  const regressionGateDisplay = regressionHasLinkedDefects ? regressionGate : '验证待观察';
  const regressionHeadline = regressionHasLinkedDefects
    ? (asText(regressionSummary.headline) || '当前已经把客户缺陷纳入修复后验证，但尚未返回更多细节。')
    : '当前已有验证执行记录，但还没有与客户缺陷建立关联。';
  const regressionTrend = regressionTrendLabel(asText(regressionSummary.trend_direction));
  const regressionTrendSummary = asText(regressionSummary.trend_summary);
  const regressionHistoryRunCount = asNum(regressionSummary.history_run_count);
  const regressionRecentRuns = (Array.isArray(regressionSummary.recent_runs) ? regressionSummary.recent_runs : []).map(asRecord);
  const regressionLifecycleCounts = asRecord(regressionSummary.lifecycle_counts);
  const regressionValidationSummary = asRecord(regressionSummary.validation_summary);
  const regressionDoubleRunVerified = Boolean(regressionValidationSummary.double_run_verified);
  const regressionRepeatedFailures = asNum(regressionValidationSummary.repeated_failure_defect_count);
  const releaseRecommendation = releaseRecommendationLabel(
    asText(regressionSummary.release_recommendation),
    asText(regressionSummary.release_recommendation_label),
  );
  const releaseRecommendationReason = asText(regressionSummary.release_recommendation_reason);
  const customerDeliveryReadiness =
    asText(regressionSummary.customer_delivery_readiness_label)
    || asText(regressionSummary.customer_delivery_readiness)
    || '持续观察中';

  const hasAnyRegressionFact =
    regressionHasLinkedDefects
    || Boolean(regressionSummary.suite_exists)
    || regressionHistoryRunCount > 0
    || regressionRecentRuns.length > 0;
  if (!hasAnyRegressionFact) return null;

  return (
    <section className="customer-secondary-grid" aria-label="修复后验证闭环">
      {(regressionCovered > 0 || Boolean(regressionSummary.suite_exists)) && (
        <article className={`customer-secondary-card${regressionFailed > 0 || regressionPending > 0 ? ' muted' : ''}`}>
          <span className="customer-value-kicker">修复后验证</span>
          <h3>{regressionGateDisplay}</h3>
          <p>{regressionHeadline}</p>
          <div className="customer-secondary-meta">
            <span><em>已纳入问题</em><b>{regressionCovered}</b></span>
            <span><em>验证通过</em><b>{regressionPassed}</b></span>
            <span><em>验证失败</em><b>{regressionFailed}</b></span>
            <span><em>等待验证</em><b>{regressionPending}</b></span>
            <span><em>最近模式</em><b>{asText(latestRun.suite_mode_label) || asText(latestRun.suite_mode) || '未执行'}</b></span>
            <span><em>最近验证</em><b>{regressionRunAt ? formatScanTime(regressionRunAt) : '暂无'}</b></span>
          </div>
          <div className="customer-showcase-actions">
            <button className="btn btn-primary" onClick={() => onRunRegression('release')} disabled={regressionRunningMode !== '' || !regressionEligible}>
              {regressionRunningMode === 'release' ? 'Release 验证中' : regressionEligible ? '执行 Release 验证' : '暂无可执行验证'}
            </button>
            <button className="btn btn-secondary" onClick={() => onRunRegression('smoke')} disabled={regressionRunningMode !== '' || !regressionEligible}>
              {regressionRunningMode === 'smoke' ? 'Smoke 验证中' : regressionEligible ? '执行 Smoke 验证' : '暂无可执行验证'}
            </button>
          </div>
          {!regressionEligible && <p className="settings-hint mt-3">当前没有已确认 Finding 处于真实回归套件中，因此前端不会提交空验证请求。</p>}
        </article>
      )}

      {(regressionHistoryRunCount > 0 || regressionRecentRuns.length > 0) && (
        <article className={`customer-secondary-card${asText(regressionSummary.trend_direction) === 'regressing' ? ' muted' : ''}`}>
          <span className="customer-value-kicker">验证趋势</span>
          <h3>{regressionTrend}</h3>
          <p>{regressionTrendSummary || '当前已开始沉淀多轮真实验证结果，趋势会随执行轮次自动更新。'}</p>
          <div className="customer-secondary-meta">
            <span><em>历史轮次</em><b>{regressionHistoryRunCount}</b></span>
            <span><em>等待验证</em><b>{asNum(regressionLifecycleCounts.pending_regression)}</b></span>
            <span><em>验证失败</em><b>{asNum(regressionLifecycleCounts.regression_failed)}</b></span>
            <span><em>验证通过</em><b>{asNum(regressionLifecycleCounts.verified_fixed)}</b></span>
            <span><em>无法确认</em><b>{asNum(regressionLifecycleCounts.manual_review_required)}</b></span>
          </div>
          {regressionRecentRuns.length > 0 && (
            <div className="customer-secondary-meta">
              {regressionRecentRuns.slice(0, 3).map((run, index) => (
                <span key={`${asText(run.generated_at)}-${asText(run.suite_mode)}-${index}`}>
                  <em>{formatScanTime(asText(run.generated_at))}</em>
                  <b>{asText(run.suite_mode_label) || asText(run.suite_mode) || '验证'} · {regressionGateLabel(asText(run.gate_status))} · 失败 {asNum(run.failed_count)}</b>
                </span>
              ))}
            </div>
          )}
        </article>
      )}

      {(Boolean(asText(regressionSummary.release_recommendation)) || regressionHistoryRunCount > 0) && (
        <article className={`customer-secondary-card${asText(regressionSummary.release_recommendation) === 'block_release' ? ' muted' : ''}`}>
          <span className="customer-value-kicker">发布 / 交付建议</span>
          <h3>{releaseRecommendation}</h3>
          <p>{releaseRecommendationReason || '当前建议基于最近多轮真实验证、趋势变化和交付资产状态自动生成。'}</p>
          <div className="customer-secondary-meta">
            <span><em>客户交付</em><b>{customerDeliveryReadiness}</b></span>
            <span><em>历史轮次</em><b>{regressionHistoryRunCount}</b></span>
            <span><em>最小双轮验真</em><b>{regressionDoubleRunVerified ? '已满足' : '未满足'}</b></span>
            <span><em>反复失败问题</em><b>{regressionRepeatedFailures}</b></span>
          </div>
        </article>
      )}

      {(regressionHistoryRunCount > 0 || regressionRepeatedFailures > 0) && (
        <article className={`customer-secondary-card${regressionDoubleRunVerified ? '' : ' muted'}`}>
          <span className="customer-value-kicker">真实验真摘要</span>
          <h3>{asText(regressionValidationSummary.headline) || '当前正在沉淀真实多轮验证记录'}</h3>
          <p>
            {regressionDoubleRunVerified
              ? '当前已具备最小双轮验真基础，可以开始把验证趋势用于发布和交付判断。'
              : '当前还不能把一次通过当成稳定结论，建议继续进行真实环境验证。'}
          </p>
          <div className="customer-secondary-meta">
            <span><em>最小要求</em><b>{asNum(regressionValidationSummary.minimum_required_runs, 2)} 轮</b></span>
            <span><em>当前轮次</em><b>{regressionHistoryRunCount}</b></span>
            <span><em>趋势结论</em><b>{regressionTrend}</b></span>
            <span><em>反复失败</em><b>{regressionRepeatedFailures}</b></span>
          </div>
        </article>
      )}
    </section>
  );
}

export default RegressionClosurePanel;