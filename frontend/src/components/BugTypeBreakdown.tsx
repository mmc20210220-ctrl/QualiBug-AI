import type { Finding } from '../types';

interface LayerStat {
  layer: string;
  token: string;
  color: string;
  count: number;
  pct: number;
  description: string;
}

interface BugTypeBreakdownProps {
  findings: Finding[];
}

// defect_family → 展示元数据映射（通用，非业务概念）
const FAMILY_DISPLAY: Record<string, { token: string; color: string; description: string }> = {
  scenario_flow: { token: 'FLOW', color: '#d97706', description: '状态机、级联更新、事务边界' },
  api_contract: { token: 'API', color: '#5865f2', description: 'HTTP 端点契约、认证、错误处理' },
  security_boundary: { token: 'AUTH', color: '#dc2626', description: '身份验证、角色权限、越权检测' },
  privacy_compliance: { token: 'DATA', color: '#be123c', description: '敏感数据暴露、加密、脱敏' },
  data_integrity: { token: 'DB', color: '#0ea571', description: '数据一致性、约束、引用完整性' },
  performance: { token: 'PERF', color: '#0284c7', description: '响应时间、吞吐量、资源占用' },
  stability: { token: 'SVC', color: '#ef4444', description: '健康检查、超时、异步可观测性' },
  compatibility: { token: 'COMP', color: '#64748b', description: '版本兼容、向后兼容' },
  ui: { token: 'UI', color: '#0891b2', description: '前端交互、数据展示、输入校验' },
  uiux: { token: 'UX', color: '#8b5cf6', description: '交互体验、可用性、反馈' },
  accessibility_i18n: { token: 'I18N', color: '#4b5563', description: '可访问性、本地化、时区' },
  observability: { token: 'AUDIT', color: '#4b5563', description: '操作日志、审计追踪、合规证据' },
  configuration_drift: { token: 'CONF', color: '#6b7280', description: '环境配置、特性开关、密钥管理' },
};

const DEFAULT_DISPLAY = { token: 'OTHER', color: '#64748b', description: '其他类型缺陷' };

export function BugTypeBreakdown({ findings }: BugTypeBreakdownProps) {
  const total = findings.length || 1;

  // 直接用后端 defect_family 聚合，不做前端关键词匹配
  const familyCounts = new Map<string, number>();
  findings.forEach(f => {
    const family = f.defect_family || 'other';
    familyCounts.set(family, (familyCounts.get(family) || 0) + 1);
  });

  // 构建层级统计
  const layers: LayerStat[] = Array.from(familyCounts.entries()).map(([family, count]) => {
    const display = FAMILY_DISPLAY[family] || DEFAULT_DISPLAY;
    const label = findings.find(f => f.defect_family === family)?.defect_family_label || family;
    return {
      layer: label,
      token: display.token,
      color: display.color,
      count,
      pct: Math.round((count / total) * 100),
      description: display.description,
    };
  }).sort((a, b) => b.count - a.count);

  const activeLayers = layers.filter(l => l.count > 0);
  const displayLayers = activeLayers.length > 0 ? activeLayers : [];
  const totalFamilies = Object.keys(FAMILY_DISPLAY).length;

  return (
    <div className="coverage-panel">
      <div className="coverage-header">
        <div>
          <span className="panel-kicker">Distribution</span>
          <h2>缺陷层级分布</h2>
        </div>
        <span className="coverage-header-meta">覆盖 {activeLayers.length}/{totalFamilies} 个系统层级</span>
      </div>
      {displayLayers.length === 0 && (
        <div className="coverage-inline-tip">暂无缺陷分类数据，运行扫描后将自动生成分布。</div>
      )}
      <div className="layer-breakdown-grid">
        {displayLayers.map(l => (
          <div key={l.layer} className="layer-breakdown-card" style={{ '--accent': l.color } as React.CSSProperties}>
            <div className="layer-breakdown-head">
              <span className="layer-breakdown-token">{l.token}</span>
              <span className="layer-breakdown-title">{l.layer}</span>
            </div>
            <div className="layer-breakdown-metric">
              <span className="layer-breakdown-value" style={{ color: l.color }}>{l.count}</span>
              <span className="layer-breakdown-meta">个缺陷 · {l.pct}%</span>
            </div>
            <div className="layer-breakdown-track">
              <div className="layer-breakdown-bar" style={{ width: `${Math.max(2, l.pct)}%`, background: l.color }} />
            </div>
            <p className="layer-breakdown-desc">{l.description}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
