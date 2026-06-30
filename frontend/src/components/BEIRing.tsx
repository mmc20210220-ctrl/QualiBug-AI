interface BEIRingProps { score: number; size?: number }

export function BEIRing({ score, size = 140 }: BEIRingProps) {
  const strokeWidth = 10;
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (score / 100) * circumference;
  const cx = size / 2, cy = size / 2;

  const getColor = () => {
    if (score >= 80) return ['#5865f2', '#22d3bb'];
    if (score >= 60) return ['#d97706', '#fbbf24'];
    return ['#e02449', '#f87171'];
  };
  const [c1, c2] = getColor();
  const gradId = `beiGrad-${score}`;

  return (
    <div className="bei-ring" style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        <defs>
          <linearGradient id={gradId} x1="0%" y1="0%" x2="100%" y2="0%">
            <stop offset="0%" stopColor={c1} />
            <stop offset="100%" stopColor={c2} />
          </linearGradient>
        </defs>
        <circle className="bei-ring-bg" cx={cx} cy={cy} r={radius} />
        <circle
          className="bei-ring-fill bei-ring-animate"
          cx={cx} cy={cy} r={radius}
          stroke={`url(#${gradId})`}
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          style={{ '--ring-offset': `${offset}px` } as React.CSSProperties}
        />
      </svg>
      <div className="bei-value">
        <strong>{score}</strong>
        <label>BEI</label>
      </div>
    </div>
  );
}
