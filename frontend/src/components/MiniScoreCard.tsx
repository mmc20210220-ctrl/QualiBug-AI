interface MiniScoreCardProps {
  label: string; value: number | string; unit?: string; description: string;
  color: 'warning' | 'success' | 'primary'; icon: string;
}

export function MiniScoreCard({ label, value, unit, description, color, icon }: MiniScoreCardProps) {
  const colorMap = {
    warning: { fg: '#d97706' },
    success: { fg: '#0ea571' },
    primary: { fg: '#5865f2' },
  };
  const c = colorMap[color];

  return (
    <div className="mini-card">
      <div className={`mini-icon ${color === 'warning' ? 'amber' : 'green'}`}>{icon}</div>
      <div className="mini-info">
        <strong>{label}</strong>
        <p>{description}</p>
      </div>
      <div className="mini-value" style={{ color: c.fg }}>
        {value}{unit && <small>{unit}</small>}
      </div>
    </div>
  );
}
