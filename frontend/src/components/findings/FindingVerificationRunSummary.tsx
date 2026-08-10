import { deriveFindingVerificationFocusContext } from '../../lib/finding-verification-focus';
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

  const context = deriveFindingVerificationFocusContext(finding, normalizedGeneratedAt);
  if (!context) {
    return (
      <section className="verification-run-summary verification-warning" aria-label="指定验证轮次变化摘要">
        <span className="panel-kicker">指定验证轮次</span>
        <h3>当前无法形成这一轮的前后变化摘要</h3>
        <p>指定时间 {normalizedGeneratedAt} 不在当前 Finding 的真实验证历史中。前端不会用最近一次验证或相邻时间替代。</p>
      </section>
    );
  }

  const { summary, isLatestRun, latestGeneratedAt, latestLabel } = context;
  const { event } = summary;
  const releaseMeaning = isLatestRun
    ? summary.releaseMeaning
    : '这是历史验证轮次，只用于追溯当时的验证事实。当前发布判断应结合该 Finding 的最新真实验证结论与项目级 Release Gate。';

  return (
    <section className={`verification-run-summary verification-${event.tone}`} aria-label="指定验证轮次变化摘要">
      <div className="verification-run-summary-head">
        <div>
          <span className="panel-kicker">{isLatestRun ? '当前最新验证' : '历史验证轮次'}</span>
          <h3>{summary.transitionLabel}</h3>
        </div>
        <div className="verification-run-summary-context">
          <span className="summary-pill">{isLatestRun ? '最新' : '历史'}</span>
          <span className="summary-pill">{summary.generatedAt}</span>
        </div>
      </div>

      {!isLatestRun && (
        <div className="verification-focus-hint">
          你正在查看历史轮次。当前 Finding 的最新真实验证发生于 {latestGeneratedAt || '时间未上报'}，最新结论为“{latestLabel}”。下方本轮结果不会覆盖当前最新结论。
        </div>
      )}

      <div className="verification-run-summary-grid">
        <div>
          <em>上一已知结论</em>
          <strong>{summary.previousKnownLabel}</strong>
        </div>
        <div>
          <em>{isLatestRun ? '当前最新真实结果' : '历史本轮真实结果'}</em>
          <strong>{summary.currentLabel}</strong>
        </div>
        <div>
          <em>是否改变当时结论</em>
          <strong>{changeLabel(summary.changedConclusion, summary.currentOutcome)}</strong>
        </div>
        <div>
          <em>{isLatestRun ? '对发布的含义' : '历史轮次的发布含义'}</em>
          <strong>{releaseMeaning}</strong>
        </div>
      </div>

      <div className="verification-timeline-meta mt-3">
        {event.run?.regression_probe_id && <span>Probe {event.run.regression_probe_id}</span>}
        {event.run?.method && event.run?.path && <span>{event.run.method} {event.run.path}</span>}
        {event.run?.gate_status && <span>Gate {event.run.gate_status}</span>}
        {event.changedConclusion && <strong className="verification-change-badge">结论变化</strong>}
      </div>

      <p className="settings-hint mt-3">
        {isLatestRun
          ? '这是当前 Finding 的最新真实验证轮次；项目是否可以发布仍以项目级 Release Gate 为唯一权威。'
          : '这里只追溯这一条 Finding 的历史真实验证轮次；当前 Finding 状态以最新真实验证为准，项目是否可以发布仍以项目级 Release Gate 为唯一权威。'}
      </p>
    </section>
  );
}

export default FindingVerificationRunSummary;
