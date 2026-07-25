import type { Finding } from '../../types';

interface QualityScoreProps {
  finding: Finding;
}

export function QualityScore({ finding }: QualityScoreProps) {
  const quality = finding.evidence_quality;
  if (!quality) return null;

  const score = quality.score ?? 0;
  const color = score >= 80 ? 'var(--success)' : score >= 50 ? 'var(--warning)' : 'var(--danger)';
  const r = 22;
  const circ = 2 * Math.PI * r;

  return (
    <div className="quality-score">
      <div className="quality-score-ring">
        <svg width={56} height={56} viewBox="0 0 56 56">
          <circle cx={28} cy={28} r={r} fill="none" stroke="rgba(31,79,216,.08)" strokeWidth="5" />
          <circle
            cx={28} cy={28} r={r} fill="none"
            stroke={color} strokeWidth="5" strokeLinecap="round"
            strokeDasharray={`${circ * score / 100} ${circ}`}
            transform="rotate(-90 28 28)"
          />
          <text x={28} y={28} textAnchor="middle" dominantBaseline="central" fill={color} fontSize="13" fontWeight="800">{score}</text>
        </svg>
      </div>
      <div className="quality-score-info">
        <h4>{quality.label || '证据质量'}</h4>
        <p>{quality.summary || `评分 ${score}/100`}</p>
        <div className="quality-dimensions">
          {(quality.verified || []).map((v) => <span key={v} className="quality-dim">{v}</span>)}
          {(quality.missing || []).map((m) => <span key={m} className="quality-dim missing">{m}</span>)}
        </div>
      </div>
    </div>
  );
}
