import { useEffect, useState } from 'react';
import {
  updateFindingCollaboration,
  type CollaborativeFindingProjection,
  type FindingDisposition,
  type FindingHandlingStatus,
} from '../../api/finding-collaboration';
import type { Finding } from '../../types';

interface FindingCardProps {
  finding: Finding;
  project: string;
  expanded: boolean;
  onToggle: () => void;
  onViewEvidence: () => void;
  onCollaborationUpdated?: () => Promise<void> | void;
}

type CollaborativeFinding = Finding & CollaborativeFindingProjection;

type CollaborationDraft = {
  handling_status: FindingHandlingStatus;
  assignee: string;
  fix_version: string;
  developer_feedback: string;
  disposition: FindingDisposition;
  disposition_note: string;
  external_issue_url: string;
};

const HANDLING_STATUS_OPTIONS: Array<{ value: FindingHandlingStatus; label: string }> = [
  { value: 'new', label: '待分诊' },
  { value: 'triaged', label: '已分诊' },
  { value: 'in_progress', label: '修复中' },
  { value: 'fix_ready', label: '待回归' },
  { value: 'risk_review', label: '风险评审' },
  { value: 'false_positive_review', label: '误报评审' },
];

const DISPOSITION_OPTIONS: Array<{ value: FindingDisposition; label: string }> = [
  { value: 'none', label: '未作人工处置' },
  { value: 'accepted_risk', label: '已接受风险' },
  { value: 'false_positive', label: '已标记误报' },
];

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

function verificationStatusLabel(status: string): string {
  switch (status) {
    case 'resolved': return '真实回放确认已修复';
    case 'falsified': return '真实验证已证伪';
    case 'open': return '真实验证仍打开';
    default: return status || '尚未绑定持久化状态';
  }
}

