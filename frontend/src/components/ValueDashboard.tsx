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

export function ValueDashboard({ value }: ValueDashboardProps) {
  if (!value) return null;

  const metrics = [
    { label: '验证覆盖点', value: value.ai_equivalent_test_points?.toLocaleString() || '0', hint: '把人工测试与规则评审沉淀为可复用检查点' },
    { label: '证据可信度', value: `${value.evidence_trust_score ?? 0}%`, hint: '用于上线评审、客户验收和责任闭环' },
    { label: '已覆盖路径', value: value.explored_behavior_paths?.toLocaleString() || '0', hint: '覆盖接口、状态、权限、数据一致性与边界场景' },
    { label: '高优先级风险', value: String(value.blocked_risk_count ?? 0), hint: 'P0/P1 风险优先进入修复与发布决策' },
  ];

  return (
    <section className="commercial-value-panel mb-4">
      <div className="commercial-value-head">
        <div>
          <span className="commercial-eyebrow">价值证明</span>
          <h2>价值证据面板</h2>
          <p>{value.executive_message}</p>
        </div>
        <div className="commercial-proof">
          <strong>{value.bug_families || value.capability_families || '持续'}</strong>
          <span>{value.bug_families ? '缺陷族覆盖' : value.capability_families ? '能力族覆盖' : '知识资产复用'}</span>
        </div>
      </div>

      <div className="commercial-metrics">
        {metrics.map((metric) => (
          <div key={metric.label} className="commercial-metric">
            <strong>{metric.value}</strong>
            <span>{metric.label}</span>
            <small>{metric.hint}</small>
          </div>
        ))}
      </div>

      <div className="commercial-decision-grid">
        {value.decision_cards?.map((card) => (
          <article key={card.role} className="commercial-decision-card">
            <span>{card.role}</span>
            <h3>{card.title}</h3>
            <strong>{card.value}</strong>
            <p>{card.detail}</p>
          </article>
        ))}
      </div>
    </section>
  );
}

export default ValueDashboard;
