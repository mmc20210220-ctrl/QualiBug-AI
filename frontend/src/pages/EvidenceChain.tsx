import { lazy, Suspense, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { EvidenceTimeline } from '../components/EvidenceTimeline';
import { hasRealReplayAsset, isCustomerReadyFinding, useFindingsData } from '../api/data';
import { usePageTitle } from '../lib/page-title';
import { useProjectNavigation } from '../lib/project-navigation';
import { AssertionDiff } from '../components/evidence/AssertionDiff';
import { QualityScore } from '../components/evidence/QualityScore';
import { ReplayPanel } from '../components/evidence/ReplayPanel';
import { Skeleton } from '../components/dashboard/DashboardPrimitives';
import { TermHint } from '../components/TermHint';
import { GLOSSARY } from '../lib/glossary';
import type { Finding } from '../types';

const ReplayViewer = lazy(() => import('../components/ReplayViewer'));

function moduleName(finding: Finding): string {
  return String(finding.business_impact?.module || finding.source_entity || finding.defect_family_label || '未归类').trim() || '未归类';
}

export function EvidenceChain() {
  usePageTitle('证据中心');
  const [params] = useSearchParams();
  const project = params.get('project')?.trim() || '';
  const { navigateToProjectPath } = useProjectNavigation();
  const { findings, clues, loading } = useFindingsData(project);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [replayFinding, setReplayFinding] = useState<Finding | null>(null);

  const customerFindings = findings.filter(isCustomerReadyFinding);
  const withEvidence = customerFindings.filter((f) => f.evidence_chain?.length > 0);
  const replayReady = withEvidence.filter((f) => hasRealReplayAsset(f)).length;
  const selected = withEvidence.find((f) => f.id === selectedId) || null;

  return (
    <div>
      <div className="findings-page-head">
        <div>
          <h1>证据中心</h1>
          <span className="findings-count">
            {withEvidence.length} 个<TermHint label="证据包" hint={GLOSSARY.evidencePack} /> · {replayReady} 个可回放
            {clues.length > 0 && <> · {clues.length} 条<TermHint label="待补证线索" hint={GLOSSARY.clue} /></>}
          </span>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn btn-secondary" onClick={() => navigateToProjectPath('/findings', project)}>问题清单</button>
          <button className="btn btn-primary" onClick={() => navigateToProjectPath('/release', project)}>发布门禁</button>
        </div>
      </div>

      {loading && (
        <div className="evidence-layout" aria-busy="true" aria-label="正在整理证据链">
          <div className="evidence-list-panel">
            {[1, 2, 3, 4].map((i) => (
              <div key={i} className="evidence-list-item">
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

      {!loading && withEvidence.length === 0 && (
        <section className="findings-empty-state compact">
          <span className="findings-empty-kicker">当前结论</span>
          <h3>当前没有可交付证据包</h3>
          <p>{clues.length > 0 ? `当前仅有 ${clues.length} 条待验证线索，尚未形成客户可验收的证据闭环。` : '运行扫描并形成已验证缺陷后，这里会自动展示真实可交付的证据链。'}</p>
          <button className="btn btn-primary" onClick={() => navigateToProjectPath('/dashboard', project)}>返回价值总览</button>
        </section>
      )}

      {!loading && withEvidence.length > 0 && (
        <div className="evidence-layout">
          <div className="evidence-list-panel">
            {withEvidence.map((f) => (
              <div
                key={f.id}
                role="button"
                tabIndex={0}
                aria-pressed={selectedId === f.id}
                className={`evidence-list-item${selectedId === f.id ? ' active' : ''}`}
                onClick={() => setSelectedId(f.id)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    setSelectedId(f.id);
                  }
                }}
              >
                <h4>
                  <span className={`severity-badge ${f.severity.toLowerCase()}`} style={{ marginRight: 6 }}>{f.severity}</span>
                  {f.title}
                </h4>
                <span>{moduleName(f)} · 证据 {f.evidence_quality?.score ?? 0}/100 · {hasRealReplayAsset(f) ? '可回放' : '待补充'}</span>
              </div>
            ))}
          </div>

          <div className="evidence-detail-panel">
            {!selected ? (
              <div className="evidence-detail-empty">← 选择左侧证据包查看详情</div>
            ) : (
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16 }}>
                  <span className={`severity-badge ${selected.severity.toLowerCase()}`}>{selected.severity}</span>
                  <h2 style={{ font: '700 18px var(--font-display)', flex: 1 }}>{selected.title}</h2>
                </div>
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
                {selected.regression && (
                  <div style={{ marginTop: 16, fontSize: 12, color: 'var(--muted)' }}>
                    <h4 style={{ fontSize: 13, fontWeight: 700, marginBottom: 6, color: 'var(--ink)' }}>回归闭环</h4>
                    <p>生命周期：{selected.regression.lifecycle_label || '待回归'}</p>
                    <p>最新状态：{selected.regression.latest_status_label || '未执行'}</p>
                    <p>{selected.regression.lifecycle_description || selected.regression.reason || '等待后端上报回归结果。'}</p>
                    {selected.regression.history?.length > 0 && (
                      <p>最近轨迹：{selected.regression.history.map((item) => `[${item.generated_at || '未知时间'}] ${item.status_label || item.gate_status || '回归'}`).join(' -> ')}</p>
                    )}
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
