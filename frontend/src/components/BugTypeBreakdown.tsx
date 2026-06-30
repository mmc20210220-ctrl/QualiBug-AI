import type { Finding } from '../types';

interface LayerStat {
  layer: string;
  icon: string;
  color: string;
  count: number;
  pct: number;
  description: string;
}

interface BugTypeBreakdownProps {
  findings: Finding[];
}

export function BugTypeBreakdown({ findings }: BugTypeBreakdownProps) {
  const total = findings.length || 1;

  const layers: LayerStat[] = [
    {
      layer: 'API 接口',
      icon: '🔌',
      color: '#5865f2',
      count: 0,
      pct: 0,
      description: 'HTTP 端点契约、认证、幂等、错误处理',
    },
    {
      layer: '数据库',
      icon: '🗄️',
      color: '#0ea571',
      count: 0,
      pct: 0,
      description: '数据一致性、约束、引用完整性',
    },
    {
      layer: '业务流程',
      icon: '🔄',
      color: '#d97706',
      count: 0,
      pct: 0,
      description: '状态机、级联更新、事务边界',
    },
    {
      layer: '业务规则',
      icon: '📋',
      color: '#8b5cf6',
      count: 0,
      pct: 0,
      description: 'PRD 与实现偏差、约束校验',
    },
    {
      layer: '规范结构',
      icon: '📐',
      color: '#64748b',
      count: 0,
      pct: 0,
      description: 'OpenAPI 规范、operationId、Schema',
    },
    {
      layer: '服务可用性',
      icon: '🟢',
      color: '#ef4444',
      count: 0,
      pct: 0,
      description: '健康检查、超时、异步可观测性',
    },
    {
      layer: 'UI 界面',
      icon: '🖥️',
      color: '#0891b2',
      count: 0,
      pct: 0,
      description: '前端交互、数据展示、输入校验',
    },
    {
      layer: '认证授权',
      icon: '🔐',
      color: '#dc2626',
      count: 0,
      pct: 0,
      description: '身份验证、角色权限、越权检测',
    },
    {
      layer: '数据安全',
      icon: '🛡️',
      color: '#be123c',
      count: 0,
      pct: 0,
      description: '敏感数据暴露、加密、脱敏',
    },
    {
      layer: '日志审计',
      icon: '📝',
      color: '#4b5563',
      count: 0,
      pct: 0,
      description: '操作日志、审计追踪、合规证据',
    },
    {
      layer: '配置管理',
      icon: '⚙️',
      color: '#6b7280',
      count: 0,
      pct: 0,
      description: '环境配置、特性开关、密钥管理',
    },
    {
      layer: '集成对接',
      icon: '🔗',
      color: '#7c3aed',
      count: 0,
      pct: 0,
      description: '外部系统集成、Webhook、消息队列',
    },
    {
      layer: '并发安全',
      icon: '⚡',
      color: '#ea580c',
      count: 0,
      pct: 0,
      description: '竞态条件、死锁、原子性、幂等',
    },
    {
      layer: '安全防护',
      icon: '🚨',
      color: '#b91c1c',
      count: 0,
      pct: 0,
      description: '注入攻击、XSS、CSRF、输入校验',
    },
    {
      layer: '性能',
      icon: '⏱️',
      color: '#0284c7',
      count: 0,
      pct: 0,
      description: '响应时间、吞吐量、资源占用、内存泄漏',
    },
  ];

  // Classify each finding into a layer
  // Indices: 0=API, 1=DB, 2=业务流程, 3=业务规则, 4=规范结构, 5=服务可用性, 6=UI, 7=认证授权, 8=数据安全, 9=日志审计, 10=配置管理, 11=集成对接, 12=并发安全, 13=安全防护, 14=性能
  findings.forEach(f => {
    const t = f.title.toLowerCase();
    const c = f.evidence_chain.map(s => (s.content + s.detail).toLowerCase()).join(' ');

    if (t.includes('db verified') || t.includes('库存') || t.includes('bom') || c.includes('db_verified') || c.includes('sql')) {
      layers[1].count++; // 数据库
    } else if (t.includes('幂等') || t.includes('idempotenc') || t.includes('replay') || t.includes('竞态') || t.includes('死锁') || t.includes('原子') || t.includes('并发')) {
      layers[12].count++; // 并发安全
    } else if (t.includes('auth') || t.includes('鉴权') || t.includes('认证') || t.includes('401') || t.includes('403') || t.includes('角色') || t.includes('权限') || t.includes('越权')) {
      layers[7].count++; // 认证授权
    } else if (t.includes('注入') || t.includes('xss') || t.includes('csrf') || t.includes('攻击')) {
      layers[13].count++; // 安全防护
    } else if (t.includes('泄露') || t.includes('暴露') || t.includes('敏感') || t.includes('加密') || t.includes('脱敏') || t.includes('password') || t.includes('api_key')) {
      layers[8].count++; // 数据安全
    } else if (t.includes('性能') || t.includes('响应') || t.includes('吞吐') || t.includes('内存') || t.includes('timeout') && !t.includes('async')) {
      layers[14].count++; // 性能
    } else if (t.includes('日志') || t.includes('审计') || t.includes('audit') || t.includes('追踪')) {
      layers[9].count++; // 日志审计
    } else if (t.includes('配置') || t.includes('环境') || t.includes('密钥') || t.includes('config') || t.includes('env')) {
      layers[10].count++; // 配置管理
    } else if (t.includes('集成') || t.includes('webhook') || t.includes('消息') || t.includes('队列') || t.includes('connector')) {
      layers[11].count++; // 集成对接
    } else if (t.includes('operationid') || t.includes('spec') || t.includes('openapi')) {
      layers[4].count++; // 规范结构
    } else if (t.includes('health') || t.includes('unreachable') || t.includes('async') || t.includes('timeout')) {
      layers[5].count++; // 服务可用性
    } else if (t.includes('级联') || t.includes('cascade') || t.includes('状态') || t.includes('事务')) {
      layers[2].count++; // 业务流程
    } else if (t.includes('prd') || t.includes('规则') || t.includes('contract') || (t.includes('应') && t.includes('返回'))) {
      layers[3].count++; // 业务规则
    } else {
      layers[0].count++; // API 接口 (default)
    }
  });

  // Compute percentages
  layers.forEach(l => { l.pct = Math.round((l.count / total) * 100); });

  return (
    <div className="coverage-panel">
      <div className="coverage-header">
        <h2>缺陷层级分布</h2>
        <span style={{ fontSize: 11, color: 'var(--muted)' }}>覆盖 {layers.filter(l => l.count > 0).length}/{layers.length} 个系统层级</span>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12 }}>
        {layers.map(l => (
          <div key={l.layer} className="stat-card" style={{ '--accent': l.color, textAlign: 'left', padding: '16px 18px' } as React.CSSProperties}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
              <span style={{ fontSize: 20 }}>{l.icon}</span>
              <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--ink)' }}>{l.layer}</span>
            </div>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, marginBottom: 6 }}>
              <span style={{ fontSize: 28, fontWeight: 900, color: l.color }}>{l.count}</span>
              <span style={{ fontSize: 12, color: 'var(--muted)' }}>个缺陷 · {l.pct}%</span>
            </div>
            <div style={{ height: 4, background: '#f1f5f9', borderRadius: 2, marginBottom: 8 }}>
              <div style={{ height: '100%', width: `${Math.max(2, l.pct)}%`, background: l.color, borderRadius: 2, transition: 'width 1s ease' }} />
            </div>
            <p style={{ fontSize: 11, color: 'var(--muted)', lineHeight: 1.4 }}>{l.description}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
