import { useState } from 'react';
import { Link } from 'react-router-dom';
import { deriveFindingVerification } from '../../lib/finding-verification';
import { buildProjectPath } from '../../lib/project-navigation';
import { FindingDecisionSnapshot } from './FindingDecisionSnapshot';
import { FindingVerificationPanel } from './FindingVerificationPanel';
import { FindingVerificationStatus } from './FindingVerificationStatus';
import type { Finding } from '../../types';

interface FindingCardProps {
  finding: Finding;
  project: string;
  expanded: boolean;
  onToggle: () => void;
  onViewEvidence: () => void;
  reverifyRunning?: boolean;
  onReverify?: () => void;
  focusGeneratedAt?: string;
}

function moduleName(finding: Finding): string {
  return String(finding.business_impact?.module || finding.source_entity || finding.defect_family_label || '未归类').trim() || '未归类';
}

function findingSummary(finding: Finding): string {
  const verification = deriveFindingVerification(finding);
  const lines = [
    `[${finding.severity}] ${finding.title}`,
    `问题 ID：${finding.id}`,
    `影响模块：${moduleName(finding)}`,
    `业务影响：${finding.business_summary || finding.business_impact?.summary || finding.actual || '未上报'}`,
    `预期：${finding.expected || '未指定'}`,
    `实际：${finding.actual || '未捕获'}`,
    `证据质量：${finding.evidence_quality?.label || '未评分'}${finding.evidence_quality?.score != null ? `（${finding.evidence_quality.score}）` : ''}`,
    `复现率：${finding.proof?.repro_rate != null ? `${finding.proof.repro_rate}%` : '未上报'}`,
    `QualiBug 验证状态：${verification.label}`,
  ];

  if (finding.reproduction?.steps?.length) {
    lines.push('复现步骤：');
    finding.reproduction.steps.forEach((step, index) => lines.push(`${index + 1}. ${step}`));
  }
  if (finding.regression_verification_obligations?.length) {
    lines.push(`修复后验证义务：${finding.regression_verification_obligations.join('；')}`);
  }
  return lines.join('\n');
}

export function FindingCard({
  finding,
  project,
  expanded,
  onToggle,
  onViewEvidence,
  reverifyRunning = false,
  onReverify,
  focusGeneratedAt = '',
}: FindingCardProps) {
  const quality = finding.evidence_quality;
  const [copyStatus, setCopyStatus] = useState('');
  const detailHref = buildProjectPath(`/findings/${encodeURIComponent(finding.id)}`, project);

  const copySummary = async () => {
    try {
      await navigator.clipboard.writeText(findingSummary(finding));
      setCopyStatus('已复制问题与验证摘要');
    } catch {
      setCopyStatus('复制失败，请展开后手动复制问题信息');
    }
    window.setTimeout(() => setCopyStatus(''), 2500);
  };

  return (
    <article className={`finding-card severity-${finding.severity.toLowerCase()}`}>
      <div className="finding-card-main" onClick={onToggle}>
        <div className="finding-card-top">
          <span className={`severity-badge ${finding.severity.toLowerCase()}`}>{finding.severity}</span>
          <span className="finding-card-title">{finding.title}</span>
        </div>
        <div className="finding-card-meta">
          <span>模块 <b>{moduleName(finding)}</b></span>
          <span>证据 <b>{quality?.label || '未评分'}</b></span>
          <span>复现 <b>{finding.proof?.repro_rate != null ? `${finding.proof.repro_rate}%` : '未上报'}</b></span>
          <FindingVerificationStatus finding={finding} compact />
        </div>
        <div className="finding-card-actions" onClick={(event) => event.stopPropagation()}>
          <Link className="btn btn-secondary btn-sm" to={detailHref}>查看详情</Link>
          <button className="btn btn-secondary btn-sm" onClick={onViewEvidence}>查看证据</button>
          <button className="btn btn-secondary btn-sm" onClick={() => void copySummary()}>复制问题摘要</button>
          <button className="btn btn-secondary btn-sm" onClick={onToggle}>{expanded ? '收起' : '展开详情'}</button>
        </div>
        {copyStatus && <div className="settings-inline-feedback" role="status">{copyStatus}</div>}
      </div>

      {expanded && (
        <div className="finding-card-expand">
          <FindingDecisionSnapshot finding={finding} />

          <details className="settings-auth-section mt-3">
            <summary>
              <strong>查看预期 / 实际与复现细节</strong>
              <span className="muted">需要进一步核对时展开</span>
            </summary>
            <div className="mt-3">
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
                    {finding.reproduction.steps.map((step, index) => <li key={index}>{step}</li>)}
                  </ol>
                </div>
              )}
            </div>
          </details>

          <section className="customer-secondary-grid mt-3" aria-label="问题证据与产品边界">
            <article className="customer-secondary-card">
              <span className="customer-value-kicker">问题摘要</span>
              <h3>只复制可复现、可验收的信息</h3>
              <p>摘要只包含问题事实、证据、复现步骤和修复后验证义务，不混入企业内部研发流程。</p>
              <button type="button" className="btn btn-secondary settings-btn-mini" onClick={() => void copySummary()}>复制问题与验证摘要</button>
            </article>

            <article className="customer-secondary-card">
              <span className="customer-value-kicker">产品责任边界</span>
              <h3>只判断验证结果，不管理修复过程</h3>
              <p>QualiBug 不记录企业内部负责人、修复版本、研发进度或工单流转。客户修复后，QualiBug 只重新执行真实验证并更新问题与发布判断。</p>
            </article>
          </section>

          <FindingVerificationPanel
            finding={finding}
            running={reverifyRunning}
            onReverify={onReverify}
            focusGeneratedAt={focusGeneratedAt}
          />
        </div>
      )}
    </article>
  );
}

export default FindingCard;
