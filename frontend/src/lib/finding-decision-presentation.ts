import { deriveFindingVerification } from './finding-verification';
import type { Finding } from '../types';

export type FindingDecisionPresentation = {
  impact: string;
  basis: string;
  evidenceLabel: string;
  evidenceDetail: string;
  reproductionLabel: string;
  latestVerificationAt: string;
  nextActionLabel: string;
  nextActionDetail: string;
};

function firstText(...values: unknown[]): string {
  for (const value of values) {
    const text = String(value || '').trim();
    if (text) return text;
  }
  return '';
}

export function deriveFindingDecisionPresentation(finding: Finding): FindingDecisionPresentation {
  const verification = deriveFindingVerification(finding);
  const evidenceCount = finding.evidence_chain?.length || 0;
  const expected = firstText(finding.expected, finding.expected_actual_comparison?.expected, '预期行为未单独上报');
  const actual = firstText(finding.actual, finding.expected_actual_comparison?.actual, '实际行为未单独上报');
  const difference = firstText(finding.expected_actual_comparison?.difference);
  const evidenceSummary = firstText(finding.evidence_quality?.summary);
  const reproductionLabel = finding.proof?.repro_rate != null
    ? `复现率 ${finding.proof.repro_rate}%`
    : finding.reproducibility_count > 0
      ? `已记录 ${finding.reproducibility_count} 次复现`
      : '复现次数未上报';

  return {
    impact: firstText(
      finding.business_summary,
      finding.business_impact?.summary,
      finding.actual,
      '该 Finding 已形成真实问题结论。',
    ),
    basis: difference || `预期：${expected}；实际：${actual}`,
    evidenceLabel: firstText(finding.evidence_quality?.label, '证据质量未评分'),
    evidenceDetail: [
      `${evidenceCount} 条证据链`,
      evidenceSummary,
      reproductionLabel,
    ].filter(Boolean).join(' · '),
    reproductionLabel,
    latestVerificationAt: firstText(
      verification.latestRun?.generated_at,
      finding.regression?.last_run_at,
      '尚未执行修复后验证',
    ),
    nextActionLabel: verification.nextActionLabel,
    nextActionDetail: verification.detail,
  };
}
