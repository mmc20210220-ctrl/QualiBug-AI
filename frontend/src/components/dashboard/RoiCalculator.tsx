import { AnimatedCounter } from '../AnimatedCounter';

interface RoiCalculatorProps {
  aiTestPoints: number;
  savedHours: number;
  p0Count: number;
  p1Count: number;
  modulesCount: number;
  totalModules: number;
}

function MiniRing({ percent, color, size = 56 }: { percent: number; color: string; size?: number }) {
  const r = (size - 10) / 2;
  const circ = 2 * Math.PI * r;
  const clamped = Math.min(100, Math.max(0, percent));
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="rgba(31,79,216,.08)" strokeWidth="6" />
      <circle
        cx={size / 2} cy={size / 2} r={r} fill="none"
        stroke={color} strokeWidth="6" strokeLinecap="round"
        strokeDasharray={`${circ * clamped / 100} ${circ}`}
        transform={`rotate(-90 ${size / 2} ${size / 2})`}
        style={{ transition: 'stroke-dasharray 1.2s ease' }}
      />
      <text x={size / 2} y={size / 2} textAnchor="middle" dominantBaseline="central"
        fill={color} fontSize="12" fontWeight="800">{clamped}%</text>
    </svg>
  );
}

export function RoiCalculator({
  aiTestPoints,
  savedHours,
  p0Count,
  p1Count,
  modulesCount,
  totalModules,
}: RoiCalculatorProps) {
  const savedDays = Math.max(1, Math.round(savedHours / 8));
  const riskInterceptCount = p0Count + p1Count;
  const riskValueWan = riskInterceptCount * 15; // 每个 P0/P1 拦截等效 15 万风险
  const coveragePercent = totalModules > 0 ? Math.round((modulesCount / totalModules) * 100) : (modulesCount > 0 ? 100 : 0);

  return (
    <section className="roi-section">
      <div className="roi-section-head">
        <div>
          <h2>价值量化</h2>
          <p>AI 检测为您节省的人力与拦截的风险</p>
        </div>
      </div>
      <div className="roi-cards">
        <div className="roi-card">
          <div className="roi-card-icon time">⏱</div>
          <strong><AnimatedCounter value={savedHours} /></strong>
          <span className="roi-card-label">等效人工小时</span>
          <span className="roi-card-hint">≈ {savedDays} 人天 · {aiTestPoints} 个测试点</span>
        </div>
        <div className="roi-card">
          <div className="roi-card-icon risk">🛡</div>
          <strong><AnimatedCounter value={riskInterceptCount} /></strong>
          <span className="roi-card-label">风险拦截</span>
          <span className="roi-card-hint">≈ {riskValueWan} 万风险敞口 · {p0Count} 个 P0</span>
        </div>
        <div className="roi-card">
          <div className="roi-card-icon coverage">📊</div>
          <MiniRing percent={coveragePercent} color="var(--success)" />
          <span className="roi-card-label">覆盖深度</span>
          <span className="roi-card-hint">{modulesCount}/{totalModules || modulesCount} 个业务模块</span>
        </div>
      </div>
      <div className="roi-comparison">
        <div className="roi-comparison-item">
          <strong>30 分钟</strong>
          <span>AI 自动检测</span>
        </div>
        <div className="roi-comparison-vs">vs</div>
        <div className="roi-comparison-item muted">
          <strong>{savedDays} 天</strong>
          <span>人工团队等效</span>
        </div>
      </div>
    </section>
  );
}
