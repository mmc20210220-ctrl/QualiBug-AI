import { lazy, Suspense, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { EvidenceTimeline } from '../components/EvidenceTimeline';
import { hasRealReplayAsset, isCustomerReadyFinding, useFindingsData } from '../api/data';
import { usePageTitle } from '../lib/page-title';
import { useProjectNavigation } from '../lib/project-navigation';
import { evidenceDeepLinkSearch, evidenceScoreLabel } from '../lib/evidence-presentation';
import { AssertionDiff } from '../components/evidence/AssertionDiff';
import { QualityScore } from '../components/evidence/QualityScore';
import { ReplayPanel } from '../components/evidence/ReplayPanel';
import { Skeleton } from '../components/dashboard/DashboardPrimitives';
import { FindingVerificationPanel } from '../components/findings/FindingVerificationPanel';
import { FindingVerificationStatus } from '../components/findings/FindingVerificationStatus';
import { TermHint } from '../components/TermHint';
import { GLOSSARY } from '../lib/glossary';
import type { Finding } from '../types';

const ReplayViewer = lazy(() => import('../components/ReplayViewer'));

function moduleName(finding: Finding): string {
  return String(finding.business_impact?.module || finding.source_entity || finding.defect_family_label || '未归类').trim() || '未归类';
}

export function EvidenceChain() {
  usePageTitle('证据中心');
  const [params, setParams] = useSearchParams();
  const project = params.get('project')?.trim() || '';
  const requestedFindingId = params.get('finding')?.trim() || '';
  const requestedVerificationAt = params.get('verification_at')?.trim() || '';
  const { navigateToProjectPath } = useProjectNavigation();
  const { findings, clues, loading, error, refetch } = useFindingsData(project);
  const [replayFinding, setReplayFinding] = useState<Finding | null>(null);

  const customerFindings = findings.filter(isCustomerReadyFinding);
  const withEvidence = customerFindings.filter((finding) => finding.evidence_chain?.length > 0);
  const replayReady = withEvidence.filter((finding) => hasRealReplayAsset(finding)).length;
  const requestedFinding = requestedFindingId
    ? customerFindings.find((finding) => finding.id === requestedFindingId) || null
    : null;
  const selected = requestedFindingId
    ? withEvidence.find((finding) => finding.id === requestedFindingId) || null
    : withEvidence[0] || null;
  const confirmedWithoutEvidence = Math.max(0, customerFindings.length - withEvidence.length);
  const preserveRequestedRun = Boolean(selected && selected.id === requestedFindingId && requestedVerificationAt);
  const findingContextSearch = evidenceDeepLinkSearch(
    selected?.id || requestedFindingId,
    preserveRequestedRun ? requestedVerificationAt : '',
  );

  const selectFinding = (findingId: string) => {
    const next = new URLSearchParams(params);
    next.set('finding', findingId);
    next.delete('verification_at');
    setParams(next, { replace: true });
  };

  const showFirstEvidence = () => {
    const first = withEvidence[0];
    if (first) selectFinding(first.id);
  };

  return (
    <div>
      <div className="findings-page-head">
        <div>
          <h1>证据中心</h1>
          <span className="findings-count">
            {withEvidence.length} 个<TermHint label="证据包" hint={GLOSSARY.evidencePack} /> · {replayReady} 个可回放
            {confirmedWithoutEvidence > 0 && <> · {confirmedWithoutEvidence} 个已确认问题待形成证据包</>}
            {clues.length > 0 && <> · {clues.length} 条<TermHint label="待补证线索" hint={GLOSSARY.clue} /></>}
          </span>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          {customerFindings.length > 0 && <button className="btn btn-secondary" onClick={() => navigateToProjectPath('/findings', project, findingContextSearch)}>问题清单</button>}
          <button className="btn btn-primary" onClick={() => navigateToProjectPath('/release', project, findingContextSearch)}>发布门禁</button>
        </div>
      </div>

      {loading && (
        <div className="evidence-layout" aria-busy="true" aria-label="正在整理证据链">
          <div className="evidence-list-panel">
            {[1, 2, 3, 4].map((index) => (
              <div key={index} className="evidence-list-item">
                <Skeleton h={14} w="80%" br={4} />
                <div style={{ marginTop: 8 }}><Skeleton h={11} w="55%" br={4} /></div>
              </div>
            ))}
          </div>
          <div className="evidence-detail-panel">
            <Skeleton h={20} w="50%" br={4} />
            <div style={{ marginTop: 16 }}><Skeleton h={80} w="100%" br={8} /></div>
            <div style={{ marginTop: 16 }}><Skeleton h={120} w="100%" br={8} /></div>
          </div>
        </div>
      )}

      {!loading && error && (
        <section className="findings-empty-state danger">
          <span className="findings-empty-kicker">证据读取异常</span>
          <h3>当前无法确认可交付证据状态</h3>
          <p>{error}。读取失败不能解释为“没有证据”或“没有问题”。</p>
          <div className="settings-actions">
            <button className="btn btn-primary" onClick={refetch}>重新读取</button>
            <button className="btn btn-secondary" onClick={() => navigateToProjectPath('/dashboard', project)}>返回价值总览</button>
          </div>
        </section>
      )}

      {!loading && !error && customerFindings.length === 0 && (
        <section className="findings-empty-state compact">
          <span className="findings-empty-kicker">当前无已确认问题</span>
          <h3>当前没有可交付证据包</h3>
          <p>{clues.length > 0
            ? `当前仅有 ${clues.length} 条待验证线索，尚未形成客户可验收的缺陷与证据闭环。`
            : '当前没有具备客户交付条件的已确认缺陷。空证据中心不等于系统没有问题，仍需结合本轮覆盖状态判断。'}
          </p>
          <div className="settings-actions">
            <button className="btn btn-primary" onClick={() => navigateToProjectPath('/campaigns', project)}>继续检测</button>
            <button className="btn btn-secondary" onClick={() => navigateToProjectPath('/coverage', project)}>查看覆盖范围</button>
            <button className="btn btn-secondary" onClick={() => navigateToProjectPath('/dashboard', project)}>返回价值总览</button>
          </div>
        </section>
      )}

      {!loading && !error && customerFindings.length > 0 && withEvidence.length === 0 && (
        <section className="findings-empty-state danger">
          <span className="findings-empty-kicker">证据尚未形成</span>
          <h3>{customerFindings.length} 个已确认问题当前没有可展示证据包</h3>
          <p>这里不会把“有已确认问题但证据包不可展示”降级成普通空态。请先回到问题清单核对问题状态，或重新读取证据数据。</p>
          <div className="settings-actions">
            <button className="btn btn-primary" onClick={() => navigateToProjectPath('/findings', project, findingContextSearch)}>查看问题清单</button>
            <button className="btn btn-secondary" onClick={refetch}>重新读取</button>
            <button className="btn btn-secondary" onClick={() => navigateToProjectPath('/dashboard', project)}>返回价值总览</button>
          </div>
        </section>
      )}

      {!loading && !error && withEvidence.length > 0 && (
        <div className="evidence-layout">
          <div className="evidence-list-panel">
            {withEvidence.map((finding) => (
              <div
                key={finding.id}
                role="button"
                tabIndex={0}
                aria-pressed={selected?.id === finding.id}
                className={`evidence-list-item${selected?.id === finding.id ? ' active' : ''}`}
                onClick={() => selectFinding(finding.id)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    selectFinding(finding.id);
                  }
                }}
              >
                <h4>
                  <span className={`severity-badge ${finding.severity.toLowerCase()}`} style={{ marginRight: 6 }}>{finding.severity}</span>
                  {finding.title}
                </h4>
                <span>{moduleName(finding)} · 证据 {evidenceScoreLabel(finding)} · {hasRealReplayAsset(finding) ? '可回放' : '待补充'}</span>
                <div style={{ marginTop: 8 }}><FindingVerificationStatus finding={finding} compact /></div>
              </div>
            ))}
          </div>

          <div className="evidence-detail-panel">
            {!selected && requestedFindingId && (
              <section className="findings-empty-state compact">
                <span className="findings-empty-kicker">指定问题</span>
                <h3>{requestedFinding ? '该问题当前还没有可展示证据包' : '指定问题已不在当前已确认结果中'}</h3>
                <p>{requestedFinding
                  ? '证据中心不会静默切换到另一条问题来冒充当前证据。可以回到问题清单核对该问题，或查看当前第一条真实证据包。'
                  : '链接中的问题标识可能来自旧结果。当前不会用其他 Finding 的证据替代它。'}
                </p>
                <div className="settings-actions">
                  <button className="btn btn-primary" onClick={() => navigateToProjectPath('/findings', project, findingContextSearch)}>返回问题清单</button>
                  <button className="btn btn-secondary" onClick={showFirstEvidence}>查看第一条真实证据</button>
                </div>
              </section>
            )}

            {selected && (
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16 }}>
                  <span className={`severity-badge ${selected.severity.toLowerCase()}`}>{selected.severity}</span>
                  <h2 style={{ font: '700 18px var(--font-display)', flex: 1 }}>{selected.title}</h2>
                </div>
                <FindingVerificationStatus finding={selected} />
                <QualityScore finding={selected} />
                <h4 style={{ fontSize: 13, fontWeight: 700, margin: '16px 0 8px' }}>预期 vs 实际</h4>
                <AssertionDiff comparison={selected.expected_actual_comparison} expected={selected.expected} actual={selected.actual} />
                <ReplayPanel finding={selected} project={project} onReplay={setReplayFinding} />

                {selected.evidence_chain.length > 0 && (
                  <div style={{ marginTop: 16 }}>
                    <h4 style={{ fontSize: 13, fontWeight: 700, marginBottom: 8 }}>证据时间线</h4>
                    <EvidenceTimeline steps={selected.evidence_chain} />
                  </div>
                )}

                <div style={{ marginTop: 16 }}>
                  <h4 style={{ fontSize: 13, fontWeight: 700, marginBottom: 8 }}>业务影响</h4>
                  <p style={{ fontSize: 13, color: 'var(--muted)' }}>{selected.business_impact?.summary || selected.business_summary || '该问题已形成确认结论。'}</p>
                </div>

                <FindingVerificationPanel
                  finding={selected}
                  focusGeneratedAt={preserveRequestedRun ? requestedVerificationAt : ''}
                />

                {selected.regression?.included_in_suite && (
                  <div className="settings-actions mt-3">
                    <button className="btn btn-primary" onClick={() => navigateToProjectPath('/findings', project, findingContextSearch)}>回到这条问题并重新验证</button>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {replayFinding && (
        <Suspense fallback={<div className="replay-loading"><div className="spinner spinner-centered" /></div>}>
          <ReplayViewer projectId={project} finding={replayFinding} onClose={() => setReplayFinding(null)} />
        </Suspense>
      )}
    </div>
  );
}

export default EvidenceChain;
