interface CoverageData {
  modeled_paths: number;
  executed_probes: number;
  confirmed_findings: number;
  evidence_completeness: number;
}

interface CoveragePanelProps { data: CoverageData }

export function CoveragePanel({ data }: CoveragePanelProps) {
  const modeledBase = Math.max(1, data.modeled_paths);
  const findingsBase = Math.max(1, Math.min(data.modeled_paths, 20));
  const items = [
    {
      label: '已建模行为路径',
      value: data.modeled_paths.toLocaleString(),
      pct: 100,
      full: true,
      hint: '作为本轮检测覆盖基线',
    },
    {
      label: '已验证行为',
      value: data.executed_probes.toLocaleString(),
      pct: Math.max(8, Math.min(100, Math.round((data.executed_probes / modeledBase) * 100))),
      full: data.executed_probes >= modeledBase * 0.8,
      hint: '已完成真实探测或回放验证',
    },
    {
      label: '确认风险',
      value: data.confirmed_findings.toString(),
      pct: Math.max(6, Math.min(100, Math.round((data.confirmed_findings / findingsBase) * 100))),
      full: data.confirmed_findings >= findingsBase * 0.6,
      hint: '已形成证据闭环的风险数',
    },
    {
      label: '证据完备度',
      value: `${data.evidence_completeness}%`,
      pct: Math.max(10, data.evidence_completeness),
      full: data.evidence_completeness >= 80,
      hint: '用于发布与验收评审',
    },
  ];

  return (
    <div className="coverage-panel">
      <div className="coverage-header">
        <div>
          <span className="panel-kicker">覆盖总览</span>
          <h2>行为空间覆盖</h2>
        </div>
        <div className="coverage-header-meta">仅统计已建模且可追溯的真实行为路径</div>
      </div>
      <div className="coverage-grid">
        {items.map(item => (
          <div className="cov-item" key={item.label}>
            <div className="cov-value">{item.value}</div>
            <div className="cov-label">{item.label}</div>
            <div className="cov-hint">{item.hint}</div>
            <div className={`cov-bar ${item.full ? 'full' : 'partial'}`} style={{ width: `${item.pct}%` }} />
          </div>
        ))}
      </div>
    </div>
  );
}
