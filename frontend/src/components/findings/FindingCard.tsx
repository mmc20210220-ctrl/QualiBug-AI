import { useState } from 'react';
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

function handoffSummary(finding: Finding): string {
  const regression = finding.regression;
  const lines = [
    `[${finding.severity}] ${finding.title}`,
    `问题 ID：${finding.id}`,
    `影响模块：${moduleName(finding)}`,
    `业务影响：${finding.business_summary || finding.business_impact?.summary || finding.actual || '未上报'}`,
    `预期：${finding.expected || '未指定'}`,
    `实际：${finding.actual || '未捕获'}`,
    `证据质量：${finding.evidence_quality?.label || '未评分'}${finding.evidence_quality?.score != null ? `（${finding.evidence_quality.score}）` : ''}`,
    `复现率：${finding.proof?.repro_rate != null ? `${finding.proof.repro_rate}%` : '未上报'}`,
    `回归状态：${regression?.lifecycle_label || regressionStatusLabel(finding)}`,
  ];

  if (finding.reproduction?.steps?.length) {
    lines.push('复现步骤：');
    finding.reproduction.steps.forEach((step, index) => lines.push(`${index + 1}. ${step}`));
  }
  if (finding.investigation_guidance?.relevant_apis?.length) {
    lines.push(`相关接口：${finding.investigation_guidance.relevant_apis.join('、')}`);
  }
  if (finding.investigation_guidance?.relevant_tables?.length) {
    lines.push(`相关表：${finding.investigation_guidance.relevant_tables.join('、')}`);
  }
  if (finding.investigation_guidance?.trace_id) {
    lines.push(`Trace ID：${finding.investigation_guidance.trace_id}`);
  }
  if (finding.regression_verification_obligations?.length) {
    lines.push(`修复后验收：${finding.regression_verification_obligations.join('；')}`);
  }
  return lines.join('\n');
}

export function FindingCard({ finding, expanded, onToggle, onViewEvidence }: FindingCardProps) {
  const quality = finding.evidence_quality;
  const impact = finding.business_summary || finding.business_impact?.summary || finding.actual || '该问题已形成可交付缺陷。';
  const regTone = regressionTone(finding);
  const [copyStatus, setCopyStatus] = useState('');

  const copyHandoff = async () => {
    try {
      await navigator.clipboard.writeText(handoffSummary(finding));
      setCopyStatus('已复制研发交接摘要');
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
          <span>回归 <b className={regTone}>{regressionStatusLabel(finding)}</b></span>
        </div>
        <div className="finding-card-actions" onClick={(e) => e.stopPropagation()}>
          <button className="btn btn-secondary btn-sm" onClick={onViewEvidence}>查看证据</button>
          <button className="btn btn-secondary btn-sm" onClick={() => void copyHandoff()}>复制研发交接</button>
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
                {finding.reproduction.steps.map((step, i) => <li key={i}>{step}</li>)}
              </ol>
            </div>
          )}

          <section className="customer-secondary-grid mt-3" aria-label="问题协作与回归闭环">
            <article className="customer-secondary-card">
              <span className="customer-value-kicker">研发交接</span>
              <h3>证据摘要可直接交给研发定位</h3>
              <p>
                摘要来自本条 Finding 的业务影响、预期/实际、复现步骤、相关接口/数据表、Trace ID 与修复后验收义务，不生成额外结论。
              </p>
              <button type="button" className="btn btn-secondary settings-btn-mini" onClick={() => void copyHandoff()}>复制研发交接摘要</button>
            </article>

            <article className="customer-secondary-card">
              <span className="customer-value-kicker">回归闭环</span>
              {finding.regression ? (
                <>
                  <h3 className={regTone}>{finding.regression.lifecycle_label || regressionStatusLabel(finding)}</h3>
                  <p>
                    最近状态：{finding.regression.latest_status_label || finding.regression.latest_status || '未报告'}
                    {finding.regression.last_run_at ? ` · 最近执行 ${finding.regression.last_run_at}` : ''}
                    {finding.regression.gate_status ? ` · 门禁 ${finding.regression.gate_status}` : ''}
                    {finding.regression.history_count > 0 ? ` · 历史 ${finding.regression.history_count} 次` : ''}
                  </p>
                  {finding.regression.reason && <p className="muted">{finding.regression.reason}</p>}
                </>
              ) : (
                <>
                  <h3>尚未纳入回归套件</h3>
                  <p>当前后端未返回该问题的回归合同，因此不会显示成“待通过”或“已关闭”。</p>
                </>
              )}
            </article>
          </section>

          <p className="settings-hint mt-3">
            负责人、处理状态、修复版本、研发反馈、风险接受和误报结论需要后端持久化协作合同；当前前端不使用浏览器本地状态伪装这些企业协作字段。
          </p>
        </div>
      )}
    </article>
  );
}
