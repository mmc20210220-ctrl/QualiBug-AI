import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { emitScanCompleted, isCustomerReadyFinding, useFindingsData } from '../api/data';
import { runRegression } from '../api/client';
import { useToast } from '../components/useToast';
import { deriveFindingVerification, hasFindingReverificationObligation } from '../lib/finding-verification';
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

export function Findings() {
  usePageTitle('问题清单');
  const [params] = useSearchParams();
  const project = params.get('project')?.trim() || '';
  const requestedFindingId = params.get('finding')?.trim() || '';
  const { navigateToProjectPath } = useProjectNavigation();
  const { findings, clues, loading, error, refetch } = useFindingsData(project);
  const toast = useToast();
  const [expandedId, setExpandedId] = useState<string | null>(requestedFindingId || null);
  const [filter, setFilter] = useState('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [drawerFinding, setDrawerFinding] = useState<Finding | null>(null);
  const [regressionRunning, setRegressionRunning] = useState(false);

  useEffect(() => {
    if (requestedFindingId) {
      setExpandedId(requestedFindingId);
      setFilter('all');
      setSearchQuery('');
    }
  }, [requestedFindingId]);

  const confirmed = findings.filter(isCustomerReadyFinding);
  const bySeverity = {
    P0: confirmed.filter((finding) => finding.severity === 'P0').length,
    P1: confirmed.filter((finding) => finding.severity === 'P1').length,
    P2: confirmed.filter((finding) => finding.severity === 'P2').length,
  };
  const verificationRows = confirmed.map((finding) => ({ finding, verification: deriveFindingVerification(finding) }));
  const pendingRegression = verificationRows.filter(({ verification }) => verification.state === 'pending');
  const passedRegression = verificationRows.filter(({ verification }) => verification.state === 'verified_fixed');
  const failedRegression = verificationRows.filter(({ verification }) => verification.state === 'still_failing');
  const inconclusiveRegression = verificationRows.filter(({ verification }) => verification.state === 'inconclusive');

  const moduleStats = new Map<string, number>();
  for (const finding of confirmed) {
    const mod = moduleName(finding);
    moduleStats.set(mod, (moduleStats.get(mod) || 0) + 1);
  }

  const filterOptions = [
    { label: `全部 (${confirmed.length})`, value: 'all' },
    { label: `P0 (${bySeverity.P0})`, value: 'P0' },
    { label: `P1 (${bySeverity.P1})`, value: 'P1' },
    { label: `P2 (${bySeverity.P2})`, value: 'P2' },
    { label: `等待验证 (${pendingRegression.length})`, value: 'verify:pending' },
    { label: `仍失败 (${failedRegression.length})`, value: 'verify:still_failing' },
    { label: `无法确认 (${inconclusiveRegression.length})`, value: 'verify:inconclusive' },
    { label: `验证通过 (${passedRegression.length})`, value: 'verify:verified_fixed' },
    ...Array.from(moduleStats.entries()).slice(0, 4).map(([mod, count]) => ({ label: `${mod} (${count})`, value: `mod:${mod}` })),
  ];

  const display = [...confirmed]
    .sort((left, right) => deriveFindingVerification(right).priority - deriveFindingVerification(left).priority)
    .filter((finding) => {
      if (filter === 'P0' || filter === 'P1' || filter === 'P2') {
        if (finding.severity !== filter) return false;
      } else if (filter.startsWith('verify:')) {
        if (deriveFindingVerification(finding).state !== filter.slice('verify:'.length)) return false;
      } else if (filter.startsWith('mod:')) {
        if (moduleName(finding) !== filter.slice(4)) return false;
      }
      if (searchQuery.trim()) {
        const query = searchQuery.trim().toLowerCase();
        const haystack = `${finding.title} ${moduleName(finding)} ${finding.business_summary || ''} ${finding.actual || ''}`.toLowerCase();
        if (!haystack.includes(query)) return false;
      }
      return true;
    });
  const hasActiveFilter = filter !== 'all' || Boolean(searchQuery.trim());

  const regressionEligible = confirmed.some(hasFindingReverificationObligation);
  const regressionHistory = confirmed
    .flatMap((finding) => (finding.regression?.history || []).map((item) => ({ finding, item })))
    .sort((left, right) => String(right.item.generated_at || '').localeCompare(String(left.item.generated_at || '')))
    .slice(0, 6);
  const hasRegressionFact = confirmed.some((finding) => Boolean(finding.regression));

  const runReleaseRegression = async (): Promise<void> => {
    if (!project || regressionRunning) return;
    const currentEligible = confirmed.some(hasFindingReverificationObligation);
    if (!currentEligible) {
      toast.show('当前没有已纳入真实回归套件的验证义务；不会提交空验证请求。', 'warning');
      return;
    }
    setRegressionRunning(true);
    try {
      toast.show('正在重新验证客户修复结果…', 'info');
      const result = await runRegression(project, { mode: 'release' });
      emitScanCompleted(project);
      await refetch();
      const gateStatus = String(result.ci_feedback?.gate_status || 'unknown');
      const failedCount = Number(result.summary?.failed_count || 0);
      toast.show(
        `修复后验证完成：${gateStatus}${failedCount > 0 ? `，仍失败 ${failedCount} 项` : ''}`,
        gateStatus === 'failed' ? 'danger' : gateStatus === 'passed' ? 'success' : 'warning',
      );
    } catch (caught: unknown) {
      toast.show(caught instanceof Error ? caught.message : '修复后验证失败', 'danger');
    } finally {
      setRegressionRunning(false);
    }
  };

  const clearFilters = (): void => {
    setFilter('all');
    setSearchQuery('');
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
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button className="btn btn-secondary" onClick={() => void runReleaseRegression()} disabled={!project || regressionRunning || !regressionEligible} title={regressionEligible ? '执行当前已纳入真实回归套件的修复后验证' : '当前没有真实可执行回归义务'}>
            {regressionRunning ? '正在重新验证' : regressionEligible ? '修复后重新验证' : '暂无可执行验证'}
          </button>
          <button className="btn btn-secondary" onClick={() => navigateToProjectPath('/evidence', project)}>证据中心</button>
          <button className="btn btn-primary" onClick={() => navigateToProjectPath('/release', project)}>发布门禁</button>
        </div>
      </div>

      {requestedFindingId && !loading && !error && !confirmed.some((finding) => finding.id === requestedFindingId) && (
        <section className="findings-empty-state compact">
          <span className="findings-empty-kicker">指定问题</span>
          <h3>该问题已不在当前已确认结果中</h3>
          <p>链接中的 Finding 标识可能来自旧扫描或已经发生状态变化。当前不会用标题相似的问题代替它。</p>
          <button className="btn btn-secondary" onClick={() => navigateToProjectPath('/findings', project)}>查看当前问题清单</button>
        </section>
      )}

      {hasRegressionFact && (
        <section className="customer-secondary-grid findings-regression-grid" aria-label="QualiBug 修复后验证">
          <article className="customer-secondary-card">
            <span className="customer-value-kicker">验证闭环</span>
            <h3>{failedRegression.length > 0
              ? `${failedRegression.length} 个问题重新验证仍失败`
              : inconclusiveRegression.length > 0
                ? `${inconclusiveRegression.length} 个问题本轮无法确认`
                : pendingRegression.length > 0
                  ? `${pendingRegression.length} 个问题等待修复后验证`
                  : passedRegression.length > 0
                    ? '已执行的问题验证均通过'
                    : '尚无修复后验证结果'}
            </h3>
            <p>验证通过 {passedRegression.length} · 等待验证 {pendingRegression.length} · 仍失败 {failedRegression.length} · 无法确认 {inconclusiveRegression.length}。QualiBug 只验证修复后的系统行为，不记录企业内部研发进度。</p>
          </article>

          <article className="customer-secondary-card">
            <span className="customer-value-kicker">等待重新验证</span>
            <h3>{pendingRegression.length > 0 ? pendingRegression.slice(0, 3).map(({ finding }) => finding.title).join('、') : '当前无等待验证项'}</h3>
            <p>{pendingRegression.length > 0
              ? '客户完成修复后直接执行真实回归即可；不需要先在 QualiBug 中登记负责人、版本或“修复中”状态。'
              : '有真实回归义务且尚未形成终态结果的问题会出现在这里。'}
            </p>
          </article>

          <article className="customer-secondary-card">
            <span className="customer-value-kicker">验证历史</span>
            <h3>{regressionHistory.length > 0 ? `最近 ${regressionHistory.length} 条真实验证记录` : '暂无验证记录'}</h3>
            {regressionHistory.length > 0 ? (
              <p>{regressionHistory.map(({ finding, item }) => `[${item.generated_at || '未知时间'}] ${finding.title} · ${item.status_label || item.gate_status || '状态未上报'}`).join('；')}</p>
            ) : (
              <p>执行修复后验证后，真实回归记录会在这里展示。</p>
            )}
          </article>
        </section>
      )}

      {(loading || confirmed.length > 0) && (
        <FindingFilter filters={filterOptions} active={filter} onChange={setFilter} searchQuery={searchQuery} onSearchChange={setSearchQuery} />
      )}

      {loading && (
        <div aria-busy="true" aria-label="正在整理问题清单">
          {[1, 2, 3].map((index) => (
            <div key={index} className="finding-card">
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
          <span className="findings-empty-kicker">{hasActiveFilter ? '筛选结果' : '当前结论'}</span>
          <h3>{hasActiveFilter ? '没有匹配的问题' : '当前暂无已确认缺陷'}</h3>
          <p>{hasActiveFilter
            ? '当前筛选条件没有命中已确认问题。清除筛选后可返回完整问题清单。'
            : clues.length > 0
              ? `本轮存在 ${clues.length} 条待验证线索，尚不足以进入客户交付；可继续检测或查看当前覆盖范围。`
              : '当前没有具备真实运行证据的可交付缺陷。若本轮覆盖尚未完成，请继续检测或查看覆盖范围，不要把空列表直接解释为系统没有问题。'}
          </p>
          <div className="settings-actions">
            {hasActiveFilter ? (
              <button className="btn btn-primary" onClick={clearFilters}>清除筛选</button>
            ) : (
              <>
                <button className="btn btn-primary" onClick={() => navigateToProjectPath('/campaigns', project)}>继续检测</button>
                <button className="btn btn-secondary" onClick={() => navigateToProjectPath('/coverage', project)}>查看覆盖范围</button>
              </>
            )}
            <button className="btn btn-secondary" onClick={() => navigateToProjectPath('/dashboard', project)}>返回价值总览</button>
          </div>
        </section>
      )}

      {display.map((finding) => (
        <FindingCard
          key={finding.id}
          finding={finding}
          expanded={expandedId === finding.id}
          onToggle={() => setExpandedId(expandedId === finding.id ? null : finding.id)}
          onViewEvidence={() => setDrawerFinding(finding)}
          reverifyRunning={regressionRunning}
          onReverify={hasFindingReverificationObligation(finding) ? () => void runReleaseRegression() : undefined}
        />
      ))}

      <EvidenceDrawer finding={drawerFinding} project={project} onClose={() => setDrawerFinding(null)} />
    </div>
  );
}

export default Findings;
