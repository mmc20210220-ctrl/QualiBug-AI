import {
  buildFindingVerificationTimeline,
  deriveFocusedVerificationRunSummary,
  type FindingVerificationOutcome,
  type FocusedVerificationRunSummary,
} from './finding-verification';
import type { Finding } from '../types';

export type FindingVerificationFocusContext = {
  summary: FocusedVerificationRunSummary;
  isLatestRun: boolean;
  latestGeneratedAt: string;
  latestOutcome: FindingVerificationOutcome | null;
  latestLabel: string;
};

export function deriveFindingVerificationFocusContext(
  finding: Finding,
  generatedAt: string,
): FindingVerificationFocusContext | null {
  const normalizedGeneratedAt = String(generatedAt || '').trim();
  if (!normalizedGeneratedAt) return null;

  const summary = deriveFocusedVerificationRunSummary(finding, normalizedGeneratedAt);
  if (!summary) return null;

  const verificationEvents = buildFindingVerificationTimeline(finding)
    .filter((event) => event.kind === 'verification');
  const latestEvent = verificationEvents[verificationEvents.length - 1] || null;

  return {
    summary,
    isLatestRun: Boolean(latestEvent && latestEvent.key === summary.event.key),
    latestGeneratedAt: latestEvent?.generatedAt || '',
    latestOutcome: latestEvent?.outcome || null,
    latestLabel: latestEvent?.label || '尚无最新真实验证回执',
  };
}
