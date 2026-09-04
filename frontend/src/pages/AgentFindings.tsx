import { useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { emitScanCompleted, isCustomerReadyFinding, useFindingsData } from '../api/data';
import { runRegression } from '../api/client';
import { useToast } from '../components/useToast';
import { FindingCard } from '../components/findings/FindingCard';
import { EvidenceDrawer } from '../components/findings/EvidenceDrawer';
import { Skeleton, StatePanel } from '../components/dashboard/DashboardPrimitives';
import { deriveFindingVerification, hasFindingReverificationObligation } from '../lib/finding-verification';
import { buildProjectPath } from '../lib/project-navigation';
import { usePageTitle } from '../lib/page-title';
import type { Finding } from '../types';
import './AgentFindings.css';

function severityWeight(value: string): number {
  const weights: Record<string, number> = { P0: 4, P1: 3, P2: 2, P3: 1 };
  return weights[value] || 0;
}

function findingSummary(finding: Finding): string {
  return finding.business_summary
    || finding.business_impact?.summary
    || finding.actual
    || '查看真实复现、Expected / Actual 与证据。';
}

export function AgentFindings() {
  usePageTitle('Findings');
  const [params] = useSearchParams();
  const project = params.get('project')?.trim() || '';
  const requestedFindingId = params.get('finding')?.trim() || '';
  const { findings, clues, loading, error, refetch } = useFindingsData(project);
  const toast = useToast();
  const [expandedId, setExpandedId] = useState<string | null>(requestedFindingId || null);
  const [drawerFinding, setDrawerFinding] = useState<Finding | null>(null);
  const [regressionRunning, setRegressionRunning] = useState(false);

  useEffect(() => {
    if (requestedFindingId) setExpandedId(requestedFindingId);
  }, [requestedFindingId]);

  const confirmed = useMemo(
    () => findings
      .filter(isCustomerReadyFinding)
      .sort((left, right) => {
        const verificationGap = deriveFindingVerification(right).priority - deriveFindingVerification(left).priority;
        if (verificationGap !== 0) return verificationGap;
        return severityWeight(right.severity) - severityWeight(left.severity);
      }),
    [findings],
  );
  const p0Count = confirmed.filter((finding) => finding.severity === 'P0').length;
  const evidenceBacked = confirmed.filter((finding) => (finding.evidence_chain?.length || 0) > 0).length;
  const regressionEligible = confirmed.some(hasFindingReverificationObligation);

  const headline = p0Count > 0
    ? `我发现 ${p0Count} 个会阻断发布的问题`
    : confirmed.length > 0
      ? `我发现 ${confirmed.length} 个需要你关注的问题`
      : clues.length > 0
        ? `还没有已确认 Finding，但有 ${clues.length} 条待补证线索`
        : '当前没有已确认 Finding';

  const explanation = p0Count > 0
    ? '这些问题已经满足客户可交付证据边界，并且严重度为 P0。先调查证据并完成修复后验证，再讨论发布。'
    : confirmed.length > 0
      ? `${evidenceBacked}/${confirmed.length} 个问题已携带证据链。列表为空或没有 P0 都不能单独解释为系统安全。`
      : clues.length > 0
        ? '线索还没有达到可交付 Finding 的证据门槛；QualiBug 不会把推测或不完整证据升级成真实 Bug。'
        : '当前没有具备真实运行证据的可交付问题；是否安全仍要结合覆盖范围、运行状态和 Release Gate。';

  const runReleaseRegression = async () => {
    if (!project || regressionRunning || !regressionEligible) return;
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

  if (!project) {
    return <StatePanel eyebrow="Findings" title="选择项目后查看 Agent 发现的问题" description="这里只呈现满足真实执行和证据边界的 Finding，不把待补证线索或推测当作 Bug。" />;
  }

  if (loading) {
    return (
      <div className="agent-findings">
        <Skeleton h={220} br={22} />
        <Skeleton h={360} br={18} />
      </div>
    );
  }

  if (error && confirmed.length === 0) {
    return <StatePanel eyebrow="Findings · 连接状态" title="无法读取当前 Finding" description={error} action={<button type="button" className="btn btn-primary" onClick={refetch}>重新连接</button>} />;
  }

  return (
    <div className="agent-findings">
      <section className={`agent-findings-hero${p0Count > 0 ? ' has-blocker' : ''}`}>
        <div>
          <span className="agent-findings-kicker">QualiBug found</span>
          <h1>{headline}</h1>
          <p>{explanation}</p>
        </div>
        <div className="agent-findings-actions">
          <button
            type="button"
            className="btn btn-secondary"
            disabled={!regressionEligible || regressionRunning}
            onClick={() => void runReleaseRegression()}
          >
            {regressionRunning ? '正在重新验证…' : regressionEligible ? '重新验证修复结果' : '暂无可执行回归'}
          </button>
          <Link className="btn btn-primary" to={buildProjectPath('/release', project)}>查看 Decision</Link>
        </div>
      </section>

      {confirmed.length > 0 && (
        <section className="agent-findings-priority" aria-label="优先问题">
          <div className="agent-findings-section-head">
            <div><span>Why this matters</span><h2>最需要先看的问题</h2></div>
            <small>{confirmed.length} confirmed · {evidenceBacked} evidence-backed</small>
          </div>
          <div className="agent-findings-priority-grid">
            {confirmed.slice(0, 3).map((finding) => (
              <button key={finding.id} type="button" onClick={() => setExpandedId(finding.id)}>
                <span className={`severity-badge ${finding.severity.toLowerCase()}`}>{finding.severity}</span>
                <strong>{finding.title}</strong>
                <p>{findingSummary(finding)}</p>
                <small>{finding.evidence_chain?.length || 0} 条真实证据 · 点击调查</small>
              </button>
            ))}
          </div>
        </section>
      )}

      {requestedFindingId && !confirmed.some((finding) => finding.id === requestedFindingId) && (
        <section className="agent-findings-boundary">
          <strong>指定 Finding 已不在当前确认结果中</strong>
          <p>它可能来自旧扫描或已经发生状态变化；当前不会拿标题相似的问题代替。</p>
        </section>
      )}

      <section className="agent-findings-list">
        <div className="agent-findings-section-head">
          <div><span>Inspect evidence</span><h2>{confirmed.length > 0 ? '全部已确认 Finding' : '没有可交付 Finding'}</h2></div>
          <Link to={buildProjectPath('/verify', project)}>返回 Live Workspace</Link>
        </div>

        {confirmed.length > 0 ? confirmed.map((finding) => (
          <FindingCard
            key={finding.id}
            finding={finding}
            project={project}
            expanded={expandedId === finding.id}
            onToggle={() => setExpandedId(expandedId === finding.id ? null : finding.id)}
            onViewEvidence={() => setDrawerFinding(finding)}
            reverifyRunning={regressionRunning}
            onReverify={hasFindingReverificationObligation(finding) ? () => void runReleaseRegression() : undefined}
          />
        )) : (
          <div className="agent-findings-empty">
            <strong>当前没有满足交付证据边界的问题</strong>
            <p>{clues.length > 0 ? `${clues.length} 条线索仍在补证。` : '继续验证或查看 Decision，不要把空列表直接解释为系统无问题。'}</p>
            <div>
              <Link className="btn btn-primary" to={buildProjectPath('/verify', project)}>继续验证</Link>
              <Link className="btn btn-secondary" to={buildProjectPath('/release', project)}>查看 Decision</Link>
            </div>
          </div>
        )}
      </section>

      <EvidenceDrawer finding={drawerFinding} project={project} onClose={() => setDrawerFinding(null)} />
    </div>
  );
}

export default AgentFindings;
