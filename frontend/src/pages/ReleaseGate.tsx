import { useSearchParams } from 'react-router-dom';
import { useReleaseData } from '../api/data';

export function ReleaseGate() {
  const [params] = useSearchParams();
  const project = params.get('project') || 'real_project_demo';
  const { data, loading } = useReleaseData(project);

  const checks = data?.checks || [];
  const overall = data?.overall || 'pass';
  const passCount = checks.filter(c => c.status === 'pass').length;
  const failCount = checks.filter(c => c.status === 'fail').length;
  const warnCount = checks.filter(c => c.status === 'warning').length;

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>发布门禁</h1>
          <p>基于行为风险评级的自动化发布决策</p>
        </div>
        <div className={`gate-result ${overall}`}>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
            <h2>{overall === 'pass' ? '通过' : '阻塞'}</h2>
            <p>{passCount}/{checks.length || '--'} 检查通过</p>
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
          { label: '失败', val: failCount, color: 'var(--danger)' },
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
        {checks.length === 0 && !loading && (
          <div className="check-item">
            <span className="check-icon warning">!</span>
            <div className="flex-1">
              <strong style={{ fontSize: 13 }}>暂无门禁数据</strong>
              <p style={{ fontSize: 11, color: 'var(--subtle)', marginTop: 2 }}>运行一次完整扫描以生成发布门禁检查结果</p>
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
              {c.status.toUpperCase()}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
