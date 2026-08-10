import { deriveFocusedVerificationRunSummary } from '../../lib/finding-verification';
import type { Finding } from '../../types';

type Props = {
  finding: Finding;
  generatedAt?: string;
};

function changeLabel(changedConclusion: boolean, currentOutcome: 'fixed' | 'open' | 'unknown'): string {
  if (changedConclusion) return '是，真实终态发生变化';
  if (currentOutcome === 'unknown') return '否，本轮未形成新终态';
  return '否，已知结论保持不变';
}

export function FindingVerificationRunSummary({ finding, generatedAt = '' }: Props) {
  const normalizedGeneratedAt = String(generatedAt || '').trim();
  if (!normalizedGeneratedAt) return null;

  const summary = deriveFocusedVerificationRunSummary(finding, normalizedGeneratedAt);
  if (!summary) {
    return (
      <section className="verification-run-summary verification-warning" aria-label="指定验证轮次变化摘要">
        <span className="panel-kicker">指定验证轮次</span>
        <h3>当前无法形成这一轮的前后变化摘要</h3>
        <p>指定时间 {normalizedGeneratedAt} 不在当前 Finding 的真实验证历史中。前端不会用最近一次验证或相邻时间替代。</p>
      </section>
    );
  }

  const { event } = summary;
  return (
    <section className={`verification-run-summary verification-${event.tone}`} aria-label="指定验证轮次变化摘要">
      <div className="verification-run-summary-head">
        <div>
          <span className="panel-kicker">当前指定验证</span>
          <h3>{summary.transitionLabel}</h3>
        </div>
        <span className="summary-pill">{summary.generatedAt}</span>
      </div>

      <div className="verification-run-summary-grid">
        <div>
          <em>上一已知结论</em>
          <strong>{summary.previousKnownLabel}</strong>
        </div>
        <div>
          <em>本轮真实结果</em>
          <strong>{summary.currentLabel}</strong>
        </div>
        <div>
          <em>是否改变结论</em>
          <strong>{changeLabel(summary.changedConclusion, summary.currentOutcome)}</strong>
        </div>
        <div>
          <em>对发布的含义</em>
          <strong>{summary.releaseMeaning}</strong>
        </div>
      </div>

      <div className="verification-timeline-meta mt-3">
        {event.run?.regression_probe_id && <span>Probe {event.run.regression_probe_id}</span>}
        {event.run?.method && event.run?.path && <span>{event.run.method} {event.run.path}</span>}
        {event.run?.gate_status && <span>Gate {event.run.gate_status}</span>}
        {event.changedConclusion && <strong className="verification-change-badge">结论变化</strong>}
      </div>

      <p className="settings-hint mt-3">这里只解释这一条 Finding 在指定真实验证轮次中的变化；项目是否可以发布仍以项目级 Release Gate 为唯一权威。</p>
    </section>
  );
}

export default FindingVerificationRunSummary;
