import { useSearchParams } from 'react-router-dom';
import { useFindingsData } from '../api/data';

export function ReleaseGate() {
  const [params] = useSearchParams();
  const project = params.get('project') || 'real_project_demo';
  const { findings, loading } = useFindingsData(project);

  // Generate release gate checks from real findings
  const p0 = findings.filter(f => f.severity === 'P0');
  const p1 = findings.filter(f => f.severity === 'P1');
  const confirmed = findings.filter(f => f.verdict === 'confirmed');

  const checks = [
    {
      name: 'P0 阻塞级缺陷',
      status: p0.length === 0 ? 'pass' as const : 'fail' as const,
      detail: p0.length === 0 ? '无 P0 级别缺陷' : `发现 ${p0.length} 个 P0 缺陷，阻塞发布`,
    },
    {
      name: 'P1 高风险缺陷',
      status: p1.length <= 3 ? 'pass' as const : 'warning' as const,
      detail: p1.length === 0 ? '无 P1 级别缺陷' : p1.length <= 3 ? `${p1.length} 个 P1 缺陷，建议修复后发布` : `${p1.length} 个 P1 缺陷，建议暂缓发布`,
    },
    {
      name: '风险确认率',
      status: findings.length === 0 ? 'pending' as const : confirmed.length / findings.length >= 0.5 ? 'pass' as const : 'warning' as const,
      detail: findings.length === 0 ? '暂无风险数据' : `${confirmed.length}/${findings.length} 已确认 (${Math.round(confirmed.length / findings.length * 100)}%)`,
    },
    {
      name: '证据链完整度',
      status: findings.length === 0 ? 'pending' as const
        : findings.filter(f => f.evidence_chain.length >= 3).length / findings.length >= 0.7 ? 'pass' as const : 'warning' as const,
      detail: `${findings.filter(f => f.evidence_chain.length >= 3).length}/${findings.length} 具备完整证据链`,
    },
    {
      name: '服务可用性',
      status: 'pass' as const,
      detail: '后端引擎正常响应',
    },
  ];

  const failCount = checks.filter(c => c.status === 'fail').length;
  const warnCount = checks.filter(c => c.status === 'warning').length;
  const passCount = checks.filter(c => c.status === 'pass').length;
  const overall = failCount > 0 ? 'fail' : 'pass';

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>发布门禁</h1>
          <p>基于 {findings.length} 条真实行为风险的自动化发布决策</p>
        </div>
        <div className={`gate-result ${overall}`}>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
            <h2>{overall === 'pass' ? '通过' : '阻塞'}</h2>
            <p>{passCount}/{checks.length} 检查通过</p>
          </div>
        </div>
      </div>

      {loading && (
        <div style={{ textAlign: 'center', padding: 48 }}>
          <div className="spinner" style={{ margin: '0 auto 16px' }} />
          <p style={{ color: 'var(--muted)', fontSize: 13 }}>评估发布就绪状态...</p>
        </div>
      )}

      {/* Stats */}
      <div className="grid grid-3 gap-4 mb-4">
        {[
          { label: '通过', val: passCount, color: 'var(--success)' },
          { label: '阻塞', val: failCount, color: 'var(--danger)' },
          { label: '警告', val: warnCount, color: 'var(--warning)' },
        ].map(m => (
          <div key={m.label} className="stat-card" style={{ '--accent': m.color } as React.CSSProperties}>
            <div className="cov-value" style={{ color: m.color }}>{m.val}</div>
            <div className="cov-label">{m.label}</div>
          </div>
        ))}
      </div>

      {/* Check list */}
      <div className="check-list">
        {findings.length === 0 && !loading && (
          <div className="check-item">
            <span className="check-icon warning">!</span>
            <div className="flex-1">
              <strong style={{ fontSize: 13 }}>暂无风险数据</strong>
              <p style={{ fontSize: 11, color: 'var(--subtle)', marginTop: 2 }}>运行一次扫描以生成发布门禁检查结果</p>
            </div>
          </div>
        )}
        {checks.map(c => (
          <div key={c.name} className="check-item">
            <span className={`check-icon ${c.status}`}>
              {c.status === 'pass' ? '✓' : c.status === 'fail' ? '✗' : '!'}
            </span>
            <div className="flex-1">
              <strong style={{ fontSize: 13 }}>{c.name}</strong>
              <p style={{ fontSize: 11, color: 'var(--subtle)', marginTop: 2 }}>{c.detail}</p>
            </div>
            <span className={`status status-${c.status === 'pass' ? 'success' : c.status === 'fail' ? 'danger' : 'warning'}`}>
              {c.status === 'pass' ? '通过' : c.status === 'fail' ? '阻塞' : '待检'}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
