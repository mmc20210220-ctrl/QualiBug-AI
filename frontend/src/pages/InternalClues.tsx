import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useFindingsData } from '../api/data';
import { usePageTitle } from '../lib/page-title';
import { formatBeijingDateTime } from '../lib/time';
import { useProjectNavigation } from '../lib/project-navigation';

type ClueFilter = 'all' | 'suspected' | 'risk_clue' | 'not_reproduced' | 'P0' | 'P1' | 'P2';
type GateExplanation = { code?: string; label?: string; detail?: string; next_action?: string };

const GATE_REASON_LABELS: Record<string, string> = {
  INVALID_FINDING_PAYLOAD: '结果结构异常，无法作为客户缺陷交付',
  NOT_MARKED_FOR_DEFECT_DELIVERY: '后端未标记为客户缺陷交付轨道',
  BUG_STATUS_NOT_REPRODUCED: '缺陷状态不是已复现',
  GATE_NOT_PASSED: '证据门控未通过',
  SYNTHETIC_OR_DEMO_EVIDENCE: '证据来源包含模拟、演示或 mock 信号',
  NOT_EXECUTED: '尚未真实执行',
  NOT_CONFIRMED: '尚未形成确认结论',
  EVIDENCE_CONSISTENCY_REJECTED: '声明与证据不一致或证据缺失',
  EVIDENCE_QUALITY_NOT_VALIDATED: '证据质量未达到可交付标准',
  BUSINESS_EVIDENCE_NOT_VALIDATED: '业务证据状态未验证通过',
  MISSING_REAL_REPLAY_ASSET: '缺少真实复现资产',
  MISSING_CUSTOMER_FACING_HARD_EVIDENCE: '缺少客户可核验的请求、响应、断言或时间戳',
  BLOCKED_AUTH_BLOCKED: '认证或权限配置阻断，不能证明业务缺陷',
  BLOCKED_ROUTE_BLOCKED: '路由或网关阻断，不能证明目标业务缺陷',
  BLOCKED_ENVIRONMENT_BLOCKED: '环境不可达或被拦截，不能作为已复现缺陷',
  BLOCKED_COVERAGE_GAP: '覆盖缺口，需继续补跑场景',
  BLOCKED_NOT_REPRODUCED: '已执行但未复现',
  BLOCKED_VALIDATION_LEAD: '仍是验证线索，未达到交付标准',
};

function getClueStatusLabel(status: ClueFilter) {
  if (status === 'all') return '全部线索';
  if (status === 'suspected') return '疑似问题';
  if (status === 'risk_clue') return '风险线索';
  if (status === 'not_reproduced') return '未复现';
  return status;
}

function explainGateReason(reason: string): string {
  return GATE_REASON_LABELS[reason] || reason.replace(/_/g, ' ').toLowerCase();
}

function uniqueText(values: Array<string | undefined>): string[] {
  return Array.from(new Set(values.map((value) => String(value || '').trim()).filter(Boolean)));
}

function getClueReasonCodes(item: unknown): string[] {
  const record = item && typeof item === 'object' ? item as Record<string, unknown> : {};
  const reasons = Array.isArray(record.customer_delivery_gate_reasons) ? record.customer_delivery_gate_reasons : [];
  const gateFailures = Array.isArray(record.gate_failures) ? record.gate_failures : [];
  const evidenceStatus = record.evidence_status && typeof record.evidence_status === 'object' ? record.evidence_status as Record<string, unknown> : {};
  const missingRequirements = Array.isArray(evidenceStatus.missing_requirements) ? evidenceStatus.missing_requirements : [];
  return uniqueText([
    ...reasons.map(String),
    ...gateFailures.map(String),
    ...missingRequirements.map((value) => `MISSING_${String(value)}`),
    record.execution_block ? `BLOCKED_${String(record.execution_block)}` : undefined,
    record.block_reason ? `BLOCKED_${String(record.block_reason)}` : undefined,
    record.confirmation_status ? `CONFIRMATION_${String(record.confirmation_status)}` : undefined,
    record.value_lane ? `LANE_${String(record.value_lane)}` : undefined,
  ]);
}

function getGateExplanations(item: unknown, reasonCodes: string[]): GateExplanation[] {
  const record = item && typeof item === 'object' ? item as Record<string, unknown> : {};
  const explanations = Array.isArray(record.customer_delivery_gate_explanations) ? record.customer_delivery_gate_explanations : [];
  const valid = explanations.filter((value): value is GateExplanation => Boolean(value && typeof value === 'object'));
  if (valid.length > 0) return valid;
  return reasonCodes.map((reason) => ({ code: reason, label: explainGateReason(reason), detail: '', next_action: '' }));
}

