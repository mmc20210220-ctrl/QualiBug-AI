import { AnimatedCounter } from '../AnimatedCounter';
import { RiskRing, ReleaseLight } from './DashboardPrimitives';

interface ValueHeroProps {
  projectName: string;
  conclusion: string;
  headline: string;
  level: 'safe' | 'attention' | 'blocked';
  decision: { color: 'red' | 'yellow' | 'green'; label: string; advice: string };
  metrics: {
    confirmedDefects: number;
    p0Count: number;
    savedHours: number;
    modulesCount: number;
  };
  scanTime: string;
  evidenceTrust: number;
}

export function ValueHero({
  projectName,
  conclusion,
  headline,
  level,
  decision,
  metrics,
  scanTime,
  evidenceTrust,
}: ValueHeroProps) {
  return (
    <section className="value-hero">
      <span className="value-hero-eyebrow">{projectName}</span>
      <div className="value-hero-conclusion">
        <RiskRing level={level} />
        <div>
          <h1>{conclusion}</h1>
          <p>{headline}</p>
        </div>
      </div>
      <div className="value-hero-metrics">
        <div className="value-hero-metric">
          <AnimatedCounter value={metrics.confirmedDefects} className="" />
          <span>已确认问题</span>
        </div>
        <div className="value-hero-metric">
          <strong className={metrics.p0Count > 0 ? 'danger' : ''}>
            <AnimatedCounter value={metrics.p0Count} className="" />
          </strong>
          <span>P0 阻断发布</span>
        </div>
        <div className="value-hero-metric">
          <AnimatedCounter value={metrics.savedHours} className="" />
          <span>节省人工（小时）</span>
        </div>
        <div className="value-hero-metric">
          <AnimatedCounter value={metrics.modulesCount} className="" />
          <span>覆盖模块</span>
        </div>
      </div>
      <div className="value-hero-decision">
        <ReleaseLight color={decision.color} label={decision.label} advice={decision.advice} />
        <div style={{ fontSize: 12, color: 'rgba(255,255,255,.5)' }}>
          <span>最近检测 {scanTime}</span>
          {evidenceTrust > 0 && <span> · 结论可靠度 {evidenceTrust}%</span>}
        </div>
      </div>
    </section>
  );
}
