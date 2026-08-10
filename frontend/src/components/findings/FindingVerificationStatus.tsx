import { deriveFindingVerification, type FindingVerificationState } from '../../lib/finding-verification';
import type { Finding } from '../../types';

type Props = {
  finding: Finding;
  compact?: boolean;
  prefix?: string;
};

export function verificationStatePriority(state: FindingVerificationState): number {
  switch (state) {
    case 'still_failing': return 5;
    case 'inconclusive': return 4;
    case 'pending': return 3;
    case 'not_enrolled': return 2;
    case 'verified_fixed': return 1;
    default: return 0;
  }
}

export function FindingVerificationStatus({ finding, compact = false, prefix = '验证' }: Props) {
  const presentation = deriveFindingVerification(finding);

  return (
    <span
      className={`finding-verification-status verification-${presentation.tone}`}
      data-verification-state={presentation.state}
      title={presentation.detail}
      aria-label={`${prefix}：${presentation.label}`}
    >
      {!compact && <span className="finding-verification-prefix">{prefix}</span>}
      <strong>{presentation.label}</strong>
    </span>
  );
}

export default FindingVerificationStatus;
