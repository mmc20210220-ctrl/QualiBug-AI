import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { isCustomerReadyFinding, useFindingsData } from '../api/data';
import { usePageTitle } from '../lib/page-title';
import { useProjectNavigation } from '../lib/project-navigation';
import { FindingCard } from '../components/findings/FindingCard';
import { EvidenceDrawer } from '../components/findings/EvidenceDrawer';
import { FindingFilter } from '../components/findings/FindingFilter';
import type { Finding } from '../types';

function moduleName(finding: Finding): string {
  return String(finding.business_impact?.module || finding.source_entity || finding.defect_family_label || '未归类').trim() || '未归类';
}

export function Findings() {
  usePageTitle('问题清单');
  const [params] = useSearchParams();
  const project = params.get('project')?.trim() || '';
  const { navigateToProjectPath } = useProjectNavigation();
  const { findings, clues, loading, error, refetch } = useFindingsData(project);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [filter, setFilter] = useState('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [drawerFinding, setDrawerFinding] = useState<Finding | null>(null);

  const confirmed = findings.filter(isCustomerReadyFinding);
  const bySeverity = {
    P0: confirmed.filter((f) => f.severity === 'P0').length,
    P1: confirmed.filter((f) => f.severity === 'P1').length,
    P2: confirmed.filter((f) => f.severity === 'P2').length,
  };

  const moduleStats = new Map<string, number>();
  for (const f of confirmed) {
    const mod = moduleName(f);
    moduleStats.set(mod, (moduleStats.get(mod) || 0) + 1);
  }

  const filterOptions = [
    { label: `全部 (${confirmed.length})`, value: 'all' },
    { label: `P0 (${bySeverity.P0})`, value: 'P0' },
    { label: `P1 (${bySeverity.P1})`, value: 'P1' },
    { label: `P2 (${bySeverity.P2})`, value: 'P2' },
    ...Array.from(moduleStats.entries()).slice(0, 4).map(([mod, count]) => ({ label: `${mod} (${count})`, value: `mod:${mod}` })),
  ];

  const display = confirmed.filter((f) => {
    if (filter === 'P0' || filter === 'P1' || filter === 'P2') {
      if (f.severity !== filter) return false;
    } else if (filter.startsWith('mod:')) {
      if (moduleName(f) !== filter.slice(4)) return false;
    }
    if (searchQuery.trim()) {
      const q = searchQuery.trim().toLowerCase();
      const haystack = `${f.title} ${moduleName(f)} ${f.business_summary || ''} ${f.actual || ''}`.toLowerCase();
      if (!haystack.includes(q)) return false;
    }
    return true;
  });

  return (
    <div>
      <div className="findings-page-head">
        <div>
          <h1>问题清单</h1>
          <span className="findings-count">
            {confirmed.length} 个已确认缺陷
            {bySeverity.P0 > 0 && ` · ${bySeverity.P0} 个 P0 阻断`}
            {clues.length > 0 && ` · ${clues.length} 条待补证`}
          </span>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn btn-secondary" onClick={() => navigateToProjectPath('/evidence', project)}>证据中心</button>
          <button className="btn btn-primary" onClick={() => navigateToProjectPath('/release', project)}>发布门禁</button>
        </div>
      </div>

      <FindingFilter filters={filterOptions} active={filter} onChange={setFilter} searchQuery={searchQuery} onSearchChange={setSearchQuery} />

      {loading && <div className="state-panel"><div className="spinner spinner-centered" /><p>正在整理问题清单...</p></div>}
      {!loading && error && display.length === 0 && (
        <section className="findings-empty-state danger">
          <span className="findings-empty-kicker">连接异常</span>
          <h3>数据暂时不可用</h3>
          <p>{error}</p>
          <button className="btn btn-primary" onClick={refetch}>重新连接</button>
        </section>
      )}
      {!loading && !error && display.length === 0 && (
        <section className="findings-empty-state compact">
          <span className="findings-empty-kicker">当前结论</span>
          <h3>{filter === 'all' && !searchQuery ? '当前暂无已确认缺陷' : '没有匹配的问题'}</h3>
          <p>{clues.length > 0 ? `本轮存在 ${clues.length} 条待验证线索，尚不足以进入客户交付。` : '系统尚未检测到具备真实运行证据的可交付缺陷。'}</p>
          <button className="btn btn-primary" onClick={() => navigateToProjectPath('/dashboard', project)}>返回价值总览</button>
        </section>
      )}

      {display.map((finding) => (
        <FindingCard
          key={finding.id}
          finding={finding}
          expanded={expandedId === finding.id}
          onToggle={() => setExpandedId(expandedId === finding.id ? null : finding.id)}
          onViewEvidence={() => setDrawerFinding(finding)}
        />
      ))}

      <EvidenceDrawer finding={drawerFinding} onClose={() => setDrawerFinding(null)} />
    </div>
  );
}

export default Findings;
