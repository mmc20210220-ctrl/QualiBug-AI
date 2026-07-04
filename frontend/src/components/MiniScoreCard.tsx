interface MiniScoreCardProps {
  label: string; value: number | string; unit?: string; description: string;
  color: 'warning' | 'success' | 'primary'; icon: string;
}

export function MiniScoreCard({ label, value, unit, description, color, icon }: MiniScoreCardProps) {
  return (
    <div className={`mini-card tone-${color}`}>
      <div className={`mini-icon tone-${color}`}>{icon}</div>
      <div className="mini-info">
        <strong>{label}</strong>
        <p>{description}</p>
      </div>
      <div className="mini-value">
        {value}{unit && <small>{unit}</small>}
      </div>
    </div>
  );
}
