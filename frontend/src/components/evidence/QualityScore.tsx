import type { Finding } from '../../types';
import { evidenceScoreLabel } from '../../lib/evidence-presentation';

interface QualityScoreProps {
  finding: Finding;
}

export function QualityScore({ finding }: QualityScoreProps) {
  const quality = finding.evidence_quality;
  if (!quality) return null;

  const rawScore = quality.score;
  const hasScore = typeof rawScore === 'number' && Number.isFinite(rawScore);
  const score = hasScore ? Math.max(0, Math.min(100, rawScore)) : 0;
  const color = !hasScore
    ? 'var(--subtle)'
    : score >= 80
      ? 'var(--success)'
      : score >= 50
        ? 'var(--warning)'
        : 'var(--danger)';
  const r = 22;
  const circ = 2 * Math.PI * r;

  return (
    <div className="quality-score">
      <div className="quality-score-ring" aria-label={hasScore ? `证据质量评分 ${score}/100` : '证据质量评分未上报'}>
        <svg width={56} height={56} viewBox="0 0 56 56" aria-hidden="true">
          <circle cx={28} cy={28} r={r} fill="none" stroke="rgba(31,79,216,.08)" strokeWidth="5" />
          <circle
            cx={28} cy={28} r={r} fill="none"
            stroke={color} strokeWidth="5" strokeLinecap="round"
            strokeDasharray={`${hasScore ? circ * score / 100 : 0} ${circ}`}
            transform="rotate(-90 28 28)"
          />
          <text x={28} y={28} textAnchor="middle" dominantBaseline="central" fill={color} fontSize="13" fontWeight="800">{hasScore ? score : '—'}</text>
        </svg>
      </div>
      <div className="quality-score-info">
        <h4>{quality.label || '证据质量'}</h4>
        <p>{quality.summary || `评分 ${evidenceScoreLabel(finding)}`}</p>
        <div className="quality-dimensions">
          {(quality.verified || []).map((v) => <span key={v} className="quality-dim">{v}</span>)}
          {(quality.missing || []).map((m) => <span key={m} className="quality-dim missing">{m}</span>)}
        </div>
      </div>
    </div>
  );
}
