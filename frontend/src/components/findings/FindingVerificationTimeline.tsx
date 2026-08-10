import { useEffect, useRef } from 'react';
import {
  buildFindingVerificationTimeline,
  latestFindingConclusionChange,
} from '../../lib/finding-verification';
import type { Finding } from '../../types';

type Props = {
  finding: Finding;
  compact?: boolean;
  focusGeneratedAt?: string;
};

function runTitle(event: ReturnType<typeof buildFindingVerificationTimeline>[number]): string {
  if (event.kind === 'baseline') return '原始 Finding';
  const mode = event.run?.suite_mode_label || event.run?.suite_mode || '修复后验证';
  return mode;
}

export function FindingVerificationTimeline({ finding, compact = false, focusGeneratedAt = '' }: Props) {
  const timeline = buildFindingVerificationTimeline(finding);
  const latestChange = latestFindingConclusionChange(finding);
  const normalizedFocus = String(focusGeneratedAt || '').trim();
  const focusedEvent = normalizedFocus
    ? timeline.find((event) => event.kind === 'verification' && event.generatedAt === normalizedFocus) || null
    : null;
  const focusedRef = useRef<HTMLLIElement | null>(null);

  const hasCollapsedHistory = compact && timeline.length > 4;
  let visibleTimeline = timeline;
  if (hasCollapsedHistory) {
    const baseline = timeline[0];
    const recent = timeline.slice(-3);
    if (focusedEvent && !recent.some((event) => event.key === focusedEvent.key)) {
      const recentWithoutOldest = timeline.slice(-2);
      const focusedAndRecent = [focusedEvent, ...recentWithoutOldest]
        .filter((event, index, all) => all.findIndex((candidate) => candidate.key === event.key) === index)
        .sort((left, right) => left.generatedAt.localeCompare(right.generatedAt));
      visibleTimeline = [baseline, ...focusedAndRecent];
    } else {
      visibleTimeline = [baseline, ...recent];
    }
  }
  const collapsedCount = hasCollapsedHistory ? Math.max(0, timeline.length - visibleTimeline.length) : 0;

  useEffect(() => {
    if (!normalizedFocus || !focusedEvent || !focusedRef.current) return;
    focusedRef.current.scrollIntoView({ behavior: 'smooth', block: 'center' });
    focusedRef.current.focus({ preventScroll: true });
  }, [finding.id, focusedEvent?.key, normalizedFocus]);

  return (
    <section className="finding-verification-timeline" aria-label="真实验证历史时间线">
      <div className="finding-verification-timeline-head">
        <div>
          <span className="panel-kicker">真实验证历史</span>
          <h3>{latestChange ? `最近结论变化：${latestChange.transitionLabel}` : '当前尚无真实验证改变问题结论'}</h3>
        </div>
        <span className="summary-pill">{Math.max(0, timeline.length - 1)} 次修复后验证</span>
      </div>

      {normalizedFocus && focusedEvent && (
        <p className="verification-focus-hint">已定位到指定真实验证：{focusedEvent.generatedAt} · {focusedEvent.transitionLabel}</p>
      )}
      {normalizedFocus && !focusedEvent && (
        <p className="verification-focus-hint warning">指定验证轮次 {normalizedFocus} 不在当前 Finding 的真实 history 中；不会用其他轮次替代。</p>
      )}

      {hasCollapsedHistory && (
        <p className="settings-hint">
          {focusedEvent
            ? `紧凑视图保留原始 Finding 基线、指定验证轮次和最近验证；中间 ${collapsedCount} 次已折叠。`
            : `保留原始 Finding 基线和最近 3 次真实验证；中间 ${collapsedCount} 次已折叠，完整历史可在问题清单或证据中心查看。`}
        </p>
      )}

      <ol className="verification-timeline-list">
        {visibleTimeline.map((event) => {
          const isFocused = Boolean(normalizedFocus && event.kind === 'verification' && event.generatedAt === normalizedFocus);
          return (
            <li
              key={event.key}
              ref={isFocused ? focusedRef : undefined}
              tabIndex={isFocused ? -1 : undefined}
              className={`verification-timeline-item verification-${event.tone}${isFocused ? ' verification-focused' : ''}`}
            >
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
                  {isFocused && <strong className="verification-focus-badge">当前定位</strong>}
                  {event.changedConclusion && <strong className="verification-change-badge">结论变化</strong>}
                  {event.run?.regression_probe_id && <span>Probe {event.run.regression_probe_id}</span>}
                  {event.run?.method && event.run?.path && <span>{event.run.method} {event.run.path}</span>}
                  {event.run?.gate_status && <span>Gate {event.run.gate_status}</span>}
                </div>
              </div>
            </li>
          );
        })}
      </ol>

      {timeline.length === 1 && (
        <p className="settings-hint">当前只有原始 Finding 基线，后端尚未返回真实修复后验证历史。前端不会补造验证轮次。</p>
      )}
    </section>
  );
}

export default FindingVerificationTimeline;
