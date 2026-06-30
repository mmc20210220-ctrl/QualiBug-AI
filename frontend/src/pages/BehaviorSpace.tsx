import { useSearchParams } from 'react-router-dom';

type BehaviorType = 'API' | '数据库' | '文档' | '业务流程';

interface BehaviorItem {
  type: BehaviorType;
  identifier: string;    // API path, table name, doc section, process step
  detail: string;        // method, column, source file, trigger
  tested: boolean;
  findings: number;
}

const behaviors: BehaviorItem[] = [
  // API
  { type: 'API', identifier: '/api/orders', detail: 'POST', tested: true, findings: 3 },
  { type: 'API', identifier: '/api/orders', detail: 'GET', tested: true, findings: 2 },
  { type: 'API', identifier: '/api/materials', detail: 'POST', tested: true, findings: 1 },
  { type: 'API', identifier: '/api/warehouse/inventory', detail: 'GET', tested: true, findings: 1 },
  { type: 'API', identifier: '/api/production/orders', detail: 'GET', tested: true, findings: 0 },
  { type: 'API', identifier: '/api/warehouse/transactions', detail: 'POST', tested: false, findings: 0 },
  { type: 'API', identifier: '/api/quality/inspections', detail: 'GET', tested: true, findings: 0 },
  { type: 'API', identifier: '/api/production/schedules', detail: 'POST', tested: false, findings: 0 },
  // 数据库
  { type: '数据库', identifier: 'inventory', detail: 'qty_available < 0', tested: true, findings: 2 },
  { type: '数据库', identifier: 'bom_items', detail: 'material_id FK 失效', tested: true, findings: 1 },
  { type: '数据库', identifier: 'production_orders', detail: 'completed_qty > plan_qty', tested: true, findings: 1 },
  { type: '数据库', identifier: 'inventory_transactions', detail: '重复流水记录', tested: true, findings: 1 },
  // 文档
  { type: '文档', identifier: 'PRD §3.2 订单幂等', detail: 'PRD_v2.1.md', tested: true, findings: 1 },
  { type: '文档', identifier: 'PRD §5.1 库存鉴权', detail: 'PRD_v2.1.md', tested: true, findings: 1 },
  { type: '文档', identifier: 'OpenAPI securitySchemes', detail: 'openapi_mes_v3.yaml', tested: true, findings: 0 },
  // 业务流程
  { type: '业务流程', identifier: '订单创建→库存扣减', detail: '幂等性校验', tested: false, findings: 0 },
  { type: '业务流程', identifier: '物料停用→BOM级联', detail: '级联更新', tested: true, findings: 1 },
  { type: '业务流程', identifier: '报工→完工入库', detail: '数量校验', tested: false, findings: 0 },
];

const typeStyle: Record<string, string> = {
  'API': 'background:var(--primary-muted);color:var(--primary)',
  '数据库': 'background:var(--success-muted);color:var(--success)',
  '文档': 'background:var(--warning-muted);color:var(--warning)',
  '业务流程': 'background:var(--danger-muted);color:var(--danger)',
};

export function BehaviorSpace() {
  const [params] = useSearchParams();
  const project = params.get('project') || 'real_project_demo';
  const covered = behaviors.filter(r => r.tested).length;
  const totalFindings = behaviors.reduce((s, r) => s + r.findings, 0);
  const pct = Math.round((covered / behaviors.length) * 100);

  return (
    <div>
      <div className="page-header"><div><h1>行为空间</h1><p>企业系统行为建模 · 覆盖度追踪 · 风险热力图</p></div></div>
      
      {/* Stats */}
      <div className="grid grid-5 gap-4 mb-4">
        {[
          { label: '行为点总数', val: behaviors.length },
          { label: '已覆盖', val: covered },
          { label: '覆盖率', val: `${pct}%` },
          { label: '风险发现', val: totalFindings },
          { label: '待检测', val: behaviors.length - covered },
        ].map(m => (
          <div key={m.label} className="stat-card">
            <div className="cov-value">{m.val}</div><div className="cov-label">{m.label}</div>
          </div>
        ))}
      </div>

      {/* Type breakdown */}
      <div className="grid grid-4 gap-4 mb-4">
        {(['API', '数据库', '文档', '业务流程'] as BehaviorType[]).map(t => {
          const items = behaviors.filter(b => b.type === t);
          const risks = items.reduce((s, b) => s + b.findings, 0);
          const cov = items.filter(b => b.tested).length;
          const accentColor = t === 'API' ? 'var(--primary)' : t === '数据库' ? 'var(--success)' : t === '文档' ? 'var(--warning)' : 'var(--danger)';
          return (
            <div key={t} className="stat-card" style={{ '--accent': accentColor } as React.CSSProperties}>
              <span style={{ display: 'inline-block', padding: '2px 10px', borderRadius: 4, fontSize: 10, fontWeight: 800, ...parseStyle(typeStyle[t]) }}>{t}</span>
              <div style={{ marginTop: 8, display: 'flex', justifyContent: 'space-around', fontSize: 12 }}>
                <span>{items.length}<span style={{ color: 'var(--muted)', fontSize: 10 }}> 项</span></span>
                <span style={{ color: risks > 0 ? 'var(--danger)' : 'var(--success)' }}>{risks}<span style={{ color: 'var(--muted)', fontSize: 10 }}> 风险</span></span>
                <span>{cov}/{items.length}<span style={{ color: 'var(--muted)', fontSize: 10 }}> 覆盖</span></span>
              </div>
            </div>
          );
        })}
      </div>

      {/* Behavior Matrix */}
      <div style={{ background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 'var(--radius)', overflow: 'hidden' }}>
        <div style={{ padding: '12px 20px', background: '#f8fafc', borderBottom: '1px solid var(--line)', fontSize: 12, fontWeight: 700, color: 'var(--muted)' }}>行为覆盖矩阵</div>
        <div className="overflow-x-auto">
          <table className="data-table">
            <thead><tr><th>类型</th><th>标识</th><th>详情</th><th style={{ textAlign: 'center' }}>已检测</th><th style={{ textAlign: 'center' }}>风险</th><th>风险等级</th></tr></thead>
            <tbody>
              {behaviors.map((b, i) => (
                <tr key={`${b.type}-${b.identifier}-${i}`}>
                  <td><span style={{ padding: '2px 8px', borderRadius: 4, fontSize: 10, fontWeight: 800, ...parseStyle(typeStyle[b.type]) }}>{b.type}</span></td>
                  <td className="font-mono" style={{ fontSize: 11 }}>{b.identifier}</td>
                  <td style={{ fontSize: 11, color: 'var(--muted)' }}>{b.detail}</td>
                  <td style={{ textAlign: 'center' }}>{b.tested ? '✅' : '⏳'}</td>
                  <td style={{ textAlign: 'center', fontWeight: 700, color: b.findings > 0 ? 'var(--danger)' : 'var(--muted)' }}>{b.findings || '-'}</td>
                  <td><div style={{ height: 6, borderRadius: 3, background: '#f1f5f9', width: 80 }}><div style={{ height: '100%', borderRadius: 3, background: b.findings >= 2 ? 'var(--danger)' : b.findings === 1 ? 'var(--warning)' : 'var(--success)', width: `${Math.min(100, (b.findings + 1) * 25)}%` }} /></div></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function parseStyle(s: string): Record<string, string> {
  const r: Record<string, string> = {};
  s.split(';').filter(Boolean).forEach(p => { const [k, v] = p.split(':'); if (k && v) r[k.trim()] = v.trim(); });
  return r;
}
