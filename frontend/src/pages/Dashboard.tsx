import { useCallback } from 'react';
import type { ReactNode } from 'react';
import { useSearchParams } from 'react-router-dom';
import { getCommercialAssets, usePipelineData } from '../api/data';
import { useToast } from '../components/useToast';
import { buildReportData, renderReportHTML } from '../api/report';
import { formatDurationMs } from '../lib/display';
import { usePageTitle } from '../lib/page-title';
import { useProjectNavigation } from '../lib/project-navigation';
import type { CommercialAssets, Finding, RegressionSummary } from '../types';

type JsonRecord = Record<string, unknown>;

const MAIN_CHAIN_STAGE_LABELS: Record<string, string> = {
  enterprise_inputs: '企业资料',
  knowledge_parse: '解析知识',
  test_plan: '测试计划',
  execution: '真实执行',
  bug_discovery: 'Bug 发现',
  evidence_chain: '证据链',
};

const MAIN_CHAIN_STATUS_LABELS: Record<string, string> = {
  passed: '通过',
  partial: '部分',
  missing: '缺失',
};

const EVIDENCE_MISSING_FIELD_LABELS: Record<string, string> = {
  issue_id: '稳定 issue_id',
  request: '原始 request',
  response: '原始 response',
  expected: 'expected',
  actual: 'actual',
  reproduction_or_replay: 'reproduction / replay',
  execution_receipt: 'execution_receipt',
  non_synthetic_evidence: '非 synthetic 证据',
  missing_raw_request: '原始 request',
  missing_raw_response: '原始 response',
  missing_expected_actual_pair: 'expected + actual',
  missing_replay_or_reproduction: 'reproduction / replay',
  missing_execution_receipt: 'execution_receipt',
  synthetic_evidence_present: 'synthetic 证据',
  strict_evidence_not_linked_to_all_issues: '严格证据未覆盖全部 issue',
};

function Skeleton({ h = 20, w = '100%', br = 4, className = '' }: { h?: number; w?: string | number; br?: number; className?: string }) {
  return <div className={`skeleton-block${className ? ` ${className}` : ''}`} style={{ height: h, width: w, borderRadius: br }} />;
}

function StatePanel({ eyebrow, title, description, action }: { eyebrow: string; title: string; description: string; action?: ReactNode }) {
  return (
    <section className="state-panel">
      <div className="state-panel-badge">{eyebrow}</div>
      <h2>{title}</h2>
      <p>{description}</p>
      {action ? <div className="state-panel-actions">{action}</div> : null}
    </section>
  );
}

function asRecord(value: unknown): JsonRecord {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as JsonRecord : {};
}

