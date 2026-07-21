/**
 * Dashboard primitive UI components.
 * Extracted from Dashboard.tsx for better maintainability.
 */
import type { ReactNode } from 'react';

export function Skeleton({ h = 20, w = '100%', br = 4, className = '' }: { h?: number; w?: string | number; br?: number; className?: string }) {
  return <div className={`skeleton-block${className ? ` ${className}` : ''}`} style={{ height: h, width: w, borderRadius: br }} />;
}

export function StatePanel({ eyebrow, title, description, action }: { eyebrow: string; title: string; description: string; action?: ReactNode }) {
  return (
    <section className="state-panel">
      <div className="state-panel-badge">{eyebrow}</div>
      <h2>{title}</h2>
      <p>{description}</p>
      {action ? <div className="state-panel-actions">{action}</div> : null}
    </section>
  );
}

export function RiskRing({ level, size = 88 }: { level: 'safe' | 'attention' | 'blocked'; size?: number }) {
  const colors = { safe: '#0c9a6a', attention: '#c9780a', blocked: '#d91f45' };
  const labels = { safe: '安全', attention: '关注', blocked: '阻断' };
  const color = colors[level];
  const r = (size - 12) / 2;
  const circ = 2 * Math.PI * r;
  const progress = level === 'safe' ? 1 : level === 'attention' ? 0.6 : 0.3;
  return (
    <div className="risk-ring" style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="rgba(255,255,255,0.1)" strokeWidth="7" />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={color}
          strokeWidth="7"
          strokeLinecap="round"
          strokeDasharray={`${circ * progress} ${circ}`}
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
          style={{ transition: 'stroke-dasharray 1s ease' }}
        />
      </svg>
      <span className="risk-ring-label" style={{ color }}>{labels[level]}</span>
    </div>
  );
}

export function ReleaseLight({ color, label, advice }: { color: 'red' | 'yellow' | 'green'; label: string; advice: string }) {
  const cm = { red: '#d91f45', yellow: '#c9780a', green: '#0c9a6a' };
  return (
    <div className="release-light">
      <div className="release-light-indicator" style={{ background: cm[color], boxShadow: `0 0 20px ${cm[color]}66` }} />
      <strong>{label}</strong>
      <p>{advice}</p>
    </div>
  );
}
