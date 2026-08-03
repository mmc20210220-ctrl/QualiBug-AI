import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { emitScanCompleted, isCustomerReadyFinding, useFindingsData } from '../api/data';
import { runRegression } from '../api/client';
import { useToast } from '../components/useToast';
import { usePageTitle } from '../lib/page-title';
import { useProjectNavigation } from '../lib/project-navigation';
import { FindingCard } from '../components/findings/FindingCard';
import { EvidenceDrawer } from '../components/findings/EvidenceDrawer';
import { FindingFilter } from '../components/findings/FindingFilter';
import { Skeleton } from '../components/dashboard/DashboardPrimitives';
import { TermHint } from '../components/TermHint';
import { GLOSSARY } from '../lib/glossary';
import type { Finding } from '../types';

function moduleName(finding: Finding): string {
  return String(finding.business_impact?.module || finding.source_entity || finding.defect_family_label || '未归类').trim() || '未归类';
}

function regressionLifecycleLabel(finding: Finding): string {
  return String(finding.regression?.lifecycle_label || (finding.regression?.included_in_suite ? '待回归' : '待纳入回归')).trim() || '待纳入回归';
}

export function Findings() {
  usePageTitle('问题清单');
  const [params] = useSearchParams();
  const project = params.get('project')?.trim() || '';
  const { navigateToProjectPath } = useProjectNavigation();
  const { findings, clues, loading, error, refetch } = useFindingsData(project);
  const toast = useToast();
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [filter, setFilter] = useState('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [drawerFinding, setDrawerFinding] = useState<Finding | null>(null);
  const [regressionRunning, setRegressionRunning] = useState(false);

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

  const pendingRegression = confirmed.filter((f) => f.regression && f.regression.included_in_suite && f.regression.latest_status !== 'passed');
  const passedRegression = confirmed.filter((f) => f.regression?.latest_status === 'passed');
  const failedRegression = confirmed.filter((f) => f.regression?.latest_status === 'failed');
  const regressionHistory = confirmed
    .flatMap((f) => (f.regression?.history || []).map((item) => ({ finding: f, item })))
    .sort((a, b) => String(b.item.generated_at || '').localeCompare(String(a.item.generated_at || '')))
    .slice(0, 6);
  const hasRegressionFact = confirmed.some((f) => Boolean(f.regression));

  const runReleaseRegression = async (): Promise<void> => {
    if (!project || regressionRunning) return;
    setRegressionRunning(true);
    try {
      toast.show('正在执行 Release 回归…', 'info');
      const result = await runRegression(project, { mode: 'release' });
      emitScanCompleted(project);
      await refetch();
      const gateStatus = String(result.ci_feedback?.gate_status || 'unknown');
      const failedCount = Number(result.summary?.failed_count || 0);
      toast.show(
        `Release 回归完成：${gateStatus}${failedCount > 0 ? `，失败 ${failedCount} 项` : ''}`,
        gateStatus === 'failed' ? 'danger' : gateStatus === 'passed' ? 'success' : 'warning',
      );
    } catch (caught: unknown) {
      toast.show(caught instanceof Error ? caught.message : '回归执行失败', 'danger');
    } finally {
      setRegressionRunning(false);
    }
  };

  return (
    <div>
      <div className="findings-page-head">
        <div>
          <h1>问题清单</h1>
          <span className="findings-count">
            {confirmed.length} 个<TermHint label="已确认缺陷" hint={GLOSSARY.confirmedDefect} />
            {bySeverity.P0 > 0 && <> · {bySeverity.P0} 个 <TermHint label="P0 阻断" hint={GLOSSARY.p0} /></>}
            {clues.length > 0 && <> · {clues.length} 条<TermHint label="待补证线索" hint={GLOSSARY.clue} /></>}
          </span>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn btn-secondary" onClick={() => void runReleaseRegression()} disabled={!project || regressionRunning}>
            {regressionRunning ? 'Release 回归中' : '执行 Release 回归'}
          </button>
          <button className="btn btn-secondary" onClick={() => navigateToProjectPath('/evidence', project)}>证据中心</button>
          <button className="btn btn-primary" onClick={() => navigateToProjectPath('/release', project)}>发布门禁</button>
        </div>
      </div>

      {hasRegressionFact && (
        <section className="customer-secondary-grid findings-regression-grid" aria-label="回归验证">
          <article className="customer-secondary-card">
            <span className="customer-value-kicker">回归验证</span>
            <h3>{failedRegression.length > 0 ? `${failedRegression.length} 个缺陷回归仍失败` : pendingRegression.length > 0 ? `${pendingRegression.length} 个缺陷待执行回归` : passedRegression.length > 0 ? '已纳入缺陷回归均通过' : '尚无回归结果'}</h3>
            <p>已通过 {passedRegression.length} · 待执行 {pendingRegression.length} · 仍失败 {failedRegression.length}。客户修复后由真实回归验证是否闭环。</p>
          </article>
          <article className="customer-secondary-card">
            <span className="customer-value-kicker">待执行回归</span>
            <h3>{pendingRegression.length > 0 ? pendingRegression.slice(0, 3).map((f) => f.title).join('、') : '当前无待执行项'}</h3>
            <p>{pendingRegression.length > 0
              ? pendingRegression.slice(0, 3).map((f) => `${f.title}（生命周期：${regressionLifecycleLabel(f)}）`).join('；')
              : '纳入回归套件后，这里会列出尚未验证闭环的缺陷。'}
            </p>
          </article>
          <article className="customer-secondary-card">
            <span className="customer-value-kicker">回归历史</span>
            <h3>{regressionHistory.length > 0 ? `最近 ${regressionHistory.length} 条回归记录` : '暂无回归记录'}</h3>
            {regressionHistory.length > 0 ? (
              <p>{regressionHistory.map(({ finding, item }) => `[${item.generated_at || '未知时间'}] ${finding.title} · ${item.status_label || item.gate_status || '回归'}`).join('；')}</p>
            ) : (
              <p>执行回归后，历史记录会在这里展示。</p>
            )}
          </article>
        </section>
      )}

      <FindingFilter filters={filterOptions} active={filter} onChange={setFilter} searchQuery={searchQuery} onSearchChange={setSearchQuery} />

      {loading && (
        <div aria-busy="true" aria-label="正在整理问题清单">
          {[1, 2, 3].map((i) => (
            <div key={i} className="finding-card">
              <div className="finding-card-main">
                <Skeleton h={14} w={72} br={4} />
                <div style={{ marginTop: 10 }}><Skeleton h={18} w="68%" br={4} /></div>
                <div style={{ marginTop: 12 }}><Skeleton h={12} w="46%" br={4} /></div>
              </div>
            </div>
          ))}
        </div>
      )}
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