function asText(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function asNum(v: unknown, fallback = 0): number {
  const parsed = typeof v === 'number' ? v : Number(v);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function firstNum(...values: unknown[]): number {
  for (const value of values) {
    const parsed = typeof value === 'number' ? value : Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return 0;
}

function formatScanTime(value: string) {
  if (!value) return '暂无';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', { hour12: false });
}

function getSeverityWeight(severity: Finding['severity']) {
  if (severity === 'P0') return 3;
  if (severity === 'P1') return 2;
  return 1;
}

function getFindingModule(finding: Finding) {
  return String(finding.business_impact?.module || finding.source_entity || finding.defect_family_label || '核心业务').trim() || '核心业务';
}

function riskRatingLabel(score: number) {
  if (score >= 80) return '稳健';
  if (score >= 60) return '关注';
  return '优先治理';
}

function campaignStatusLabel(status: string): string {
  if (status === 'blocked') return 'Campaign 已阻塞';
  if (status === 'coverage_deferred') return '覆盖已递延';
  if (status === 'completed') return 'Campaign 已完成';
  if (status === 'active') return 'Campaign 进行中';
  return 'Campaign 状态待同步';
}

function campaignDetail(status: string, deferredReason: string, nextCampaignReason: string): string {
  if (status === 'blocked') return `当前扫描未进入执行阶段：${deferredReason || '缺少经过批准的来源、范围、环境或测试数据条件'}。${nextCampaignReason ? ` 下一步：${nextCampaignReason}。` : ''}`;
  if (status === 'coverage_deferred') return `自动范围已到边界，剩余覆盖被明确递延，而非判定为已覆盖。${deferredReason ? ` 原因：${deferredReason}。` : ''}${nextCampaignReason ? ` 下一步：${nextCampaignReason}。` : ''}`;
  if (status === 'completed') return '本 Campaign 的可执行范围已按真实回执收口；后续范围变化会创建新的 Campaign。';
  if (status === 'active') return '当前 Campaign 仍在执行或补证；只有完整真实回执可以形成 confirmed 结果。';
  return '当前没有可用的 Campaign 治理状态。';
}

function commercialHandoffLabel(assets: CommercialAssets | null): string {
  const status = assets?.commercial_handoff.status || assets?.status || '';
  if (status === 'commercial_handoff_ready_with_validated_findings') return '商业交付已就绪';
  if (status === 'ready_for_customer_acceptance') return '待客户验收';
  if (status === 'materialized') return '交付资产已生成';
  if (status === 'empty') return '尚未生成';
  return status || '未上报';
}

function trackerSyncLabel(assets: CommercialAssets | null): string {
  const status = assets?.tracker_sync.payload_status || '';
  if (status === 'external_tracker_sync_payloads_blocked_or_empty') return '仅保留待同步草稿';
  if (status === 'external_tracker_sync_payloads_ready') return '同步载荷已就绪';
  return status || '未上报';
}

function deliveryPackageLabel(assets: CommercialAssets | null): string {
  const status = assets?.delivery_package.status || '';
  if (status === 'created') return '交付包已创建';
  if (status === 'not_created') return '交付包未生成';
  return status || '未上报';
}

function regressionGateLabel(status: string): string {
  const normalized = status.trim().toLowerCase();
  if (normalized === 'failed') return '回归失败';
  if (normalized === 'passed') return '回归通过';
  if (normalized === 'manual_approval_required') return '回归待审批';
  return normalized ? '回归待执行' : '回归未上报';
}

function getExecutiveHeadline(defectCount: number, p0Count: number, highPriorityCount: number, clueCount: number, campaignStatus: string, deferredReason: string) {
  if (campaignStatus === 'blocked') return `本轮 Campaign 已阻塞：${deferredReason || '缺少进入执行的必要合同'}。`;
  if (campaignStatus === 'coverage_deferred') return `本轮覆盖已递延：${deferredReason || '自动执行边界已到达'}。`;
  if (defectCount > 0 && p0Count > 0) return `已确认 ${defectCount} 个可交付缺陷，其中 ${p0Count} 个会直接影响发布。`;
  if (defectCount > 0) return `已确认 ${defectCount} 个可交付缺陷，可直接进入整改与验收闭环。`;
  if (clueCount > 0) return `本轮尚未形成可交付缺陷，内部仍有 ${clueCount} 条线索正在补证。`;
  if (highPriorityCount === 0) return '当前未发现可交付缺陷，但仍需结合 Campaign 覆盖状态判断发布结论。';
  return '当前没有形成客户可交付缺陷，建议继续进行真实场景扫描。';
}

function getExecutiveDescription(defectCount: number, clueCount: number, evidenceScore: number, modulesCount: number, campaignStatus: string, deferredReason: string, nextCampaignReason: string) {
  if (campaignStatus === 'blocked' || campaignStatus === 'coverage_deferred') return campaignDetail(campaignStatus, deferredReason, nextCampaignReason);
  if (defectCount > 0) return `本页只展示已验证、可复现、具备原始证据的缺陷结果。当前已覆盖 ${modulesCount} 个业务模块，证据可信度 ${evidenceScore}%。`;
  if (clueCount > 0) return '当前没有站得住的客户缺陷，说明系统仍处于补证阶段。本轮内部线索不会进入客户交付，待形成真实证据后再升级展示。';
  return '当前结果代表本轮没有形成客户可交付缺陷。后续新增扫描结果后，这里会自动更新业务结论。';
}

function getGatePatchStatus(record: JsonRecord): JsonRecord {
  const direct = asRecord(record.customer_delivery_gate_patch);
  if (Object.keys(direct).length > 0) return direct;
  const contract = asRecord(record.data_contract);
  return asRecord(contract.customer_delivery_gate_patch);
}

function gatePatchLabel(patched: boolean): string {
  return patched ? '严格 Gate 已启用' : '严格 Gate 未确认';
}

function getMainChainContract(record: JsonRecord): JsonRecord {
  const direct = asRecord(record.main_chain_contract);
  if (Object.keys(direct).length > 0) return direct;
  const contract = asRecord(record.data_contract);
  return asRecord(contract.main_chain_contract);
}

function getMainChainSummary(record: JsonRecord, contract: JsonRecord): JsonRecord {
  const direct = asRecord(record.main_chain_contract_summary);
  if (Object.keys(direct).length > 0) return direct;
  const summary = asRecord(contract.summary);
  if (Object.keys(summary).length > 0) {
    return {
      chain_ready: contract.chain_ready,
      customer_defect_delivery_ready: contract.customer_defect_delivery_ready,
      first_blocked_stage: summary.first_blocked_stage,
      first_blocked_next_action: summary.first_blocked_next_action,
      passed_stage_count: summary.passed_stage_count,
      partial_stage_count: summary.partial_stage_count,
      missing_stage_count: summary.missing_stage_count,
    };
  }
  return {};
}

function getMainChainStages(contract: JsonRecord): JsonRecord[] {
  if (!Array.isArray(contract.stages)) return [];
  return contract.stages.map(asRecord).filter((stage) => Object.keys(stage).length > 0);
}

function getEvidenceNormalizationSummary(record: JsonRecord): JsonRecord {
  const direct = asRecord(record.evidence_bundle_normalization_summary);
  if (Object.keys(direct).length > 0) return direct;
  const contract = asRecord(record.data_contract);
  return asRecord(contract.evidence_bundle_normalization_summary);
}

function getEvidenceNormalizationReport(record: JsonRecord): JsonRecord {
  const direct = asRecord(record.evidence_bundle_normalization_report);
  if (Object.keys(direct).length > 0) return direct;
  const contract = asRecord(record.data_contract);
  return asRecord(contract.evidence_bundle_normalization_report);
}

function evidenceNormalizationItems(report: JsonRecord): JsonRecord[] {
  if (!Array.isArray(report.items)) return [];
  return report.items.map(asRecord).filter((item) => Object.keys(item).length > 0);
}

function evidenceMissingEntries(summary: JsonRecord): Array<[string, number]> {
  const missing = asRecord(summary.missing_fields);
  return Object.entries(missing)
    .map(([field, count]) => [field, asNum(count)] as [string, number])
    .filter(([, count]) => count > 0)
    .sort((a, b) => b[1] - a[1]);
}

function evidenceMissingFieldLabel(field: string): string {
  return EVIDENCE_MISSING_FIELD_LABELS[field] || field;
}

function evidenceItemTitle(item: JsonRecord): string {
  const evidenceId = asText(item.evidence_id);
  const issueId = asText(item.issue_id);
  const probeId = asText(item.probe_id);
  return [evidenceId, issueId, probeId].filter(Boolean).join(' / ') || '未命名证据项';
}

function evidenceItemAction(item: JsonRecord): string {
  return asText(item.next_action) || '补齐该证据项缺失字段后重新运行主链路合同。';
}

function evidenceNormalizationLabel(summary: JsonRecord): string {
  if (Object.keys(summary).length === 0) return '证据标准化未上报';
  const blocked = asNum(summary.blocked_item_count);
  return blocked > 0 ? `证据缺字段 ${blocked} 项` : '证据字段已标准化';
}

function mainChainStageLabel(stage: JsonRecord): string {
  const key = asText(stage.stage);
  return MAIN_CHAIN_STAGE_LABELS[key] || key || '未知阶段';
}

function mainChainStatusLabel(stage: JsonRecord): string {
  const status = asText(stage.status) || (stage.ok === true ? 'passed' : 'missing');
  return MAIN_CHAIN_STATUS_LABELS[status] || status || '未知';
}

function mainChainReadyLabel(ready: boolean, hasContract: boolean): string {
  if (!hasContract) return '主链路未上报';
  return ready ? '主链路已闭合' : '主链路未闭合';
}

export function Dashboard() {
  usePageTitle('风险总览');
  const [params] = useSearchParams();
  const project = params.get('project')?.trim() || '';
  const { data, loading, error, refetch } = usePipelineData(project);
  const { navigateToProjectPath } = useProjectNavigation();
  const toast = useToast();

  const handleExport = useCallback(async () => {
    if (!data) return;
    try {
      const record = asRecord(data);
      toast.show('正在生成评级报告...', 'info');
      const findings = ((record.defects || record.risks || []) as Finding[]);
      const valueMetrics = asRecord(record.value_metrics);
      const scores = asRecord(valueMetrics.scores);
      const reportData = buildReportData({
        projectName: asText(record.project_name) || project,
        industry: asText(record.industry),
        totalBugs: findings.length,
        beiScore: asNum(scores.bei),
        bdsScore: String(scores.bds || '0.0'),
        bcsScore: asNum(scores.bcs),
        runtimeProbes: asNum(asRecord(record.business_flow_summary).total),
        dbConfirmed: asNum(asRecord(record.db_verification).confirmed),
        findings,
        dbFindings: [],
      });
      const html = renderReportHTML(reportData);
      const blob = new Blob([html], { type: 'text/html;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      window.open(url, '_blank');
      toast.show('评级报告已在新标签页打开', 'success');
    } catch (caught: unknown) {
      const message = caught instanceof Error ? caught.message : '导出失败';
      toast.show(`导出失败: ${message}`, 'danger');
    }
  }, [project, data, toast]);

  if (loading) {
    return (
      <div>
        <div className="page-header dashboard-loading-header">
          <div className="dashboard-loading-header-main">
            <Skeleton h={28} w="60%" br={6} />
            <div className="dashboard-loading-gap-sm"><Skeleton h={16} w="80%" /></div>
          </div>
          <Skeleton h={36} w={140} br={7} />
        </div>
        <div className="score-row">
          <div className="bei-card dashboard-loading-card"><div className="dashboard-loading-ring" /><Skeleton h={16} w={120} /><div className="dashboard-loading-gap-sm"><Skeleton h={12} w={180} /></div></div>
          <div className="bei-details">
            {[1, 2].map((item) => (
              <div key={item} className="mini-card dashboard-loading-mini-card"><Skeleton h={44} w={44} br={10} /><div className="dashboard-loading-flex"><Skeleton h={16} w="60%" /><div className="dashboard-loading-gap-xs"><Skeleton h={12} w="80%" /></div></div></div>
            ))}
          </div>
        </div>
      </div>
    );
  }

  if (error && !data) return <StatePanel eyebrow="连接状态" title="后端暂时不可用" description={error} action={<button className="btn btn-primary" onClick={refetch}>重新连接</button>} />;
  if (!project) return <StatePanel eyebrow="客户选择" title="请先选择客户项目" description="风险总览只展示真实项目数据。选择客户后，界面会按该项目的检测结果与证据链自动刷新。" />;

  const record = asRecord(data);
  const commercialAssets = getCommercialAssets(record);
  const findings = ((record.defects || record.risks || []) as Finding[]);
  const clues = ((record.clues || []) as Finding[]);
  const regressionSummary = asRecord(record.regression_summary) as unknown as RegressionSummary;
  const valueMetrics = asRecord(record.value_metrics);
  const scanMeta = asRecord(record.scan_meta);
  const gatePatch = getGatePatchStatus(record);
  const gatePatchEnabled = Boolean(gatePatch.patched);
  const gatePatchSource = asText(gatePatch.source) || '未上报';
  const activePartitionName = asText(gatePatch.active_partition_name) || '未上报';
  const mainChainContract = getMainChainContract(record);
  const mainChainSummary = getMainChainSummary(record, mainChainContract);
  const mainChainStages = getMainChainStages(mainChainContract);
  const hasMainChainContract = Object.keys(mainChainContract).length > 0 || Object.keys(mainChainSummary).length > 0;
  const mainChainReady = Boolean(mainChainSummary.chain_ready);
  const firstBlockedStage = asText(mainChainSummary.first_blocked_stage);
  const firstBlockedStageLabel = firstBlockedStage ? (MAIN_CHAIN_STAGE_LABELS[firstBlockedStage] || firstBlockedStage) : '暂无';
  const firstBlockedNextAction = asText(mainChainSummary.first_blocked_next_action) || '等待后端上报主链路下一步。';
  const evidenceNormalizationSummary = getEvidenceNormalizationSummary(record);
  const evidenceNormalizationReport = getEvidenceNormalizationReport(record);
  const evidenceNormalizationItemReports = evidenceNormalizationItems(evidenceNormalizationReport);
  const blockedEvidenceActionItems = evidenceNormalizationItemReports.filter((item) => item.normalized !== true);
  const evidenceMissingFields = evidenceMissingEntries(evidenceNormalizationSummary);
  const evidenceBlockedItemCount = asNum(evidenceNormalizationSummary.blocked_item_count, blockedEvidenceActionItems.length);
  const evidenceFullyNormalizedCount = asNum(evidenceNormalizationSummary.fully_normalized_count);
  const hasEvidenceNormalizationSummary = Object.keys(evidenceNormalizationSummary).length > 0 || evidenceNormalizationItemReports.length > 0;
  const campaign = asRecord(record.campaign);
  const continuousCampaign = asRecord(record.continuous_discovery_campaign);
  const campaignSummary = asRecord(continuousCampaign.summary);
  const campaignStatus = asText(campaign.campaign_status).toLowerCase();
  const campaignDeferredReason = asText(campaign.coverage_deferred_reason);
  const nextCampaignReason = asText(campaign.next_campaign_reason);
  const campaignScope = asText(campaign.scope_id);
  const campaignEnvironment = asText(campaign.environment_ref);
  const totalRiskCount = findings.length;
  const campaignAttempted = asNum(campaign.attempted_slice_count);
  const campaignConfirmed = firstNum(campaignSummary.current_campaign_confirmed_slice_count, campaignSummary.confirmed_slice_count, campaign.confirmed_slice_count);
  const campaignCurrentDefects = asNum(campaignSummary.current_campaign_customer_ready_defect_count);
  const campaignCurrentRawFindings = asNum(campaignSummary.current_campaign_bundle_finding_count_raw);
  const campaignFamilyDefects = asNum(campaignSummary.family_customer_ready_defect_count, totalRiskCount);
  const campaignCarryoverDefects = asNum(campaignSummary.family_historical_carryover_defect_count);
  const coverageGaps = Array.isArray(record.coverage_gaps) ? record.coverage_gaps.length : 0;
  const governanceNeedsAction = campaignStatus === 'blocked' || campaignStatus === 'coverage_deferred';
  const p0Count = findings.filter((finding) => finding.severity === 'P0').length;
  const clueCount = clues.length;
  const evidenceTrust = asNum(valueMetrics.evidence_trust_score, 0);
  const highPriorityCount = p0Count + findings.filter((finding) => finding.severity === 'P1').length;
  const modules = Array.from(new Set(findings.map(getFindingModule).filter(Boolean)));
  const modulesCount = modules.length;
  const validatedDefects = findings.filter((finding) => finding.evidence_quality?.level === 'validated').length;
  const deliveryReadiness = findings.length > 0 ? Math.round((validatedDefects / findings.length) * 100) : 0;
  const regressionCovered = asNum(regressionSummary.covered_defect_count);
  const regressionFailed = asNum(regressionSummary.failed_defect_count);
  const regressionPending = asNum(regressionSummary.pending_defect_count);
  const regressionPassed = asNum(regressionSummary.passed_defect_count);
  const regressionRunAt = asText(regressionSummary.latest_run?.generated_at);
  const regressionGate = regressionGateLabel(asText(regressionSummary.latest_run?.gate_status));
  const hasMaterializedMetrics = totalRiskCount > 0 || clueCount > 0 || asNum(asRecord(record.business_flow_summary).total, 0) > 0 || Boolean(campaignStatus) || coverageGaps > 0 || hasMainChainContract || hasEvidenceNormalizationSummary;
  const topFindings = [...findings].sort((a, b) => {
    const severityGap = getSeverityWeight(b.severity) - getSeverityWeight(a.severity);
    return severityGap !== 0 ? severityGap : (b.evidence_quality?.score || 0) - (a.evidence_quality?.score || 0);
  }).slice(0, 3);
  const topFamilyLabel = findings[0]?.defect_family_label || findings[0]?.defect_family || '核心业务';
  const executiveHeadline = getExecutiveHeadline(totalRiskCount, p0Count, highPriorityCount, clueCount, campaignStatus, campaignDeferredReason);
  const executiveDescription = getExecutiveDescription(totalRiskCount, clueCount, evidenceTrust, modulesCount, campaignStatus, campaignDeferredReason, nextCampaignReason);
  const conclusion = campaignStatus === 'blocked' ? 'Campaign 已阻塞' : campaignStatus === 'coverage_deferred' ? '覆盖已递延' : p0Count > 0 ? '存在阻断发布缺陷' : totalRiskCount > 0 ? '建议进入整改验收' : '当前无可交付缺陷';
  const conclusionDetail = campaignStatus === 'blocked' || campaignStatus === 'coverage_deferred'
    ? campaignDetail(campaignStatus, campaignDeferredReason, nextCampaignReason)
    : p0Count > 0 ? `${p0Count} 个 P0 缺陷需要优先闭环。` : totalRiskCount > 0 ? `${highPriorityCount} 个高风险问题建议先处理。` : '本轮结果需结合 Campaign 覆盖状态作为当前阶段风险结论。';

  const mainChainDetail = hasMainChainContract
    ? mainChainReady
      ? '企业资料、解析、计划、执行、缺陷发现和证据链已闭合。'
      : `第一断点：${firstBlockedStageLabel}。下一步：${firstBlockedNextAction}`
    : '当前后端尚未返回 main_chain_contract，请先通过链路感知发现入口生成主链路合同。';
  const evidenceDetail = hasEvidenceNormalizationSummary
    ? evidenceMissingFields.length > 0
      ? `证据标准化仍缺字段：${evidenceMissingFields.map(([field, count]) => `${evidenceMissingFieldLabel(field)}×${count}`).join('、')}。`
      : `证据字段已标准化：${evidenceFullyNormalizedCount} 项可用于严格证据链。`
    : '当前后端尚未返回 evidence_bundle_normalization_summary。';

  const secondarySummarySection = (
    <section className="customer-secondary-grid">
      <article className="customer-secondary-card">
        <span className="customer-value-kicker">本轮交付说明</span>
        <div className="customer-secondary-meta">
          <span><em>最近扫描</em><b>{formatScanTime(asText(scanMeta.last_scan_at) || asText(record.updated_at))}</b></span>
          <span><em>本轮耗时</em><b>{formatDurationMs(asNum(scanMeta.total_ms))}</b></span>
          <span><em>结果评级</em><b>{asText(scanMeta.grade) || riskRatingLabel(evidenceTrust)}</b></span>
          <span><em>交付 Gate</em><b>{gatePatchLabel(gatePatchEnabled)}</b></span>
          <span><em>主链路</em><b>{mainChainReadyLabel(mainChainReady, hasMainChainContract)}</b></span>
          <span><em>证据标准化</em><b>{evidenceNormalizationLabel(evidenceNormalizationSummary)}</b></span>
        </div>
      </article>
      <article className={`customer-secondary-card${hasMainChainContract && mainChainReady && evidenceBlockedItemCount === 0 ? '' : ' muted'}`}>
        <span className="customer-value-kicker">主链路闭合状态</span>
        <h3>{mainChainReadyLabel(mainChainReady, hasMainChainContract)}</h3>
        <p>{mainChainDetail}</p>
        <div className="customer-secondary-meta">
          {mainChainStages.length > 0 ? mainChainStages.map((stage) => (
            <span key={`${asText(stage.stage)}-${asText(stage.status)}`}><em>{mainChainStageLabel(stage)}</em><b>{mainChainStatusLabel(stage)}</b></span>
          )) : (
            <>
              <span><em>通过</em><b>{asNum(mainChainSummary.passed_stage_count)}</b></span>
              <span><em>部分</em><b>{asNum(mainChainSummary.partial_stage_count)}</b></span>
              <span><em>缺失</em><b>{asNum(mainChainSummary.missing_stage_count)}</b></span>
              <span><em>第一断点</em><b>{firstBlockedStageLabel}</b></span>
            </>
          )}
        </div>
      </article>
      <article className={`customer-secondary-card${evidenceBlockedItemCount > 0 ? ' muted' : ''}`}>
        <span className="customer-value-kicker">证据标准化阻断项</span>
        <h3>{evidenceNormalizationLabel(evidenceNormalizationSummary)}</h3>
        <p>{evidenceDetail}</p>
        <div className="customer-secondary-meta">
          <span><em>已标准化</em><b>{evidenceFullyNormalizedCount}</b></span>
          <span><em>仍阻断</em><b>{evidenceBlockedItemCount}</b></span>
          {evidenceMissingFields.slice(0, 6).map(([field, count]) => (
            <span key={field}><em>{evidenceMissingFieldLabel(field)}</em><b>{count}</b></span>
          ))}
        </div>
        {blockedEvidenceActionItems.length > 0 && (
          <div className="customer-secondary-meta">
            {blockedEvidenceActionItems.slice(0, 3).map((item) => (
              <span key={`${evidenceItemTitle(item)}-${asText(item.trace_id)}`}><em>{evidenceItemTitle(item)}</em><b>{evidenceItemAction(item)}</b></span>
            ))}
          </div>
        )}
      </article>
      <article className={`customer-secondary-card${gatePatchEnabled ? '' : ' muted'}`}>
        <span className="customer-value-kicker">交付 Gate 诊断</span>
        <h3>{gatePatchLabel(gatePatchEnabled)}</h3>
        <p>{gatePatchEnabled ? '客户缺陷列表已通过严格后端 Gate 分流，未达标结果会进入内部线索池。' : '当前返回体未确认严格 Gate 状态，建议检查后端是否通过 qualibug-server 启动。'}</p>
        <div className="customer-secondary-meta">
          <span><em>来源</em><b>{gatePatchSource}</b></span>
          <span><em>分流函数</em><b>{activePartitionName}</b></span>
          <span><em>原函数保留</em><b>{gatePatch.has_original_partition ? '是' : '否'}</b></span>
        </div>
      </article>
      {campaignStatus && (
        <article className={`customer-secondary-card${governanceNeedsAction ? ' muted' : ''}`}>
          <span className="customer-value-kicker">Campaign 治理</span>
          <h3>{campaignStatusLabel(campaignStatus)}</h3>
          <p>{campaignDetail(campaignStatus, campaignDeferredReason, nextCampaignReason)}</p>
          <div className="customer-secondary-meta">
            <span><em>范围</em><b>{campaignScope || '待登记'}</b></span>
            <span><em>环境</em><b>{campaignEnvironment || '待登记'}</b></span>
            <span><em>确认回执</em><b>{campaignConfirmed}/{campaignAttempted || 0}</b></span>
            <span><em>本轮缺陷</em><b>{campaignCurrentDefects || 0} 条</b></span>
            <span><em>缺陷货架</em><b>{campaignFamilyDefects} 条</b></span>
            <span><em>历史延续</em><b>{campaignCarryoverDefects} 条</b></span>
            <span><em>覆盖缺口</em><b>{coverageGaps}</b></span>
          </div>
          {(campaignCurrentRawFindings > 0 || campaignCarryoverDefects > 0) && (
            <div className="customer-secondary-meta">
              <span><em>本轮原始 finding</em><b>{campaignCurrentRawFindings}</b></span>
              <span><em>口径说明</em><b>回执 {campaignConfirmed} → 本轮缺陷 {campaignCurrentDefects || 0} → 货架 {campaignFamilyDefects}</b></span>
            </div>
          )}
        </article>
      )}
      {clueCount > 0 && (
        <article className="customer-secondary-card muted">
          <span className="customer-value-kicker">内部待跟进</span>
          <h3>{clueCount} 条线索仍在补证</h3>
          <p>这部分只供内部运营使用，不进入客户缺陷交付，避免把待补证线索误展示成已确认问题。</p>
          <button className="btn btn-secondary" onClick={() => navigateToProjectPath('/clues', project)}>进入内部线索页</button>
        </article>
      )}
      {commercialAssets && (
        <article className={`customer-secondary-card${commercialAssets.status === 'materialized' ? '' : ' muted'}`}>
          <span className="customer-value-kicker">商业交付资产</span>
          <h3>{commercialHandoffLabel(commercialAssets)}</h3>
          <p>{commercialAssets.status === 'materialized' ? `当前已沉淀 ${commercialAssets.finding_count} 条商业交付资产，其中 ${commercialAssets.customer_ready_reproduction_count} 条具备客户复验入口。` : '后端已经暴露 commercial_assets 契约，但当前尚未形成可交付资产包。'}</p>
          <div className="customer-secondary-meta">
            <span><em>交付包</em><b>{deliveryPackageLabel(commercialAssets)}</b></span>
            <span><em>Tracker 同步</em><b>{trackerSyncLabel(commercialAssets)}</b></span>
            <span><em>客户复验资产</em><b>{commercialAssets.customer_ready_reproduction_count}</b></span>
            <span><em>安全交付</em><b>{commercialAssets.commercial_handoff.safe_for_customer ? '是' : '否'}</b></span>
          </div>
        </article>
      )}
      {(regressionCovered > 0 || regressionSummary.suite_exists) && (
        <article className={`customer-secondary-card${regressionFailed > 0 || regressionPending > 0 ? ' muted' : ''}`}>
          <span className="customer-value-kicker">回归验证</span>
          <h3>{regressionGate}</h3>
          <p>{regressionSummary.headline || '当前已经把客户缺陷纳入回归闭环，但尚未返回更多细节。'}</p>
          <div className="customer-secondary-meta">
            <span><em>已覆盖缺陷</em><b>{regressionCovered}</b></span>
            <span><em>回归通过</em><b>{regressionPassed}</b></span>
            <span><em>回归失败</em><b>{regressionFailed}</b></span>
            <span><em>待执行</em><b>{regressionPending}</b></span>
            <span><em>最近模式</em><b>{asText(regressionSummary.latest_run?.suite_mode_label) || asText(regressionSummary.latest_run?.suite_mode) || '未执行'}</b></span>
            <span><em>最近回归</em><b>{regressionRunAt ? formatScanTime(regressionRunAt) : '暂无'}</b></span>
          </div>
        </article>
      )}
    </section>
  );

  if (!hasMaterializedMetrics) {
    return (
      <div>
        <div className="page-header"><div><h1>{asText(record.project_name) || project} · 行为风险总览</h1><p>当前项目还没有形成真实风险数据或验证结果。</p></div></div>
        <StatePanel eyebrow="结果状态" title="当前还没有形成可展示的真实指标" description="本项目暂未产生行为发现、执行探针或数据验证结果。运行检测后，页面会自动切换为真实业务视图。" />
      </div>
    );
  }

  return (
    <div>
      <section className="customer-showcase mb-4">
        <div className="customer-showcase-main">
          <span className="panel-kicker">客户成果</span>
          <h1>{asText(record.project_name) || project} · {executiveHeadline}</h1>
          <p>{executiveDescription}</p>
          <div className="page-summary-strip">
            <span className="summary-pill strong">可交付缺陷 {totalRiskCount}</span>
            <span className="summary-pill">阻断发布 {p0Count}</span>
            <span className="summary-pill">Campaign {campaignStatusLabel(campaignStatus)}</span>
            <span className="summary-pill">覆盖缺口 {coverageGaps}</span>
            {regressionCovered > 0 && <span className="summary-pill">回归 {regressionGate}</span>}
            {commercialAssets && <span className="summary-pill">商业交付 {commercialHandoffLabel(commercialAssets)}</span>}
            <span className="summary-pill">{gatePatchLabel(gatePatchEnabled)}</span>
            <span className="summary-pill">{mainChainReadyLabel(mainChainReady, hasMainChainContract)}</span>
            <span className="summary-pill">{evidenceNormalizationLabel(evidenceNormalizationSummary)}</span>
          </div>
          <div className="customer-showcase-actions">
            <button className="btn btn-primary" onClick={() => navigateToProjectPath('/findings', project)}>查看客户缺陷</button>
            <button className="btn btn-secondary" onClick={() => navigateToProjectPath('/evidence', project)}>查看证据链</button>
            <button className="btn btn-secondary" onClick={handleExport}>导出成果摘要</button>
          </div>
        </div>
        <div className="customer-showcase-side">
          <div className={`customer-status-card ${campaignStatus === 'blocked' || p0Count > 0 || (hasMainChainContract && !mainChainReady) || evidenceBlockedItemCount > 0 ? 'danger' : campaignStatus === 'coverage_deferred' || totalRiskCount > 0 ? 'warning' : 'success'}`}>
            <span>当前结论</span>
            <strong>{hasMainChainContract && !mainChainReady ? '主链路未闭合' : evidenceBlockedItemCount > 0 ? '证据标准化未完成' : conclusion}</strong>
            <p>{hasMainChainContract && !mainChainReady ? `第一断点：${firstBlockedStageLabel}。下一步：${firstBlockedNextAction}` : evidenceBlockedItemCount > 0 ? evidenceDetail : conclusionDetail}</p>
          </div>
          <div className="customer-status-meta">
            <span><em>最近扫描</em><b>{formatScanTime(asText(scanMeta.last_scan_at) || asText(record.updated_at))}</b></span>
            <span><em>证据达标</em><b>{deliveryReadiness}%</b></span>
            <span><em>交付 Gate</em><b>{gatePatchLabel(gatePatchEnabled)}</b></span>
            <span><em>主链路</em><b>{mainChainReadyLabel(mainChainReady, hasMainChainContract)}</b></span>
            <span><em>证据标准化</em><b>{evidenceNormalizationLabel(evidenceNormalizationSummary)}</b></span>
            {regressionCovered > 0 && <span><em>回归状态</em><b>{regressionGate}</b></span>}
            <span><em>本轮说明</em><b>{asNum(scanMeta.run_count) ? `第 ${asNum(scanMeta.run_count)} 轮` : campaignStatus ? campaignStatusLabel(campaignStatus) : '首次结果'}</b></span>
          </div>
        </div>
      </section>

      <div className="customer-summary-grid mb-4">
        {[
          { label: '客户可交付', val: totalRiskCount, tone: 'primary', note: totalRiskCount > 0 ? '已验证、可复现、可验收' : governanceNeedsAction ? '未把未覆盖范围误判为无风险' : '当前没有 confirmed 缺陷' },
          { label: '阻断发布', val: p0Count, tone: 'danger', note: p0Count > 0 ? '需要立即闭环' : governanceNeedsAction ? '需先补齐 Campaign 合同或范围' : '当前无阻断项' },
          { label: '主链路', val: mainChainReadyLabel(mainChainReady, hasMainChainContract), tone: hasMainChainContract && !mainChainReady ? 'danger' : 'neutral', note: hasMainChainContract ? mainChainReady ? '六段链路已闭合' : `断在 ${firstBlockedStageLabel}` : '等待后端合同' },
          { label: '证据字段', val: evidenceNormalizationLabel(evidenceNormalizationSummary), tone: evidenceBlockedItemCount > 0 ? 'danger' : 'neutral', note: evidenceMissingFields.length > 0 ? evidenceMissingFields.slice(0, 2).map(([field, count]) => `${evidenceMissingFieldLabel(field)}×${count}`).join('、') : '等待证据标准化报告' },
          ...(regressionCovered > 0 ? [{
            label: '回归闭环',
            val: regressionGate,
            tone: regressionFailed > 0 ? 'danger' : regressionPending > 0 ? 'warning' : 'success',
            note: regressionRunAt ? `${formatScanTime(regressionRunAt)} · 已覆盖 ${regressionCovered} 条` : `已覆盖 ${regressionCovered} 条客户缺陷`,
          }] : []),
          ...(commercialAssets ? [{
            label: '商业交付',
            val: deliveryPackageLabel(commercialAssets),
            tone: commercialAssets.delivery_package.status === 'created' ? 'success' : 'warning',
            note: `${trackerSyncLabel(commercialAssets)} · 客户复验资产 ${commercialAssets.customer_ready_reproduction_count} 条`,
          }] : []),
        ].map((item) => (
          <article key={item.label} className={`customer-summary-card tone-${item.tone}`}><span>{item.label}</span><strong>{item.val}</strong><small>{item.note}</small></article>
        ))}
      </div>

      <section className="customer-value-grid mb-4">
        <article className="customer-value-card"><span className="customer-value-kicker">发布建议</span><h2>{hasMainChainContract && !mainChainReady ? '暂不形成客户交付结论，先闭合主链路' : evidenceBlockedItemCount > 0 ? '暂不形成客户交付结论，先补齐证据字段' : campaignStatus === 'blocked' ? '暂不形成发布结论，先补齐执行合同' : campaignStatus === 'coverage_deferred' ? '先评审递延范围，再决定是否扩大 Campaign' : p0Count > 0 ? '建议暂停发布，先处理阻断缺陷' : totalRiskCount > 0 ? '建议带着缺陷清单推进整改验收' : '当前没有可交付缺陷，可继续观察后续轮次'}</h2><p>{hasMainChainContract && !mainChainReady ? `第一断点：${firstBlockedStageLabel}。下一步：${firstBlockedNextAction}` : evidenceBlockedItemCount > 0 ? evidenceDetail : campaignStatus === 'blocked' || campaignStatus === 'coverage_deferred' ? campaignDetail(campaignStatus, campaignDeferredReason, nextCampaignReason) : p0Count > 0 ? '当前存在会直接影响业务履约或发布安全的高风险缺陷。' : totalRiskCount > 0 ? '当前结果已经足以形成客户整改清单，不需要再从线索里筛。' : '本轮输出可以作为当前阶段的风险结论，但建议继续保持持续检测。'}</p></article>
        <article className="customer-value-card"><span className="customer-value-kicker">客户价值</span><h2>{totalRiskCount > 0 ? `本轮交付 ${totalRiskCount} 个已验证缺陷` : governanceNeedsAction || (hasMainChainContract && !mainChainReady) || evidenceBlockedItemCount > 0 ? '本轮价值在于明确暴露可测试性边界' : '本轮价值在于给出明确风险结论'}</h2><p>{totalRiskCount > 0 ? `缺陷集中在 ${topFamilyLabel} 等方向，证据可信度 ${evidenceTrust}% ，可直接进入修复与复验。` : governanceNeedsAction || (hasMainChainContract && !mainChainReady) || evidenceBlockedItemCount > 0 ? '系统没有把缺少来源、环境、测试数据、真实执行、证据字段或证据链闭合伪装成“通过”，可直接用于推动补齐条件。' : '当前没有把未补证线索冒充成客户缺陷，避免误导客户对结果质量的判断。'}</p></article>
        <article className="customer-value-card"><span className="customer-value-kicker">交付边界</span><h2>{evidenceBlockedItemCount > 0 ? `证据仍有 ${evidenceBlockedItemCount} 项阻断` : hasMainChainContract && !mainChainReady ? `主链路断在：${firstBlockedStageLabel}` : coverageGaps > 0 ? `当前有 ${coverageGaps} 项覆盖缺口` : clueCount > 0 ? `内部仍有 ${clueCount} 条待补证线索` : '当前没有待补证线索'}</h2><p>{evidenceBlockedItemCount > 0 ? evidenceDetail : hasMainChainContract && !mainChainReady ? firstBlockedNextAction : coverageGaps > 0 ? '覆盖缺口不会进入客户缺陷数量，需要通过新的资料、环境、数据或授权条件在后续 Campaign 中关闭。' : clueCount > 0 ? '这些线索不会进入客户成果展示，只作为内部继续采证与复验的运营池。' : '当前结果已经清晰收口，没有把内部线索混进客户视图。'}</p></article>
      </section>

      {topFindings.length === 0 && <div className="mb-4">{secondarySummarySection}</div>}

      <section className="customer-focus-section mb-4">
        <div className="customer-section-head"><div><span className="panel-kicker">重点缺陷</span><h2>客户应该优先关注的结果</h2></div><button className="btn btn-secondary" onClick={() => navigateToProjectPath('/findings', project)}>查看完整缺陷清单</button></div>
        {topFindings.length === 0 ? (
          <section className="findings-empty-state compact"><span className="findings-empty-kicker">当前结论</span><h3>{hasMainChainContract && !mainChainReady ? '主链路未闭合' : evidenceBlockedItemCount > 0 ? '证据标准化未完成' : governanceNeedsAction ? campaignStatusLabel(campaignStatus) : '当前没有客户可交付缺陷'}</h3><p>{hasMainChainContract && !mainChainReady ? `第一断点：${firstBlockedStageLabel}。下一步：${firstBlockedNextAction}` : evidenceBlockedItemCount > 0 ? evidenceDetail : governanceNeedsAction ? campaignDetail(campaignStatus, campaignDeferredReason, nextCampaignReason) : clueCount > 0 ? `本轮仅有 ${clueCount} 条内部线索仍在补证，客户侧暂不展示。` : '当前没有 confirmed 缺陷，说明本轮结果未发现可交付问题。'}</p></section>
        ) : (
          <div className="customer-focus-list">
            {topFindings.map((finding) => (
              <article key={finding.id} className="customer-focus-card"><div className="customer-focus-head"><span className={`severity ${finding.severity.toLowerCase()}`}>{finding.severity}</span><strong>{finding.title}</strong></div><p>{finding.business_summary || finding.business_impact?.summary || finding.actual || '该问题已形成可交付缺陷结论。'}</p><div className="customer-focus-meta"><span><em>影响模块</em><b>{getFindingModule(finding)}</b></span><span><em>证据状态</em><b>{finding.evidence_quality?.label || '已归档'}</b></span><span><em>复现稳定性</em><b>{finding.proof?.repro_rate ?? 0}%</b></span></div></article>
            ))}
          </div>
        )}
      </section>

      {topFindings.length > 0 && secondarySummarySection}
    </div>
  );
}

export default Dashboard;