function initialDraft(finding: CollaborativeFinding): CollaborationDraft {
  const collaboration = finding.collaboration || {};
  const handlingStatus = collaboration.handling_status;
  const disposition = collaboration.disposition;
  return {
    handling_status: handlingStatus === 'triaged'
      || handlingStatus === 'in_progress'
      || handlingStatus === 'fix_ready'
      || handlingStatus === 'risk_review'
      || handlingStatus === 'false_positive_review'
      ? handlingStatus
      : 'new',
    assignee: collaboration.assignee || '',
    fix_version: collaboration.fix_version || '',
    developer_feedback: collaboration.developer_feedback || '',
    disposition: disposition === 'accepted_risk' || disposition === 'false_positive' ? disposition : 'none',
    disposition_note: collaboration.disposition_note || '',
    external_issue_url: collaboration.external_issue_url || '',
  };
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

export function FindingCard({
  finding,
  project,
  expanded,
  onToggle,
  onViewEvidence,
  onCollaborationUpdated,
}: FindingCardProps) {
  const collaborativeFinding = finding as CollaborativeFinding;
  const persistenceId = collaborativeFinding.finding_persistence_id || '';
  const verificationStatus = collaborativeFinding.verification_status || '';
  const quality = finding.evidence_quality;
  const impact = finding.business_summary || finding.business_impact?.summary || finding.actual || '该问题已形成可交付缺陷。';
  const regTone = regressionTone(finding);
  const [copyStatus, setCopyStatus] = useState('');
  const [collaborationDraft, setCollaborationDraft] = useState<CollaborationDraft>(() => initialDraft(collaborativeFinding));
  const [collaborationStatus, setCollaborationStatus] = useState('');
  const [collaborationSaving, setCollaborationSaving] = useState(false);

  useEffect(() => {
    setCollaborationDraft(initialDraft(finding as CollaborativeFinding));
    setCollaborationStatus('');
  }, [finding]);

  const copyHandoff = async () => {
    try {
      await navigator.clipboard.writeText(handoffSummary(finding));
      setCopyStatus('已复制研发交接摘要');
    } catch {
      setCopyStatus('复制失败，请展开后手动复制问题信息');
    }
    window.setTimeout(() => setCopyStatus(''), 2500);
  };

  const saveCollaboration = async () => {
    if (!persistenceId || !project || collaborationSaving) return;
    setCollaborationSaving(true);
    setCollaborationStatus('保存中…');
    try {
      const saved = await updateFindingCollaboration(project, persistenceId, collaborationDraft);
      setCollaborationDraft({
        handling_status: saved.handling_status,
        assignee: saved.assignee,
        fix_version: saved.fix_version,
        developer_feedback: saved.developer_feedback,
        disposition: saved.disposition,
        disposition_note: saved.disposition_note,
        external_issue_url: saved.external_issue_url,
      });
      setCollaborationStatus('已保存到项目协作记录');
      await onCollaborationUpdated?.();
    } catch (error: unknown) {
      setCollaborationStatus(error instanceof Error ? error.message : '协作记录保存失败');
    } finally {
      setCollaborationSaving(false);
    }
  };

  const updateDraft = <K extends keyof CollaborationDraft>(key: K, value: CollaborationDraft[K]) => {
    setCollaborationDraft((current) => ({ ...current, [key]: value }));
    if (collaborationStatus && collaborationStatus !== '保存中…') setCollaborationStatus('有未保存修改');
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
          {persistenceId && <span>处理 <b>{HANDLING_STATUS_OPTIONS.find((option) => option.value === collaborationDraft.handling_status)?.label || '待分诊'}</b></span>}
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

          <section className="card mt-3" aria-label="Finding 企业协作记录">
            <div className="settings-card-head">
              <div>
                <span className="panel-kicker">企业协作</span>
                <h3>处理记录与自动验证状态分离</h3>
                <p className="muted">人工协作可以记录负责人、修复版本和处置意见，但不能把真实执行失败手工改成“已修复”。</p>
              </div>
              <span className={`summary-pill ${verificationStatus === 'resolved' ? 'strong' : ''}`}>
                自动验证状态：{verificationStatusLabel(verificationStatus)}
              </span>
            </div>

            {!persistenceId ? (
              <div className="settings-card-note mt-3">
                当前 Finding 未能唯一绑定 SQLite 持久化记录，协作字段保持只读。系统不会通过标题猜测并写入相似 Bug；需要下一轮身份映射成功后再编辑。
              </div>
            ) : (
              <>
                <div className="settings-grid mt-3">
                  <label className="form-field">
                    <span>人工处理状态</span>
                    <select
                      value={collaborationDraft.handling_status}
                      onChange={(event) => updateDraft('handling_status', event.target.value as FindingHandlingStatus)}
                    >
                      {HANDLING_STATUS_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                    </select>
                  </label>
                  <label className="form-field">
                    <span>负责人</span>
                    <input
                      value={collaborationDraft.assignee}
                      onChange={(event) => updateDraft('assignee', event.target.value)}
                      placeholder="例如：张三 / 支付研发组"
                    />
                  </label>
                  <label className="form-field">
                    <span>修复版本</span>
                    <input
                      value={collaborationDraft.fix_version}
                      onChange={(event) => updateDraft('fix_version', event.target.value)}
                      placeholder="例如：v1.8.2 / 2026.08 Release"
                    />
                  </label>
                  <label className="form-field">
                    <span>人工处置</span>
                    <select
                      value={collaborationDraft.disposition}
                      onChange={(event) => updateDraft('disposition', event.target.value as FindingDisposition)}
                    >
                      {DISPOSITION_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                    </select>
                  </label>
                  <label className="form-field">
                    <span>外部任务链接（可选）</span>
                    <input
                      value={collaborationDraft.external_issue_url}
                      onChange={(event) => updateDraft('external_issue_url', event.target.value)}
                      placeholder="https://jira.example/... 或 GitHub/GitLab Issue"
                    />
                  </label>
                </div>

                <label className="form-field mt-3">
                  <span>研发反馈</span>
                  <textarea
                    rows={3}
                    value={collaborationDraft.developer_feedback}
                    onChange={(event) => updateDraft('developer_feedback', event.target.value)}
                    placeholder="记录定位结果、修复说明或需要测试补充的上下文"
                  />
                </label>
                <label className="form-field mt-3">
                  <span>风险接受 / 误报说明</span>
                  <textarea
                    rows={3}
                    value={collaborationDraft.disposition_note}
                    onChange={(event) => updateDraft('disposition_note', event.target.value)}
                    placeholder="仅在人工选择风险接受或误报时记录理由；不会改变自动验证结论"
                  />
                </label>

                <div className="settings-actions mt-3">
                  <button
                    type="button"
                    className="btn btn-primary settings-btn-compact"
                    onClick={() => void saveCollaboration()}
                    disabled={collaborationSaving}
                  >
                    {collaborationSaving ? '保存中…' : '保存协作记录'}
                  </button>
                  {collaborativeFinding.collaboration?.updated_at && (
                    <span className="muted">最近保存 {collaborativeFinding.collaboration.updated_at}</span>
                  )}
                </div>
                {collaborationStatus && <p className="settings-inline-feedback" role="status">{collaborationStatus}</p>}
              </>
            )}

            <p className="settings-hint mt-3">
              “自动验证状态”来自真实执行 / Replay 的 SQLite Finding 状态；“人工处理状态”和协作字段保存在独立项目协作表。两者互不覆盖。
            </p>
          </section>
        </div>
      )}
    </article>
  );
}