export function InternalClues() {
  usePageTitle('待验证线索');
  const [params] = useSearchParams();
  const project = params.get('project')?.trim() || '';
  const { clues, loading, error, refetch } = useFindingsData(project);
  const { navigateToProjectPath } = useProjectNavigation();
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [filter, setFilter] = useState<ClueFilter>('all');

  const suspectedCount = clues.filter((f) => f.bug_status === 'suspected').length;
  const riskClueCount = clues.filter((f) => f.bug_status === 'risk_clue').length;
  const notReproducedCount = clues.filter((f) => f.bug_status === 'not_reproduced').length;
  const p0Count = clues.filter((f) => f.severity === 'P0').length;
  const missingEvidenceCount = clues.filter((f) => (f.evidence_quality?.missing?.length || 0) > 0 || getClueReasonCodes(f).length > 0).length;

  const filters: Array<{ label: string; value: ClueFilter }> = [
    { label: `全部 (${clues.length})`, value: 'all' },
    { label: `疑似问题 (${suspectedCount})`, value: 'suspected' },
    { label: `风险线索 (${riskClueCount})`, value: 'risk_clue' },
    { label: `未复现 (${notReproducedCount})`, value: 'not_reproduced' },
    { label: `P0 (${p0Count})`, value: 'P0' },
    { label: 'P1', value: 'P1' },
    { label: 'P2', value: 'P2' },
  ];

  const displayData = clues.filter((item) => {
    if (filter === 'all') return true;
    if (filter === 'P0' || filter === 'P1' || filter === 'P2') return item.severity === filter;
    return item.bug_status === filter;
  });

  return (
    <div>
      <div className="page-header">
        <div>
          <span className="panel-kicker">内部运营</span>
          <h1>待验证线索</h1>
          <p>仅供内部采证与复验，不进入客户缺陷交付。重点是补齐真实证据，把线索推进成可交付缺陷。</p>
          <div className="page-summary-strip">
            <span className="summary-pill strong">待验证线索 {clues.length} 条</span>
            <span className="summary-pill">疑似问题 {suspectedCount}</span>
            <span className="summary-pill">未复现 {notReproducedCount}</span>
            <span className="summary-pill">待补证据 {missingEvidenceCount}</span>
          </div>
        </div>
        <div className="findings-repro-actions">
          <button className="btn btn-secondary" onClick={() => navigateToProjectPath('/findings', project)}>查看客户缺陷</button>
          <button className="btn btn-secondary" onClick={() => navigateToProjectPath('/campaigns', project)}>进入运行中心补证</button>
          <button className="btn btn-primary" onClick={() => navigateToProjectPath('/dashboard', project)}>继续扫描</button>
        </div>
      </div>

      <div className="findings-stat-grid mb-4">
        {[
          { label: '疑似问题', value: suspectedCount, note: '已命中规则但证据不足', tone: 'warning' },
          { label: '风险线索', value: riskClueCount, note: '需要继续补采运行时证据', tone: 'neutral' },
          { label: '未复现', value: notReproducedCount, note: '建议安排定向复测', tone: 'danger' },
          { label: 'P0 线索', value: p0Count, note: '优先补证闭环', tone: 'primary' },
        ].map((item) => (
          <article key={item.label} className={`findings-stat-card tone-${item.tone}`}><strong>{item.value}</strong><span>{item.label}</span><small>{item.note}</small></article>
        ))}
      </div>

      <div className="filters behavior-filters mb-4">
        {filters.map((item) => <button key={item.value} onClick={() => setFilter(item.value)} className={`filter${filter === item.value ? ' active' : ''}`}>{item.label}</button>)}
      </div>

      {loading && <section className="findings-empty-state compact"><div className="spinner spinner-centered" /><p>正在整理待验证线索...</p></section>}
      {!loading && error && displayData.length === 0 && <section className="findings-empty-state danger"><span className="findings-empty-kicker">连接异常</span><h3>线索数据暂时不可用</h3><p>{error}</p><button className="btn btn-primary" onClick={refetch}>重新连接</button></section>}
      {!loading && !error && displayData.length === 0 && <section className="findings-empty-state"><span className="findings-empty-kicker">当前空态</span><h3>{filter === 'all' ? '暂无待验证线索' : `暂无 ${getClueStatusLabel(filter)}`}</h3><p>{filter === 'all' ? '当前项目没有待验证线索，说明本轮结果已基本收敛到客户可交付缺陷。' : '当前筛选条件下没有匹配线索。'}</p></section>}

      {displayData.map((item) => {
        const isOpen = expandedId === item.id;
        const missing = item.evidence_quality?.missing || [];
        const gateReasons = getClueReasonCodes(item);
        const gateExplanations = getGateExplanations(item, gateReasons);
        const nextActions = item.evidence_quality?.next_actions || [];
        const reproduction = item.reproduction || { method: '', path: '', steps: [], is_synthetic: false };
        const investigation = item.investigation_guidance || { relevant_apis: [], relevant_tables: [], log_search: '', sql_verify: '' };
        return <div key={item.id} className={`evidence-item findings-item ${item.severity.toLowerCase()}${isOpen ? ' open' : ''}`}>
          <div className="evidence-head" onClick={() => setExpandedId(isOpen ? null : item.id)}><span className={`severity ${item.severity.toLowerCase()}`}>{item.severity}</span><span className={`bug-status-badge bug-status-${item.bug_status || 'risk_clue'}`}>{item.bug_status_label || '待验证'}</span><span className="evidence-title">{item.title}</span><span className="evidence-meta"><span className="evidence-quality-score-chip">{item.evidence_quality?.score ?? 0}/100</span><time>{formatBeijingDateTime(item.timestamp)}</time></span><span className="evidence-expand">{isOpen ? '▲' : '▼'}</span></div>
          <div className="evidence-one-liner" onClick={() => setExpandedId(isOpen ? null : item.id)}><span className="evidence-one-liner-label">内部判断</span><span className="evidence-one-liner-text">{item.business_summary || item.actual || item.evidence_quality?.summary || '需要补充更多证据后再确认。'}</span></div>
          <div className="evidence-body">
            <div className="findings-compare-grid"><div className="findings-compare-card"><span className="findings-compare-label">当前掌握</span><p>{item.actual || item.evidence_quality?.summary || '暂未形成稳定异常结论'}</p></div><div className="findings-compare-card danger"><span className="findings-compare-label">仍缺证据</span><p>{missing.length > 0 ? missing.join('；') : '暂无明确缺口，建议继续复验确认'}</p></div></div>
            {gateExplanations.length > 0 && <div className="findings-investigation-card"><div className="findings-panel-kicker warning">未进入客户缺陷的原因</div><div className="findings-investigation-body"><ul className="findings-steps">{gateExplanations.map((reason, index) => <li key={`${reason.code || reason.label}-${index}`}><strong>{reason.label || explainGateReason(reason.code || '')}</strong>{reason.detail ? <span>：{reason.detail}</span> : null}{reason.next_action ? <small>下一步：{reason.next_action}</small> : null}</li>)}</ul></div></div>}
            {nextActions.length > 0 && <div className="findings-investigation-card"><div className="findings-panel-kicker warning">下一步建议</div><div className="findings-investigation-body"><ol className="findings-steps">{nextActions.map((step, index) => <li key={`${step}-${index}`}>{step}</li>)}</ol></div></div>}
            {(reproduction.path || investigation.log_search || investigation.sql_verify || investigation.relevant_tables?.length || investigation.relevant_apis?.length) && <div className="findings-investigation-card"><div className="findings-panel-kicker">补证入口</div><div className="findings-investigation-body">{reproduction.path && <div>优先复验：<code>{reproduction.method} {reproduction.path}</code></div>}{investigation.relevant_apis?.length > 0 && <div>关联接口：{investigation.relevant_apis.join('、')}</div>}{investigation.relevant_tables?.length > 0 && <div>关联数据表：{investigation.relevant_tables.join('、')}</div>}{investigation.log_search && <div><code>{investigation.log_search}</code></div>}{investigation.sql_verify && <div><code>{investigation.sql_verify}</code></div>}</div></div>}
            <div className="findings-investigation-card"><div className="findings-panel-kicker">补证动作</div><div className="findings-repro-actions"><button type="button" className="btn btn-primary btn-sm" onClick={() => navigateToProjectPath('/campaigns', project)}>进入运行中心补证</button><button type="button" className="btn btn-secondary btn-sm" onClick={() => navigateToProjectPath('/evidence', project)}>查看证据链</button><button type="button" className="btn btn-secondary btn-sm" onClick={() => navigateToProjectPath('/materials', project)}>补充来源资料</button></div></div>
          </div>
        </div>;
      })}
    </div>
  );
}

export default InternalClues;
