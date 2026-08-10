import type { Finding } from '../types';

export function evidenceScoreLabel(finding: Finding): string {
  const score = finding.evidence_quality?.score;
  return typeof score === 'number' && Number.isFinite(score)
    ? `${score}/100`
    : '未评分';
}

export function evidenceDeepLinkSearch(findingId: string): string {
  const params = new URLSearchParams();
  const normalized = String(findingId || '').trim();
  if (normalized) params.set('finding', normalized);
  const search = params.toString();
  return search ? `?${search}` : '';
}
