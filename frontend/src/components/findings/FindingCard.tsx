import type { Finding } from '../../types';

interface FindingCardProps {
  finding: Finding;
  expanded: boolean;
  onToggle: () => void;
  onViewEvidence: () => void;
}

function moduleName(finding: Finding): string {
  return String(finding.business_impact?.module || finding.source_entity || finding.defect_family_label || '未归类').trim() || '未归类';
}

function regressionStatusLabel(finding: Finding): string {
  const r = finding.regression;
  if (!r) return '未纳入回归';
  if (r.latest_status === 'passed') return '已通过';
  if (r.latest_status === 'failed') return '仍失败';
  if (r.included_in_suite) return '待执行';
  return '未纳入回归';
}

function regressionTone(finding: Finding): string {
  const r = finding.regression;
  if (!r) return '';
  if (r.latest_status === 'passed') return 'success';
  if (r.latest_status === 'failed') return 'danger';
  return '';
}

export function FindingCard({ finding, expanded, onToggle, onViewEvidence }: FindingCardProps) {
  const quality = finding.evidence_quality;
  const impact = finding.business_summary || finding.business_impact?.summary || finding.actual || '该问题已形成可交付缺陷。';
  const regTone = regressionTone(finding);

  return (
    <article className={`finding-card severity-${finding.severity.toLowerCase()}`}>
      <div className="finding-card-main" onClick={onToggle}>
        <div className="finding-card-top">
          <span className={`severity-badge ${finding.severity.toLowerCase()}`}>{finding.severity}</span>
          <span className="finding-card-title">{finding.title}</span>
        </div>
        <div className="finding-card-meta">
          <span>模块 <b>{moduleName(finding)}</b></span>
          <span>证据 <b>{quality?.label || '已归档'}</b></span>
          <span>复现 <b>{finding.proof?.repro_rate ?? 0}%</b></span>
          <span>回归 <b className={regTone}>{regressionStatusLabel(finding)}</b></span>
        </div>
        <div className="finding-card-actions" onClick={(e) => e.stopPropagation()}>
          <button className="btn btn-secondary btn-sm" onClick={onViewEvidence}>查看证据</button>
          <button className="btn btn-secondary btn-sm" onClick={onToggle}>{expanded ? '收起' : '展开详情'}</button>
        </div>
      </div>
      {expanded && (
        <div className="finding-card-expand">
          <p style={{ marginBottom: 12, fontSize: 13, color: 'var(--muted)' }}>{impact}</p>
          <div className="assertion-diff">
            <div className="assertion-diff-row">
              <span className="assertion-diff-label expected">预期</span>
              <span className="assertion-diff-value">{finding.expected || '未指定'}</span>
            </div>
            <div className="assertion-diff-row">
              <span className="assertion-diff-label actual">实际</span>
              <span className="assertion-diff-value">{finding.actual || '未捕获'}</span>
            </div>
          </div>
          {finding.reproduction?.steps?.length > 0 && (
            <div style={{ marginTop: 12 }}>
              <strong style={{ fontSize: 12, color: 'var(--subtle)' }}>复现步骤</strong>
              <ol style={{ fontSize: 13, paddingLeft: 18, marginTop: 6 }}>
                {finding.reproduction.steps.map((step, i) => <li key={i}>{step}</li>)}
              </ol>
            </div>
          )}
          {finding.regression && (
            <div style={{ marginTop: 12, fontSize: 12, color: 'var(--muted)' }}>
              <strong>回归状态：</strong>{finding.regression.lifecycle_label || regressionStatusLabel(finding)}
              {finding.regression.latest_status_label && ` · ${finding.regression.latest_status_label}`}
            </div>
          )}
        </div>
      )}
    </article>
  );
}
