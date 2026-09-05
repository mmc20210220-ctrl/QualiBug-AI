import { lazy, Suspense, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { useFindingDetail } from '../api/finding-detail';
import { FindingDecisionSnapshot } from '../components/findings/FindingDecisionSnapshot';
import { FindingVerificationPanel } from '../components/findings/FindingVerificationPanel';
import { AssertionDiff } from '../components/evidence/AssertionDiff';
import { EvidenceDistributionTools } from '../components/evidence/EvidenceDistributionTools';
import { EvidenceTimeline } from '../components/EvidenceTimeline';
import { Skeleton } from '../components/dashboard/DashboardPrimitives';
import { buildProjectPath } from '../lib/project-navigation';
import { buildFindingEvidencePackageText } from '../lib/finding-evidence-package';
import { usePageTitle } from '../lib/page-title';
import type { Finding } from '../types';
import './FindingDetail.css';

const ReplayViewer = lazy(() => import('../components/ReplayViewer'));

function formatTimestamp(value: string | undefined): string {
  const text = String(value || '').trim();
  if (!text) return '未上报';
  const date = new Date(text);
  if (Number.isNaN(date.getTime())) return text;
  return date.toLocaleString('zh-CN', { hour12: false });
}

function moduleName(finding: Finding): string {
  return String(finding.business_impact?.module || finding.source_entity || finding.defect_family_label || '未归类').trim() || '未归类';
}

export function FindingDetail() {
  const { id = '' } = useParams();
  const [params] = useSearchParams();
  const project = params.get('project')?.trim() || '';
  const { finding, loading, error, refetch } = useFindingDetail(project, id);
  const [replayOpen, setReplayOpen] = useState(false);
  const [copyStatus, setCopyStatus] = useState('');

  usePageTitle(finding ? finding.title : '问题详情');

  const copyProblem = async () => {
    if (!finding) return;
    try {
      await navigator.clipboard.writeText(buildFindingEvidencePackageText(finding));
      setCopyStatus('已复制问题（含证据与复现，已脱敏）');
    } catch {
      setCopyStatus('复制失败，请使用下方证据工具或手动复制');
    }
    window.setTimeout(() => setCopyStatus(''), 2500);
  };

  const backHref = buildProjectPath('/findings', project);

  if (loading) {
    return (
      <div>
        <div className="page-header"><div><Skeleton h={14} w={120} br={4} /><div className="finding-skeleton-title"><Skeleton h={28} w="55%" br={6} /></div></div></div>
        <section className="card"><Skeleton h={16} w="80%" br={4} /><div className="finding-skeleton-line"><Skeleton h={14} w="60%" br={4} /></div></section>
      </div>
    );
  }

  if (error && !finding) {
    return (
      <section className="state-panel">
        <div className="state-panel-badge">连接异常</div>
        <h2>问题数据暂时不可用</h2>
        <p>{error}</p>
        <button type="button" className="btn btn-primary" onClick={() => void refetch()}>重新连接</button>
      </section>
    );
  }

  if (!finding) {
    return (
      <section className="state-panel">
        <div className="state-panel-badge">指定问题</div>
        <h2>该问题已不在当前已确认结果中</h2>
        <p>链接中的问题标识可能来自旧扫描或状态已变化。QualiBug 不会用标题相似的问题代替它。</p>
        <Link className="btn btn-primary" to={backHref}>返回问题列表</Link>
      </section>
    );
  }

  const chain = finding.evidence_chain || [];
  const raw = finding.raw_evidence;
  const reproduction = finding.reproduction;
  const comparison = finding.expected_actual_comparison;
  const hasExpectedSource = Boolean(comparison?.expected || finding.expected);
  const businessSummary = finding.business_summary || finding.business_impact?.summary || '当前问题未上报业务摘要，请结合预期、实际与证据判断。';

  return (
    <div className="finding-detail finding-investigation">
      <header className="finding-investigation-header">
        <div className="finding-investigation-title">
          <span className="panel-kicker">Finding · Evidence-backed Investigation</span>
          <div className="finding-investigation-heading">
            <span className={`severity-badge ${finding.severity.toLowerCase()}`}>{finding.severity}</span>
            <h1>{finding.title}</h1>
          </div>
          <p>{businessSummary}</p>
          <div className="finding-investigation-meta">
            <span>模块 <strong>{moduleName(finding)}</strong></span>
            <span>发现 <strong>{formatTimestamp(finding.timestamp)}</strong></span>
            <span>证据 <strong>{chain.length} 条</strong></span>
            {finding.regression?.last_run_at && <span>最近验证 <strong>{formatTimestamp(finding.regression.last_run_at)}</strong></span>}
          </div>
        </div>
        <div className="finding-investigation-actions">
          <button type="button" className="btn btn-primary" onClick={() => setReplayOpen(true)}>重新验证</button>
          <button type="button" className="btn btn-secondary" onClick={() => void copyProblem()}>复制证据包</button>
          <Link className="btn btn-secondary" to={backHref}>返回问题列表</Link>
        </div>
      </header>

      {copyStatus && <div className="settings-inline-feedback" role="status">{copyStatus}</div>}

      <FindingDecisionSnapshot finding={finding} />

      <div className="finding-investigation-grid">
        <main className="finding-investigation-main">
          <section className="finding-investigation-card" aria-label="预期与实际">
            <div className="finding-investigation-card-head">
              <span className="panel-kicker">Expected vs Actual</span>
              <h2>为什么这是一个问题</h2>
              <p>只使用已有业务规则来源与真实运行结果；缺少可靠预期时不会由前端猜测补齐。</p>
            </div>
            {hasExpectedSource ? (
              <AssertionDiff comparison={comparison} expected={finding.expected} actual={finding.actual} />
            ) : (
              <p className="settings-inline-feedback">预期行为：当前没有可靠业务规则来源，QualiBug 不会根据前端猜测补全预期。</p>
            )}
          </section>

          <section className="finding-investigation-card" aria-label="复现步骤">
            <div className="finding-investigation-card-head">
              <span className="panel-kicker">Reproduction</span>
              <h2>真实执行轨迹中的复现步骤</h2>
            </div>
            {reproduction?.steps?.length ? (
              <ol className="finding-reproduction-list">
                {reproduction.steps.map((step, index) => <li key={index}><span>{index + 1}</span><p>{step}</p></li>)}
              </ol>
            ) : (
              <p className="settings-inline-feedback">复现步骤：未上报。复现步骤必须来自真实执行轨迹，QualiBug 不会生成不存在的步骤。</p>
            )}
          </section>

          <section className="finding-investigation-card evidence-card" aria-label="证据链">
            <div className="finding-investigation-card-head evidence-heading">
              <div>
                <span className="panel-kicker">Evidence</span>
                <h2>完整证据链</h2>
              </div>
              <span className="finding-evidence-count">{chain.length} 条真实证据</span>
            </div>
            {chain.length > 0 ? (
              <EvidenceTimeline steps={chain} />
            ) : (
              <p className="settings-inline-feedback">当前问题没有可展示的 evidence_chain；不会用摘要替代缺失的真实证据链。</p>
            )}
          </section>
        </main>

        <aside className="finding-investigation-side">
          <section className="finding-side-card">
            <span className="panel-kicker">Business Impact</span>
            <h3>业务影响</h3>
            <p>{finding.business_impact?.summary || finding.business_summary || '后端未上报业务影响摘要。'}</p>
            <div className="finding-side-facts">
              <div><span>影响模块</span><strong>{moduleName(finding)}</strong></div>
              <div><span>紧急程度</span><strong>{finding.business_impact?.urgency || '未上报'}</strong></div>
              <div><span>证据质量</span><strong>{finding.evidence_quality?.label || '未上报'}</strong></div>
              <div><span>置信度</span><strong>{typeof finding.confidence === 'number' ? `${finding.confidence}%` : '未上报'}</strong></div>
            </div>
          </section>

          <section className="finding-side-card">
            <span className="panel-kicker">Raw Evidence</span>
            <h3>运行时观测</h3>
            {raw ? (
              <div className="finding-raw-evidence">
                <div><span>Request</span><strong>{raw.request_raw?.method && raw.request_raw?.path ? `${raw.request_raw.method} ${raw.request_raw.path}` : '未上报'}</strong></div>
                <div><span>Response</span><strong>{raw.response_raw?.status_code ? `HTTP ${raw.response_raw.status_code}` : '未上报'}</strong></div>
                <div><span>Database</span><strong>{raw.db_snapshot?.table ? `${raw.db_snapshot.table}.${raw.db_snapshot.column || ''}` : '未上报'}</strong></div>
                <div><span>Trace</span><strong>{raw.logs?.trace_id || raw.execution_trace?.evidence_hash || '未上报'}</strong></div>
                {raw.has_real_evidence === false && <p className="settings-inline-feedback">后端明确标记当前没有真实运行证据；这里如实展示，不构造证据。</p>}
              </div>
            ) : (
              <p className="settings-inline-feedback">原始运行证据未上报。</p>
            )}
          </section>

          <section className="finding-side-card verification-card">
            <span className="panel-kicker">Verification</span>
            <h3>修复后验证状态</h3>
            <FindingVerificationPanel finding={finding} />
          </section>
        </aside>
      </div>

      <section className="finding-evidence-tools">
        <EvidenceDistributionTools finding={finding} project={project} />
      </section>

      {replayOpen && (
        <Suspense fallback={<div className="replay-loading"><div className="spinner spinner-centered" /></div>}>
          <ReplayViewer
            projectId={project}
            finding={finding}
            onClose={() => {
              setReplayOpen(false);
              void refetch();
            }}
          />
        </Suspense>
      )}
    </div>
  );
}

export default FindingDetail;
