import { isCustomerReadyFinding } from '../../api/data';
import { evidenceDeepLinkSearch } from '../../lib/evidence-presentation';
import {
  deriveLatestVerificationRunSummary,
  type LatestVerificationRunFinding,
} from '../../lib/finding-verification';
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

function rowPriority(row: LatestVerificationRunFinding): number {
  const { event } = row;
  if (event.changedConclusion && event.outcome === 'open') return 50;
  if (!event.changedConclusion && event.outcome === 'open') return 40;
  if (event.outcome === 'unknown') return 30;
  if (event.changedConclusion && event.outcome === 'fixed') return 20;
  return 10;
}

function rowLabel(row: LatestVerificationRunFinding): string {
  const { event } = row;
  if (event.changedConclusion && event.outcome === 'open') return '重新出现';
  if (!event.changedConclusion && event.outcome === 'open') return '仍失败';
  if (event.outcome === 'unknown') return '无法确认';
  if (event.changedConclusion && event.outcome === 'fixed') return '刚验证修复';
  return '保持通过';
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
  const sortedRows = [...summary.rows].sort((left, right) => rowPriority(right) - rowPriority(left));
  const visibleRows = sortedRows.slice(0, 8);
  const hiddenRowCount = Math.max(0, sortedRows.length - visibleRows.length);

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

      <details className="verification-delta-details mt-3" open={unresolvedCount > 0}>
        <summary>
          <strong>查看本轮具体 Finding（{summary.matchedCount}）</strong>
          <span>按验证风险排序，可直接进入同一问题的验证 / 证据时间线</span>
        </summary>
        <div className="verification-delta-list">
          {visibleRows.map((row) => {
            const { finding, event } = row;
            const findingSearch = evidenceDeepLinkSearch(finding.id);
            const hasEvidence = (finding.evidence_chain?.length || 0) > 0;
            return (
              <article key={`${finding.id}:${event.key}`} className={`verification-delta-row verification-${event.tone}`}>
                <div className="verification-delta-row-head">
                  <div>
                    <span className={`severity-badge ${finding.severity.toLowerCase()}`}>{finding.severity}</span>
                    <strong>{finding.title}</strong>
                  </div>
                  <span className="verification-delta-label">{rowLabel(row)}</span>
                </div>
                <p>{event.transitionLabel} · {event.detail}</p>
                <div className="verification-timeline-meta">
                  <span>Finding {finding.id}</span>
                  {event.run?.regression_probe_id && <span>Probe {event.run.regression_probe_id}</span>}
                  {event.run?.method && event.run?.path && <span>{event.run.method} {event.run.path}</span>}
                  {event.run?.gate_status && <span>Gate {event.run.gate_status}</span>}
                  {event.changedConclusion && <strong className="verification-change-badge">结论变化</strong>}
                </div>
                <div className="settings-actions mt-3">
                  <button className="btn btn-secondary btn-sm" onClick={() => navigateToProjectPath('/findings', project, findingSearch)}>查看这条验证</button>
                  {hasEvidence && (
                    <button className="btn btn-secondary btn-sm" onClick={() => navigateToProjectPath('/evidence', project, findingSearch)}>查看这条证据</button>
                  )}
                </div>
              </article>
            );
          })}
        </div>
        {hiddenRowCount > 0 && (
          <p className="settings-hint mt-3">首屏仅展示风险最高的 8 条；另有 {hiddenRowCount} 条同轮真实回执，可在问题清单查看完整验证状态。</p>
        )}
      </details>

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
