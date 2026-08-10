import { deriveFindingVerification, hasFindingReverificationObligation } from '../../lib/finding-verification';
import { deriveFindingVerificationFocusContext } from '../../lib/finding-verification-focus';
import { FindingVerificationRunSummary } from './FindingVerificationRunSummary';
import { FindingVerificationStatus } from './FindingVerificationStatus';
import { FindingVerificationTimeline } from './FindingVerificationTimeline';
import type { Finding } from '../../types';

type Props = {
  finding: Finding;
  running?: boolean;
  onReverify?: () => void;
  focusGeneratedAt?: string;
};

function runLabel(finding: Finding): string {
  const presentation = deriveFindingVerification(finding);
  const latest = presentation.latestRun;
  if (!latest) return finding.regression?.last_run_at || '尚未执行';
  return latest.generated_at || finding.regression?.last_run_at || '时间未上报';
}

export function FindingVerificationPanel({ finding, running = false, onReverify, focusGeneratedAt = '' }: Props) {
  const presentation = deriveFindingVerification(finding);
  const latest = presentation.latestRun;
  const hasObligation = hasFindingReverificationObligation(finding);
  const canReverifyHere = hasObligation && Boolean(onReverify);
  const focusContext = focusGeneratedAt
    ? deriveFindingVerificationFocusContext(finding, focusGeneratedAt)
    : null;
  const viewingHistoricalRun = Boolean(focusContext && !focusContext.isLatestRun);

  return (
    <section className="card mt-3" aria-label="修复后重新验证">
      <div className="settings-card-head">
        <div>
          <span className="panel-kicker">QualiBug 验证闭环</span>
          <span className="customer-value-kicker">当前最新结论</span>
          <FindingVerificationStatus finding={finding} />
          <p className="muted mt-3">{presentation.detail}</p>
          {viewingHistoricalRun && (
            <p className="verification-focus-hint">
              你当前定位的是历史验证轮次；上方状态始终表示这条 Finding 的当前最新结论，历史轮次仅用于追溯。
            </p>
          )}
        </div>
        <span className="summary-pill">当前最近验证：{runLabel(finding)}</span>
      </div>

      <FindingVerificationRunSummary finding={finding} generatedAt={focusGeneratedAt} />

      <div className="customer-secondary-grid mt-3">
        <article className="customer-secondary-card">
          <span className="customer-value-kicker">修复前基线</span>
          <h3>{finding.expected || '预期行为未单独上报'}</h3>
          <p>{finding.actual || '原始实际行为未单独上报'}</p>
          <div className="customer-secondary-meta">
            <span><em>原始证据</em><b>{finding.evidence_chain?.length || 0} 条</b></span>
            <span><em>证据质量</em><b>{finding.evidence_quality?.label || '未评分'}</b></span>
          </div>
        </article>

        <article className="customer-secondary-card">
          <span className="customer-value-kicker">当前最新修复后验证</span>
          <FindingVerificationStatus finding={finding} compact />
          <p className="mt-3">{latest?.status_label || finding.regression?.latest_status_label || presentation.detail}</p>
          <div className="customer-secondary-meta">
            <span><em>验证时间</em><b>{runLabel(finding)}</b></span>
            <span><em>验证探针</em><b>{finding.regression?.regression_probe_id || latest?.regression_probe_id || '未上报'}</b></span>
            <span><em>目标</em><b>{latest?.method && latest?.path ? `${latest.method} ${latest.path}` : '未上报'}</b></span>
            <span><em>新原始证据</em><b>当前回执未提供</b></span>
          </div>
        </article>
      </div>

      <p className="settings-hint mt-3">
        修复前基线来自当前 Finding 的原始证据；最新侧只展示后端真实 regression 回执。当前回归合同没有返回新的原始响应 / DB / UI 证据时，前端不会伪造“修复前后 Diff”。
      </p>

      {canReverifyHere && (
        <div className="settings-actions mt-3">
          <button type="button" className="btn btn-primary" onClick={onReverify} disabled={running}>
            {running ? '正在重新验证…' : '客户修复后，重新验证'}
          </button>
          <span className="muted">执行当前项目已纳入的真实回归义务；不会记录负责人、修复版本或企业内部研发状态。</span>
        </div>
      )}

      {hasObligation && !onReverify && (
        <p className="settings-hint mt-3">该 Finding 已有真实重新验证义务；当前页面只展示验证证据，重新执行请回到问题清单。</p>
      )}

      {!hasObligation && (
        <p className="settings-hint mt-3">当前没有真实可执行回归义务，因此 QualiBug 不显示虚假的“重新验证”操作。</p>
      )}

      <FindingVerificationTimeline finding={finding} focusGeneratedAt={focusGeneratedAt} />
    </section>
  );
}

export default FindingVerificationPanel;
