import {
  buildFindingVerificationTimeline,
  latestFindingConclusionChange,
} from '../../lib/finding-verification';
import type { Finding } from '../../types';

type Props = {
  finding: Finding;
  compact?: boolean;
};

function runTitle(event: ReturnType<typeof buildFindingVerificationTimeline>[number]): string {
  if (event.kind === 'baseline') return '原始 Finding';
  const mode = event.run?.suite_mode_label || event.run?.suite_mode || '修复后验证';
  return mode;
}

export function FindingVerificationTimeline({ finding, compact = false }: Props) {
  const timeline = buildFindingVerificationTimeline(finding);
  const latestChange = latestFindingConclusionChange(finding);
  const visibleTimeline = compact ? timeline.slice(-4) : timeline;

  return (
    <section className="finding-verification-timeline" aria-label="真实验证历史时间线">
      <div className="finding-verification-timeline-head">
        <div>
          <span className="panel-kicker">真实验证历史</span>
          <h3>{latestChange ? `最近结论变化：${latestChange.transitionLabel}` : '当前尚无真实验证改变问题结论'}</h3>
        </div>
        <span className="summary-pill">{Math.max(0, timeline.length - 1)} 次修复后验证</span>
      </div>

      {compact && timeline.length > visibleTimeline.length && (
        <p className="settings-hint">仅显示最近 {visibleTimeline.length - 1} 次验证；完整历史可在问题清单或证据中心查看。</p>
      )}

      <ol className="verification-timeline-list">
        {visibleTimeline.map((event) => (
          <li key={event.key} className={`verification-timeline-item verification-${event.tone}`}>
            <span className="verification-timeline-marker" aria-hidden="true" />
            <div className="verification-timeline-content">
              <div className="verification-timeline-title-row">
                <div>
                  <span className="verification-timeline-mode">{runTitle(event)}</span>
                  <strong>{event.label}</strong>
                </div>
                <span className="verification-timeline-time">{event.generatedAt || '时间未上报'}</span>
              </div>

              <p>{event.detail}</p>

              <div className="verification-timeline-meta">
                <span>{event.transitionLabel}</span>
                {event.changedConclusion && <strong className="verification-change-badge">结论变化</strong>}
                {event.run?.regression_probe_id && <span>Probe {event.run.regression_probe_id}</span>}
                {event.run?.method && event.run?.path && <span>{event.run.method} {event.run.path}</span>}
                {event.run?.gate_status && <span>Gate {event.run.gate_status}</span>}
              </div>
            </div>
          </li>
        ))}
      </ol>

      {timeline.length === 1 && (
        <p className="settings-hint">当前只有原始 Finding 基线，后端尚未返回真实修复后验证历史。前端不会补造验证轮次。</p>
      )}
    </section>
  );
}

export default FindingVerificationTimeline;
