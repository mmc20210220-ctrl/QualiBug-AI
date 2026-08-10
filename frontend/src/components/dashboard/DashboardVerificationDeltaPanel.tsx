import { isCustomerReadyFinding } from '../../api/data';
import { deriveLatestVerificationRunSummary } from '../../lib/finding-verification';
import { useProjectNavigation } from '../../lib/project-navigation';
import { asRecord, asText, formatScanTime, type JsonRecord } from '../../lib/dashboard-utils';
import type { Finding } from '../../types';

type Props = {
  record: JsonRecord;
  project: string;
};

function headline(summary: NonNullable<ReturnType<typeof deriveLatestVerificationRunSummary>>): string {
  if (summary.reopenedCount > 0) return `${summary.reopenedCount} 个问题在本轮重新出现，需要优先复核`;
  if (summary.fixedCount > 0 && summary.stillFailingCount === 0 && summary.inconclusiveCount === 0) {
    return `${summary.fixedCount} 个问题在本轮得到真实修复验证`;
  }
  if (summary.fixedCount > 0) {
    return `${summary.fixedCount} 个问题确认修复，仍有验证风险未闭环`;
  }
  if (summary.stillFailingCount > 0) return `${summary.stillFailingCount} 个问题本轮验证后仍然失败`;
  if (summary.inconclusiveCount > 0) return `${summary.inconclusiveCount} 个问题本轮尚无法形成修复结论`;
  if (summary.keptFixedCount > 0) return `${summary.keptFixedCount} 个问题继续保持验证通过`;
  return '最新修复后验证已完成';
}

export function DashboardVerificationDeltaPanel({ record, project }: Props) {
  const { navigateToProjectPath } = useProjectNavigation();
  const regressionRun = asRecord(record.regression_run);
  const regressionSummary = asRecord(record.regression_summary);
  const latestRun = asRecord(regressionSummary.latest_run);
  const runAt = asText(regressionRun.generated_at) || asText(latestRun.generated_at);
  if (!runAt) return null;

  const findings = ((record.defects || record.risks || []) as Finding[]).filter(isCustomerReadyFinding);
  const summary = deriveLatestVerificationRunSummary(findings, runAt);
  if (!summary) return null;

  const mode = asText(regressionRun.suite_mode_label)
    || asText(regressionRun.suite_mode)
    || asText(latestRun.suite_mode_label)
    || asText(latestRun.suite_mode)
    || '修复后验证';

  if (summary.matchedCount === 0) {
    return (
      <section className="customer-status-card neutral mb-4" aria-label="最新修复后验证变化">
        <span>最新修复后验证变化</span>
        <strong>项目级验证已完成，逐问题变化暂不可对齐</strong>
        <p>
          最新真实执行为 {formatScanTime(runAt)} · {mode}，但当前 Finding history 没有同一 generated_at 的逐问题回执。
          前端不会把不同轮次的“最新状态”拼成一次验证变化，也不会补造修复数量。
        </p>
        <div className="settings-actions mt-3">
          <button className="btn btn-secondary" onClick={() => navigateToProjectPath('/findings', project)}>查看当前验证状态</button>
          <button className="btn btn-secondary" onClick={() => navigateToProjectPath('/release', project)}>查看发布门禁</button>
        </div>
      </section>
    );
  }

  const unresolvedCount = summary.reopenedCount + summary.stillFailingCount + summary.inconclusiveCount;
  const tone = summary.reopenedCount > 0 || summary.stillFailingCount > 0
    ? 'danger'
    : summary.inconclusiveCount > 0
      ? 'warning'
      : summary.fixedCount > 0 || summary.keptFixedCount > 0
        ? 'success'
        : 'neutral';

  return (
    <section className={`customer-status-card ${tone} mb-4`} aria-label="最新修复后验证变化">
      <span>最新修复后验证变化</span>
      <strong>{headline(summary)}</strong>
      <p>
        只统计 {formatScanTime(summary.runAt)} · {mode} 这一轮真实回执，共关联 {summary.matchedCount} 个 Finding。
        “刚验证修复 / 重新出现”只来自真实 open ↔ fixed 结论变化；无法确认不会被包装成修复。
      </p>
      <div className="customer-secondary-meta">
        <span><em>本轮关联</em><b>{summary.matchedCount}</b></span>
        <span><em>刚验证修复</em><b>{summary.fixedCount}</b></span>
        <span><em>重新出现</em><b>{summary.reopenedCount}</b></span>
        <span><em>仍失败</em><b>{summary.stillFailingCount}</b></span>
        <span><em>无法确认</em><b>{summary.inconclusiveCount}</b></span>
        <span><em>保持通过</em><b>{summary.keptFixedCount}</b></span>
      </div>
      <div className="settings-actions mt-3">
        <button className="btn btn-primary" onClick={() => navigateToProjectPath('/findings', project)}>
          {unresolvedCount > 0 ? '查看未闭环验证' : '查看验证详情'}
        </button>
        <button className="btn btn-secondary" onClick={() => navigateToProjectPath('/release', project)}>查看发布门禁</button>
      </div>
    </section>
  );
}

export default DashboardVerificationDeltaPanel;
