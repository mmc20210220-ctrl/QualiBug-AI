interface CoverageData {
  modeled_paths: number;
  executed_probes: number;
  confirmed_findings: number;
  evidence_completeness: number;
}

interface CoveragePanelProps { data: CoverageData }

export function CoveragePanel({ data }: CoveragePanelProps) {
  const items = [
    { label: '已建模行为路径', value: data.modeled_paths.toLocaleString(), pct: 100, full: true },
    { label: '已验证行为', value: data.executed_probes.toLocaleString(), pct: Math.min(100, Math.round((data.executed_probes / Math.max(1, data.modeled_paths)) * 400)), full: data.executed_probes > 1000 },
    { label: '确认风险', value: data.confirmed_findings.toString(), pct: Math.min(100, data.confirmed_findings * 10), full: false },
    { label: '证据完备度', value: `${data.evidence_completeness}%`, pct: data.evidence_completeness, full: data.evidence_completeness >= 80 },
  ];

  return (
    <div className="coverage-panel">
      <div className="coverage-header"><h2>行为空间覆盖</h2></div>
      <div className="coverage-grid">
        {items.map(item => (
          <div className="cov-item" key={item.label}>
            <div className="cov-value">{item.value}</div>
            <div className="cov-label">{item.label}</div>
            <div className={`cov-bar ${item.full ? 'full' : 'partial'}`} style={{ width: `${item.pct}%` }} />
          </div>
        ))}
      </div>
    </div>
  );
}
