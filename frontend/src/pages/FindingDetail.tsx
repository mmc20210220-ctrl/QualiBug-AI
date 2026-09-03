import { lazy, Suspense, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { useFindingsData } from '../api/data';
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
  const { findings, loading, error, refetch } = useFindingsData(project);
  const [replayOpen, setReplayOpen] = useState(false);
  const [copyStatus, setCopyStatus] = useState('');

  const finding = findings.find((item) => item.id === id) || null;

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
        <div className="page-header"><div><Skeleton h={14} w={120} br={4} /><div style={{ marginTop: 12 }}><Skeleton h={28} w="55%" br={6} /></div></div></div>
        <section className="card"><Skeleton h={16} w="80%" br={4} /><div style={{ marginTop: 12 }}><Skeleton h={14} w="60%" br={4} /></div></section>
      </div>
    );
  }

  if (error && !finding) {
    return (
      <section className="state-panel">
        <div className="state-panel-badge">连接异常</div>
        <h2>问题数据暂时不可用</h2>
        <p>{error}</p>
        <button type="button" className="btn btn-primary" onClick={refetch}>重新连接</button>
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

  return (
    <div className="finding-detail">
      <div className="page-header">
        <div>
          <span className="panel-kicker">问题详情</span>
          <h1>
            <span className={`severity-badge ${finding.severity.toLowerCase()}`}>{finding.severity}</span>
            {finding.title}
          </h1>
          <p className="muted">
            模块 {moduleName(finding)} · 发现时间 {formatTimestamp(finding.timestamp)}
            {finding.regression?.last_run_at ? ` · 最近回归 ${formatTimestamp(finding.regression.last_run_at)}` : ''}
          </p>
        </div>
        <div className="settings-actions">
          <button type="button" className="btn btn-primary" onClick={() => void copyProblem()}>复制问题</button>
          <button type="button" className="btn btn-secondary" onClick={() => setReplayOpen(true)}>重新验证</button>
          <Link className="btn btn-secondary" to={backHref}>返回问题列表</Link>
        </div>
      </div>

      {copyStatus && <div className="settings-inline-feedback" role="status">{copyStatus}</div>}

      <FindingDecisionSnapshot finding={finding} />

      <section className="card mt-3" aria-label="发生了什么">
        <div className="settings-card-head">
          <div>
            <span className="panel-kicker">发生了什么</span>
            <h3>用业务语言复述已捕获的异常</h3>
          </div>
        </div>
        <p>{finding.business_summary || finding.business_impact?.summary || '当前问题未上报业务摘要，请结合下方预期与实际判断。'}</p>
      </section>

      <section className="card mt-3" aria-label="预期与实际">
        <div className="settings-card-head">
          <div>
            <span className="panel-kicker">预期 vs 实际</span>
            <h3>判断问题为什么成立</h3>
          </div>
        </div>
        {hasExpectedSource ? (
          <AssertionDiff comparison={comparison} expected={finding.expected} actual={finding.actual} />
        ) : (
          <p className="settings-inline-feedback">预期行为：当前没有可靠业务规则来源，QualiBug 不会根据前端猜测补全预期。</p>
        )}
      </section>

      <section className="card mt-3" aria-label="复现步骤">
        <div className="settings-card-head">
          <div>
            <span className="panel-kicker">复现</span>
            <h3>真实执行轨迹中的复现步骤</h3>
          </div>
        </div>
        {reproduction?.steps?.length ? (
          <ol style={{ paddingLeft: 18, margin: 0, fontSize: 13, lineHeight: 1.9 }}>
            {reproduction.steps.map((step, index) => <li key={index}>{step}</li>)}
          </ol>
        ) : (
          <p className="settings-inline-feedback">复现步骤：未上报。复现步骤必须来自真实执行轨迹，QualiBug 不会生成不存在的步骤。</p>
        )}
      </section>

      <section className="card mt-3" aria-label="证据链">
        <div className="settings-card-head">
          <div>
            <span className="panel-kicker">证据</span>
            <h3>证据链与原始证据</h3>
          </div>
        </div>

        {chain.length > 0 ? (
          <EvidenceTimeline steps={chain} />
        ) : (
          <p className="settings-inline-feedback">当前问题没有可展示的 evidence_chain；不会用摘要替代缺失的真实证据链。</p>
        )}

        {raw && (
          <details className="settings-auth-section mt-3">
            <summary><strong>原始证据</strong> <span className="muted">Request / Response / DB / 日志 / 轨迹</span></summary>
            <div className="settings-grid mt-3">
              <div>
                <span className="muted">请求</span>
                <p>{raw.request_raw?.method && raw.request_raw?.path ? `${raw.request_raw.method} ${raw.request_raw.path}` : '未上报'}</p>
              </div>
              <div>
                <span className="muted">响应</span>
                <p>{raw.response_raw?.status_code ? `HTTP ${raw.response_raw.status_code}` : '未上报'}</p>
              </div>
              <div>
                <span className="muted">数据库</span>
                <p>{raw.db_snapshot?.table ? `${raw.db_snapshot.table}.${raw.db_snapshot.column || ''}` : '未上报'}</p>
              </div>
              <div>
                <span className="muted">Trace</span>
                <p>{raw.logs?.trace_id || raw.execution_trace?.evidence_hash || '未上报'}</p>
              </div>
            </div>
            {raw.has_real_evidence === false && (
              <p className="settings-inline-feedback">后端明确标记该问题当前没有真实运行证据；这里如实展示，不构造证据。</p>
            )}
          </details>
        )}
      </section>

      <FindingVerificationPanel finding={finding} />

      <EvidenceDistributionTools finding={finding} project={project} />

      {replayOpen && (
        <Suspense fallback={<div className="replay-loading"><div className="spinner spinner-centered" /></div>}>
          <ReplayViewer projectId={project} finding={finding} onClose={() => setReplayOpen(false)} />
        </Suspense>
      )}
    </div>
  );
}

export default FindingDetail;
