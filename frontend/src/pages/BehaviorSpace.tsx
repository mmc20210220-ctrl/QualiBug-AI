import { useSearchParams } from 'react-router-dom';
import { useFindingsData } from '../api/data';

type BehaviorType = 'API' | '数据库' | '文档' | '业务流程';

interface BehaviorItem {
  type: BehaviorType;
  identifier: string;
  detail: string;
  tested: boolean;
  findings: number;
  severity: string;
}

const typeStyle: Record<string, string> = {
  'API': 'background:var(--primary-muted);color:var(--primary)',
  '数据库': 'background:var(--success-muted);color:var(--success)',
  '文档': 'background:var(--warning-muted);color:var(--warning)',
  '业务流程': 'background:var(--danger-muted);color:var(--danger)',
};

export function BehaviorSpace() {
  const [params] = useSearchParams();
  const project = params.get('project') || 'real_project_demo';
  const { findings, loading } = useFindingsData(project);

  // Build behavior matrix from real findings
  const behaviors: BehaviorItem[] = [];
  const seen = new Set<string>();

  findings.forEach(f => {
    // API behaviors
    if (f.repro_method && f.repro_path) {
      const key = `API:${f.repro_method}:${f.repro_path}`;
      if (!seen.has(key)) {
        seen.add(key);
        behaviors.push({
          type: 'API',
          identifier: f.repro_path,
          detail: f.repro_method,
          tested: true,
          findings: 1,
          severity: f.severity,
        });
      } else {
        // Increment findings for existing entry
        const existing = behaviors.find(b => b.identifier === f.repro_path && b.type === 'API');
        if (existing) existing.findings++;
      }
    }

    // DB behaviors
    if (f.title.includes('DB Verified') || f.title.includes('库存') || f.title.includes('BOM') || f.source_entity) {
      const key = `DB:${f.source_entity || f.title.slice(0, 30)}`;
      if (!seen.has(key)) {
        seen.add(key);
        behaviors.push({
          type: '数据库',
          identifier: f.source_entity || '数据表',
          detail: f.title.slice(0, 40),
          tested: true,
          findings: 1,
          severity: f.severity,
        });
      }
    }

    // Document/business rule behaviors
    if (!f.repro_path && !f.title.includes('DB Verified') && !f.title.includes('库存')) {
      const key = `文档:${f.title.slice(0, 30)}`;
      if (!seen.has(key)) {
        seen.add(key);
        behaviors.push({
          type: f.title.includes('PRD') ? '文档' : '业务流程',
          identifier: f.title.slice(0, 40),
          detail: f.evidence_chain[0]?.detail || '',
          tested: f.verdict === 'confirmed',
          findings: 1,
          severity: f.severity,
        });
      }
    }
  });

  const covered = behaviors.filter(r => r.tested).length;
  const totalFindings = behaviors.reduce((s, r) => s + r.findings, 0);
  const pct = behaviors.length > 0 ? Math.round((covered / behaviors.length) * 100) : 0;

  return (
    <div>
      <div className="page-header"><div><h1>行为空间</h1><p>企业系统行为建模 · 基于{findings.length}条真实风险发现构建覆盖矩阵</p></div></div>

      {/* Stats */}
      <div className="grid grid-5 gap-4 mb-4">
        {[
          { label: '行为点总数', val: behaviors.length },
          { label: '已覆盖', val: covered },
          { label: '覆盖率', val: `${pct}%` },
          { label: '风险发现', val: totalFindings },
          { label: '待检测', val: behaviors.filter(r => !r.tested).length },
        ].map(m => (
          <div key={m.label} className="stat-card">
            <div className="cov-value">{m.val}</div><div className="cov-label">{m.label}</div>
          </div>
        ))}
      </div>

      {loading && (
        <div style={{ textAlign: 'center', padding: 48 }}>
          <div className="spinner" style={{ margin: '0 auto 16px' }} />
          <p style={{ color: 'var(--muted)', fontSize: 13 }}>从风险发现重建行为矩阵...</p>
        </div>
      )}

      {!loading && behaviors.length === 0 && (
        <div style={{ textAlign: 'center', padding: 60, background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 'var(--radius)' }}>
          <div style={{ fontSize: 48, marginBottom: 12 }}>🗺️</div>
          <h3 style={{ fontSize: 16, fontWeight: 700, marginBottom: 8 }}>暂无行为数据</h3>
          <p style={{ color: 'var(--muted)', fontSize: 13 }}>运行扫描发现行为风险后，行为空间将自动生成</p>
        </div>
      )}

      {/* Type breakdown */}
      {behaviors.length > 0 && (
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
      )}

      {/* Behavior Matrix */}
      {behaviors.length > 0 && (
        <div style={{ background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 'var(--radius)', overflow: 'hidden' }}>
          <div style={{ padding: '12px 20px', background: '#f8fafc', borderBottom: '1px solid var(--line)', fontSize: 12, fontWeight: 700, color: 'var(--muted)' }}>行为覆盖矩阵</div>
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead><tr><th>类型</th><th>标识</th><th>详情</th><th style={{ textAlign: 'center' }}>风险等级</th><th style={{ textAlign: 'center' }}>状态</th><th>风险指示</th></tr></thead>
              <tbody>
                {behaviors.map((b, i) => (
                  <tr key={`${b.type}-${i}`}>
                    <td><span style={{ padding: '2px 8px', borderRadius: 4, fontSize: 10, fontWeight: 800, ...parseStyle(typeStyle[b.type]) }}>{b.type}</span></td>
                    <td className="font-mono" style={{ fontSize: 11 }}>{b.identifier}</td>
                    <td style={{ fontSize: 11, color: 'var(--muted)' }}>{b.detail}</td>
                    <td style={{ textAlign: 'center' }}>
                      <span style={{
                        padding: '1px 6px', borderRadius: 3, fontSize: 10, fontWeight: 800,
                        background: b.severity === 'P0' ? 'var(--danger-muted)' : b.severity === 'P1' ? 'var(--warning-muted)' : 'var(--primary-muted)',
                        color: b.severity === 'P0' ? 'var(--danger)' : b.severity === 'P1' ? 'var(--warning)' : 'var(--primary)',
                      }}>{b.severity}</span>
                    </td>
                    <td style={{ textAlign: 'center' }}>{b.tested ? '✅' : '⏳'}</td>
                    <td><div style={{ height: 6, borderRadius: 3, background: '#f1f5f9', width: 80 }}><div style={{ height: '100%', borderRadius: 3, background: b.findings >= 2 ? 'var(--danger)' : b.findings === 1 ? 'var(--warning)' : 'var(--success)', width: `${Math.min(100, (b.findings + 1) * 25)}%` }} /></div></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

function parseStyle(s: string): Record<string, string> {
  const r: Record<string, string> = {};
  s.split(';').filter(Boolean).forEach(p => { const [k, v] = p.split(':'); if (k && v) r[k.trim()] = v.trim(); });
  return r;
}
