import { AnimatedCounter } from './AnimatedCounter';

interface CommercialValue {
  executive_message: string;
  ai_equivalent_test_points: number;
  evidence_trust_score: number;
  explored_behavior_paths: number;
  blocked_risk_count: number;
  capability_families: number;
  bug_families: number;
  decision_cards: Array<{
    role: string;
    title: string;
    value: string;
    detail: string;
  }>;
}

interface ValueDashboardProps {
  value: CommercialValue | null | undefined;
}

function MiniRing({ percent, color, size = 52 }: { percent: number; color: string; size?: number }) {
  const r = (size - 8) / 2;
  const circumference = 2 * Math.PI * r;
  const clamped = Math.min(100, Math.max(0, percent));
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="mini-ring">
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="rgba(31,79,216,.08)" strokeWidth="5" />
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color} strokeWidth="5" strokeLinecap="round" strokeDasharray={`${circumference * clamped / 100} ${circumference}`} transform={`rotate(-90 ${size / 2} ${size / 2})`} style={{ transition: 'stroke-dasharray 1.2s ease' }} />
      <text x={size / 2} y={size / 2} textAnchor="middle" dominantBaseline="central" fill={color} fontSize="11" fontWeight="800">{clamped}%</text>
    </svg>
  );
}

export function ValueDashboard({ value }: ValueDashboardProps) {
  if (!value) return null;

  const savedHours = Math.round((value.ai_equivalent_test_points || 0) * 0.5);
  const savedDays = Math.max(1, Math.round(savedHours / 8));
  const riskInterceptValue = (value.blocked_risk_count || 0) * 15; // 每个P0/P1拦截等效15万风险

  const metrics = [
    { label: '等效测试点', value: value.ai_equivalent_test_points || 0, hint: `相当于人工 ${savedHours} 小时`, animated: true },
    { label: '结论可靠度', value: value.evidence_trust_score || 0, hint: '用于上线评审与验收', ring: true },
    { label: '覆盖路径', value: value.explored_behavior_paths || 0, hint: '接口、状态、权限、边界', animated: true },
    { label: '风险拦截', value: value.blocked_risk_count || 0, hint: 'P0/P1 优先进入修复', animated: true },
  ];

  return (
    <section className="commercial-value-panel mb-4">
      <div className="commercial-value-head">
        <div>
          <span className="commercial-eyebrow">价值量化</span>
          <h2>AI 检测为您创造的价值</h2>
          <p>{value.executive_message}</p>
        </div>
        <div className="commercial-proof">
          <strong>{savedDays} 天</strong>
          <span>等效人工节省</span>
        </div>
      </div>

      <div className="commercial-metrics">
        {metrics.map((metric) => (
          <div key={metric.label} className="commercial-metric">
            {metric.ring ? (
              <MiniRing percent={metric.value} color="var(--primary)" />
            ) : (
              <strong className="commercial-metric-number">
                {metric.animated ? <AnimatedCounter value={metric.value} /> : metric.value.toLocaleString()}
              </strong>
            )}
            <span>{metric.label}</span>
            <small>{metric.hint}</small>
          </div>
        ))}
      </div>

      <div className="commercial-comparison">
        <div className="commercial-comparison-item">
          <span className="commercial-comparison-label">AI 检测</span>
          <strong>30 分钟</strong>
          <small>自动执行、自动验证、自动出报告</small>
        </div>
        <div className="commercial-comparison-vs">vs</div>
        <div className="commercial-comparison-item muted">
          <span className="commercial-comparison-label">人工团队</span>
          <strong>{savedDays} 天</strong>
          <small>需要协调人员、编写用例、逐条执行、整理报告</small>
        </div>
      </div>

      {value.decision_cards && value.decision_cards.length > 0 && (
        <div className="commercial-decision-grid">
          {value.decision_cards.map((card) => (
            <article key={card.role} className="commercial-decision-card">
              <span>{card.role}</span>
              <h3>{card.title}</h3>
              <strong>{card.value}</strong>
              <p>{card.detail}</p>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}

export default ValueDashboard;
