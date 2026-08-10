import { useState } from 'react';
import { FindingVerificationPanel } from './FindingVerificationPanel';
import type { Finding } from '../../types';

interface FindingCardProps {
  finding: Finding;
  expanded: boolean;
  onToggle: () => void;
  onViewEvidence: () => void;
  reverifyRunning?: boolean;
  onReverify?: () => void;
}

function moduleName(finding: Finding): string {
  return String(finding.business_impact?.module || finding.source_entity || finding.defect_family_label || '未归类').trim() || '未归类';
}

function regressionStatusLabel(finding: Finding): string {
  const regression = finding.regression;
  if (!regression?.included_in_suite) return '暂无重新验证义务';
  if (regression.latest_status === 'passed' || regression.latest_status === 'verified_fixed') return '修复验证通过';
  if (regression.latest_status === 'failed' || regression.latest_status === 'reopened') return '重新验证仍失败';
  if (['blocked', 'error', 'failed_safe', 'indeterminate', 'needs_review', 'not_executed', 'not_ready', 'skipped', 'unverifiable'].includes(String(regression.latest_status || '').toLowerCase())) return '本轮无法确认';
  return '等待修复后重新验证';
}

function regressionTone(finding: Finding): string {
  const status = String(finding.regression?.latest_status || '').toLowerCase();
  if (status === 'passed' || status === 'verified_fixed') return 'success';
  if (status === 'failed' || status === 'reopened') return 'danger';
  return '';
}

function findingSummary(finding: Finding): string {
  const lines = [
    `[${finding.severity}] ${finding.title}`,
    `问题 ID：${finding.id}`,
    `影响模块：${moduleName(finding)}`,
    `业务影响：${finding.business_summary || finding.business_impact?.summary || finding.actual || '未上报'}`,
    `预期：${finding.expected || '未指定'}`,
    `实际：${finding.actual || '未捕获'}`,
    `证据质量：${finding.evidence_quality?.label || '未评分'}${finding.evidence_quality?.score != null ? `（${finding.evidence_quality.score}）` : ''}`,
    `复现率：${finding.proof?.repro_rate != null ? `${finding.proof.repro_rate}%` : '未上报'}`,
    `QualiBug 验证状态：${finding.regression?.lifecycle_label || regressionStatusLabel(finding)}`,
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
  expanded,
  onToggle,
  onViewEvidence,
  reverifyRunning = false,
  onReverify,
}: FindingCardProps) {
  const quality = finding.evidence_quality;
  const impact = finding.business_summary || finding.business_impact?.summary || finding.actual || '该问题已形成可交付缺陷。';
  const regTone = regressionTone(finding);
  const [copyStatus, setCopyStatus] = useState('');

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
          <span>验证 <b className={regTone}>{regressionStatusLabel(finding)}</b></span>
        </div>
        <div className="finding-card-actions" onClick={(event) => event.stopPropagation()}>
          <button className="btn btn-secondary btn-sm" onClick={onViewEvidence}>查看证据</button>
          <button className="btn btn-secondary btn-sm" onClick={() => void copySummary()}>复制问题摘要</button>
          <button className="btn btn-secondary btn-sm" onClick={onToggle}>{expanded ? '收起' : '展开详情'}</button>
        </div>
        {copyStatus && <div className="settings-inline-feedback" role="status">{copyStatus}</div>}
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
                {finding.reproduction.steps.map((step, index) => <li key={index}>{step}</li>)}
              </ol>
            </div>
          )}

          <section className="customer-secondary-grid mt-3" aria-label="问题证据与产品边界">
            <article className="customer-secondary-card">
              <span className="customer-value-kicker">问题摘要</span>
              <h3>保留可复现、可验收的信息</h3>
              <p>摘要只包含 Finding 事实、证据、复现步骤和修复后验证义务。QualiBug 不记录企业内部负责人、修复版本、研发进度或工单流转。</p>
              <button type="button" className="btn btn-secondary settings-btn-mini" onClick={() => void copySummary()}>复制问题与验证摘要</button>
            </article>

            <article className="customer-secondary-card">
              <span className="customer-value-kicker">产品责任边界</span>
              <h3>只判断验证结果，不管理修复过程</h3>
              <p>客户如何组织研发、由谁修复、在哪个版本修复都属于企业自己的流程。QualiBug 只在客户修复后重新执行真实验证，并据此更新 Finding 与发布判断。</p>
            </article>
          </section>

          <FindingVerificationPanel finding={finding} running={reverifyRunning} onReverify={onReverify} />
        </div>
      )}
    </article>
  );
}

export default FindingCard;
