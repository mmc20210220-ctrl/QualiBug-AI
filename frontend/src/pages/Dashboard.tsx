import { useCallback, useState } from 'react';
import type { ReactNode } from 'react';
import { useSearchParams } from 'react-router-dom';
import { emitScanCompleted, getCommercialAssets, isCustomerReadyFinding, usePipelineData } from '../api/data';
import { runRegression } from '../api/client';
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

function regressionTrendLabel(direction: string): string {
  const normalized = direction.trim().toLowerCase();
  if (normalized === 'improving') return '趋势向好';
  if (normalized === 'regressing') return '风险上升';
  if (normalized === 'stable') return '趋势持平';
  return '趋势待观察';
}

function releaseRecommendationLabel(value: string, fallback: string): string {
  if (fallback) return fallback;
  const normalized = value.trim().toLowerCase();
  if (normalized === 'block_release') return '建议阻断发布';
  if (normalized === 'continue_regression') return '建议继续执行真实回归';
  if (normalized === 'hold_for_validation') return '建议先完成剩余回归';
  if (normalized === 'candidate_release') return '可进入候选发布';
  if (normalized === 'candidate_acceptance') return '可进入客户验收';
  return '继续观察后续轮次';
}

function getExecutiveHeadline(currentDefectCount: number, familyDefectCount: number, p0Count: number, highPriorityCount: number, clueCount: number, campaignStatus: string, deferredReason: string) {
  if (campaignStatus === 'blocked') return `本轮检测已暂停：${deferredReason || '还需补齐进入执行的必要条件'}。`;
  if (campaignStatus === 'coverage_deferred') return `本轮覆盖已到边界：${deferredReason || '剩余范围已明确递延'}。`;
  if (currentDefectCount > 0 && p0Count > 0) return familyDefectCount > currentDefectCount ? `本轮确认 ${currentDefectCount} 个可交付缺陷，累计 ${familyDefectCount} 个，其中 ${p0Count} 个会直接影响发布。` : `已确认 ${currentDefectCount} 个可交付缺陷，其中 ${p0Count} 个会直接影响发布。`;
  if (currentDefectCount > 0) return familyDefectCount > currentDefectCount ? `本轮确认 ${currentDefectCount} 个可交付缺陷，累计 ${familyDefectCount} 个，可直接进入整改与验收。` : `已确认 ${currentDefectCount} 个可交付缺陷，可直接进入整改与验收。`;
  if (clueCount > 0) return `本轮尚未形成可交付缺陷，仍有 ${clueCount} 条线索在补证中。`;
  if (highPriorityCount === 0) return '当前未发现可交付缺陷，可结合覆盖状态判断发布结论。';
  return '当前没有形成客户可交付缺陷，建议继续真实场景检测。';
}

function getExecutiveDescription(currentDefectCount: number, familyDefectCount: number, clueCount: number, evidenceScore: number, modulesCount: number, campaignStatus: string, deferredReason: string, nextCampaignReason: string) {
  if (campaignStatus === 'blocked' || campaignStatus === 'coverage_deferred') return campaignDetail(campaignStatus, deferredReason, nextCampaignReason);
  if (currentDefectCount > 0) return familyDefectCount > currentDefectCount ? `本页只展示已验证、可复现、具备原始证据的结果。本轮 ${currentDefectCount} 个，累计保留 ${familyDefectCount} 个历史结论。已覆盖 ${modulesCount} 个业务模块，证据可信度 ${evidenceScore}%。` : `本页只展示已验证、可复现、具备原始证据的缺陷结果。已覆盖 ${modulesCount} 个业务模块，证据可信度 ${evidenceScore}%。`;
  if (clueCount > 0) return '当前没有站得住的客户缺陷，说明系统仍在补证。内部线索不会进入客户交付，形成真实证据后再升级展示。';
  return '当前结果代表本轮没有形成客户可交付缺陷。后续新增检测后，这里会自动更新业务结论。';
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
  return blocked > 0 ? `证据标准化未完成：缺字段 ${blocked} 项` : '证据字段已标准化';
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
  usePageTitle('成果总览');
  const [params] = useSearchParams();
  const project = params.get('project')?.trim() || '';
  const { data, loading, error, refetch } = usePipelineData(project);
  const { navigateToProjectPath } = useProjectNavigation();
  const toast = useToast();
  const [regressionRunningMode, setRegressionRunningMode] = useState('');

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

  const handleRegressionRun = useCallback(async (mode: 'smoke' | 'release') => {
    if (!project) return;
    setRegressionRunningMode(mode);
    try {
      toast.show(`正在执行${mode === 'smoke' ? ' Smoke ' : ' Release '}回归...`, 'info');
      const result = await runRegression(project, { mode });
      emitScanCompleted(project);
      await refetch();
      const gateStatus = asText(result.ci_feedback?.gate_status) || 'unknown';
      const failedCount = asNum(result.summary?.failed_count);
      toast.show(
        `${mode === 'smoke' ? 'Smoke' : 'Release'} 回归完成：${gateStatus}${failedCount > 0 ? `，失败 ${failedCount} 项` : ''}`,
        gateStatus === 'failed' ? 'danger' : gateStatus === 'passed' ? 'success' : 'warning',
      );
    } catch (caught: unknown) {
      const message = caught instanceof Error ? caught.message : '回归执行失败';
      toast.show(message, 'danger');
    } finally {
      setRegressionRunningMode('');
    }
  }, [project, refetch, toast]);

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
  if (!project) return <StatePanel eyebrow="客户选择" title="请先选择客户项目" description="成果总览只展示真实项目数据。选择客户后，界面会按该项目的检测结果与证据链自动刷新。" />;

  const record = asRecord(data);
  const commercialAssets = getCommercialAssets(record);
  const findings = ((record.defects || record.risks || []) as Finding[]).filter(isCustomerReadyFinding);
  const clues = ((record.clues || []) as Finding[]);
  const regressionSummary = asRecord(record.regression_summary) as unknown as RegressionSummary;
  const valueMetrics = asRecord(record.value_metrics);
  const scanMeta = asRecord(record.scan_meta);
  const formalCounts = asRecord(record.formal_count_projection);
  const benchmarkMetrics = asRecord(scanMeta.benchmark_metrics);
  const externalEvaluation = asRecord(
    Object.keys(asRecord(record.external_evaluation)).length > 0
      ? record.external_evaluation
      : scanMeta.external_evaluation,
  );
  const qualityClaimStatus = asText(record.quality_claim_status) || asText(scanMeta.quality_claim_status) || asText(externalEvaluation.measurement_status) || 'NOT_MEASURED';
  const externalMeasured = qualityClaimStatus === 'MEASURED' && asText(externalEvaluation.measurement_status) === 'MEASURED';
  const externalDisplay = asRecord(externalEvaluation.display);
  const qualitySuppressed = !externalMeasured || Boolean(externalDisplay.suppress_quality_score) || Boolean(benchmarkMetrics.commercial_quality_suppressed);
  // ── P3: Coverage Matrix from Bug Ontology ──
  const coverageData = asRecord(record.coverage_matrix);
  const evidenceClass = asRecord(record.evidence_classification);
  const benchmarkActive = Boolean(benchmarkMetrics.benchmark_active) && !qualitySuppressed;
  const benchmarkFailed = asText(benchmarkMetrics.status) === 'FAILED_SAFE';
  const gatePatch = getGatePatchStatus(record);
  const gatePatchEnabled = Boolean(gatePatch.core_gate_direct);
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
  const campaignCurrentRawFindings = asNum(campaignSummary.current_campaign_bundle_finding_count_raw);
  const p0Count = findings.filter((finding) => finding.severity === 'P0').length;
  const highPriorityCount = p0Count + findings.filter((finding) => finding.severity === 'P1').length;
  // Raw finding counters are internal observability only — never fall back into customer defect counts.
  const currentScanFindings = asNum(scanMeta.current_report_total_findings, asNum(scanMeta.total_findings, campaignCurrentRawFindings));
  const currentScanDefects = asNum(formalCounts.formal_customer_deliverable_count, totalRiskCount);
  const familyShelfDefects = currentScanDefects;
  const currentScanP0Count = Math.min(p0Count, currentScanDefects);
  const currentScanHighPriorityCount = Math.min(highPriorityCount, currentScanDefects);
  const shelfCarryoverCount = Math.max(0, familyShelfDefects - currentScanDefects);
  const campaignCarryoverDefects = asNum(campaignSummary.family_historical_carryover_defect_count);
  const coverageGaps = Array.isArray(record.coverage_gaps) ? record.coverage_gaps.length : 0;
  const discoveryFunnel = asRecord(record.discovery_funnel);
  const pipelineHealth = asRecord(
    Object.keys(asRecord(record.pipeline_health)).length > 0
      ? record.pipeline_health
      : Object.keys(asRecord(discoveryFunnel.pipeline_health)).length > 0
        ? discoveryFunnel.pipeline_health
        : scanMeta.pipeline_health,
  );
  const pipelineHealthStatus = asText(pipelineHealth.status) || asText(scanMeta.pipeline_health_status);
  const pipelineFailedSafe = pipelineHealthStatus === 'FAILED_SAFE';
  const pipelineBlocked = pipelineHealthStatus === 'BLOCKED';
  const pipelineUnhealthy = pipelineFailedSafe || pipelineBlocked;
  const funnelStages = Array.isArray(discoveryFunnel.stages)
    ? discoveryFunnel.stages.filter((item): item is JsonRecord => item !== null && typeof item === 'object' && !Array.isArray(item))
    : [];
  const funnelBlockers = Array.isArray(discoveryFunnel.top_blocking_reasons)
    ? discoveryFunnel.top_blocking_reasons.filter((item): item is JsonRecord => item !== null && typeof item === 'object' && !Array.isArray(item))
    : [];
  const hasDiscoveryFunnel = funnelStages.length > 0 || Boolean(asText(discoveryFunnel.explanation));
  const funnelValidated = asNum(discoveryFunnel.validated_bug_count);
  const funnelPending = asNum(discoveryFunnel.pending_finding_count);
  const funnelCandidates = asNum(discoveryFunnel.candidate_count);
  const FUNNEL_STAGE_LABELS: Record<string, string> = {
    candidate_generation: '候选生成',
    probe_selection: '探针入选',
    execution: '执行',
    verification: '验证',
    formal_accounting: '正式记账',
  };
  const governanceNeedsAction = campaignStatus === 'blocked' || campaignStatus === 'coverage_deferred' || pipelineUnhealthy;
  const clueCount = clues.length;
  const evidenceTrust = asNum(valueMetrics.evidence_trust_score, 0);
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
  const regressionHasLinkedDefects = regressionCovered > 0 || regressionPassed > 0 || regressionFailed > 0 || regressionPending > 0;
  const regressionGateDisplay = regressionHasLinkedDefects ? regressionGate : '回归待观察';
  const regressionHeadline = regressionHasLinkedDefects ? (regressionSummary.headline || '当前已经把客户缺陷纳入回归闭环，但尚未返回更多细节。') : '当前已存在回归执行记录，但还没有与客户缺陷建立关联。';
  const regressionTrend = regressionTrendLabel(asText(regressionSummary.trend_direction));
  const regressionTrendSummary = asText(regressionSummary.trend_summary);
  const regressionHistoryRunCount = asNum(regressionSummary.history_run_count);
  const regressionRecentRuns = Array.isArray(regressionSummary.recent_runs) ? regressionSummary.recent_runs : [];
  const regressionLifecycleCounts = asRecord(regressionSummary.lifecycle_counts);
  const regressionValidationSummary = asRecord(regressionSummary.validation_summary);
  const regressionDoubleRunVerified = Boolean(regressionValidationSummary.double_run_verified);
  const regressionRepeatedFailures = asNum(regressionValidationSummary.repeated_failure_defect_count);
  const releaseRecommendation = releaseRecommendationLabel(asText(regressionSummary.release_recommendation), asText(regressionSummary.release_recommendation_label));
  const releaseRecommendationReason = asText(regressionSummary.release_recommendation_reason);
  const customerDeliveryReadiness = asText(regressionSummary.customer_delivery_readiness_label) || asText(regressionSummary.customer_delivery_readiness) || '持续观察中';
  const hasMaterializedMetrics = totalRiskCount > 0 || clueCount > 0 || asNum(asRecord(record.business_flow_summary).total, 0) > 0 || Boolean(campaignStatus) || coverageGaps > 0 || hasMainChainContract || hasEvidenceNormalizationSummary;
  const topFindings = [...findings].sort((a, b) => {
    const severityGap = getSeverityWeight(b.severity) - getSeverityWeight(a.severity);
    return severityGap !== 0 ? severityGap : (b.evidence_quality?.score || 0) - (a.evidence_quality?.score || 0);
  }).slice(0, 3);
  const focusFindings = currentScanDefects > 0 ? topFindings : [];
  const topFamilyLabel = findings[0]?.defect_family_label || findings[0]?.defect_family || '核心业务';
  const executiveHeadline = getExecutiveHeadline(currentScanDefects, familyShelfDefects, currentScanP0Count, currentScanHighPriorityCount, clueCount, campaignStatus, campaignDeferredReason);
  const executiveDescription = getExecutiveDescription(currentScanDefects, familyShelfDefects, clueCount, evidenceTrust, modulesCount, campaignStatus, campaignDeferredReason, nextCampaignReason);
  const conclusion = pipelineFailedSafe
    ? '发现链路 FAILED_SAFE'
    : pipelineBlocked
      ? '发现执行被阻断'
      : campaignStatus === 'blocked'
        ? 'Campaign 已阻塞'
        : campaignStatus === 'coverage_deferred'
          ? '覆盖已递延'
          : currentScanP0Count > 0
            ? '存在阻断发布缺陷'
            : currentScanDefects > 0
              ? '建议进入整改验收'
              : '当前无可交付缺陷';
  const conclusionDetail = pipelineUnhealthy
    ? (asText(pipelineHealth.operator_note) || '空 findings / 零缺陷不能解读为目标系统无缺陷，请先修复发现链路。')
    : campaignStatus === 'blocked' || campaignStatus === 'coverage_deferred'
      ? campaignDetail(campaignStatus, campaignDeferredReason, nextCampaignReason)
      : currentScanP0Count > 0
        ? `${currentScanP0Count} 个 P0 缺陷需要优先闭环。`
        : currentScanDefects > 0
          ? `${currentScanHighPriorityCount} 个高风险问题建议先处理。`
          : '本轮结果需结合 Campaign 覆盖状态作为当前阶段风险结论。';

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
        <p>{gatePatchEnabled ? '核心服务已直接绑定严格交付 Gate，未达标结果会进入内部线索池。' : '当前返回体未确认核心交付 Gate 绑定，请检查后端版本与启动入口。'}</p>
        <div className="customer-secondary-meta">
          <span><em>来源</em><b>{gatePatchSource}</b></span>
          <span><em>分流函数</em><b>{activePartitionName}</b></span>
          <span><em>核心直连</em><b>{gatePatch.core_gate_direct ? '是' : '否'}</b></span>
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
            <span><em>本轮缺陷</em><b>{currentScanDefects} 条</b></span>
            <span><em>缺陷货架</em><b>{familyShelfDefects} 条</b></span>
            <span><em>历史延续</em><b>{campaignCarryoverDefects} 条</b></span>
            <span><em>覆盖缺口</em><b>{coverageGaps}</b></span>
          </div>
          {(campaignCurrentRawFindings > 0 || currentScanFindings > currentScanDefects) && (
            <div className="customer-secondary-meta">
              <span><em>内部原始 finding（非客户交付）</em><b>{campaignCurrentRawFindings || Math.max(0, currentScanFindings - currentScanDefects)}</b></span>
              <span><em>口径说明</em><b>回执 {campaignConfirmed} → 本轮可交付 {currentScanDefects} → 当前正式范围 {familyShelfDefects}；原始 finding 仅供内部观测</b></span>
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
      {/* ── Rounds Overview: real data from Rounds 1-4 ── */}
      {(() => {
        const rs = asRecord(asRecord(data).rounds_summary);
        if (!rs || !(rs.round_1_benchmark || rs.round_2_gaps || rs.round_3_learning || rs.round_4_dsl)) return null;
        const r1 = asRecord(rs.round_1_benchmark); const r2 = asRecord(rs.round_2_gaps);
        const r3 = asRecord(rs.round_3_learning); const r4 = asRecord(rs.round_4_dsl);
        const available = asNum(rs.rounds_with_data);
        const cardS: React.CSSProperties = { padding: '8px 10px', borderRadius: '6px', background: '#f0f9ff', border: '1px solid #bae6fd', fontSize: '0.78rem', lineHeight: 1.5 };
        return (
          <article className="customer-secondary-card" style={{ borderLeft: '3px solid var(--primary-color, #3b82f6)' }}>
            <span className="customer-value-kicker">Rounds Pipeline ({available}/4 rounds active)</span>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: '8px', marginTop: '8px' }}>
              {r1.available ? <div style={cardS}><strong>R1 Benchmark</strong><br/>Recall: {asNum(r1.recall) > 0 ? Math.round(asNum(r1.recall)*100)+'%' : '—'}<br/>F1: {asNum(r1.f1_score) > 0 ? Math.round(asNum(r1.f1_score)*100)+'%' : '—'}</div> : null}
              {r2.available ? <div style={cardS}><strong>R2 Gaps</strong><br/>Open: {asNum(r2.currently_open)}<br/>Resolved: {asNum(r2.resolved)}</div> : null}
              {r3.available ? <div style={cardS}><strong>R3 Learning</strong><br/>Probes: {asNum(r3.total_probes_generated)}<br/>Oracles: {asNum(r3.total_oracles_generated)}</div> : null}
              {r4.available ? <div style={cardS}><strong>R4 DSL</strong><br/>Rules: {asNum(r4.total_rules)}<br/>Industries: {asNum(r4.industry_count)}</div> : null}
            </div>
          </article>
        );
      })()}
      {qualitySuppressed && (
        <article className="customer-secondary-card muted">
          <span className="customer-value-kicker">外部质量评测</span>
          <h3>{asText(externalDisplay.quality_label) || '尚未完成外部质量评测'}</h3>
          <p>
            商业召回率/精度/质量分仅来自外部隐藏真值评测。当前状态为 {qualityClaimStatus}
            {asText(externalEvaluation.reason) ? `（${asText(externalEvaluation.reason)}）` : ''}，
            不显示质量分，也不把内部漏斗计数解读为能力指标。
          </p>
          <div className="customer-secondary-meta">
            <span><em>正式可交付缺陷</em><b>{asNum(externalEvaluation.formal_customer_deliverable_count, asNum(asRecord(record.formal_count_projection).formal_customer_deliverable_count, currentScanDefects))}</b></span>
            <span><em>漏斗已验证</em><b>{funnelValidated}</b></span>
            <span><em>内部候选</em><b>{funnelCandidates}</b></span>
            <span><em>商业质量分</em><b>—</b></span>
          </div>
        </article>
      )}
      {benchmarkFailed && (
        <article className="customer-secondary-card muted">
          <span className="customer-value-kicker">检测能力 Benchmark</span>
          <h3>Benchmark 计算失败（FAILED_SAFE）</h3>
          <p>召回/精度指标不可用，不等于「无缺陷」或「能力健康」。原因：{asText(benchmarkMetrics.reason) || 'benchmark_compute_failed'}{asText(benchmarkMetrics.error) ? ` — ${asText(benchmarkMetrics.error)}` : ''}</p>
        </article>
      )}
      {pipelineUnhealthy && (
        <article className="customer-secondary-card muted">
          <span className="customer-value-kicker">发现链路健康</span>
          <h3>{pipelineFailedSafe ? 'FAILED_SAFE：空结果 ≠ 无缺陷' : '执行被阻断：本轮未形成可验证证据'}</h3>
          <p>{asText(pipelineHealth.operator_note) || conclusionDetail}</p>
          <div className="customer-secondary-meta">
            <span><em>链路状态</em><b>{pipelineHealthStatus || '未知'}</b></span>
            <span><em>执行状态</em><b>{asText(pipelineHealth.execution_status) || '—'}</b></span>
            <span><em>执行原因</em><b>{asText(pipelineHealth.execution_reason) || '—'}</b></span>
            <span><em>可观测性</em><b>{asText(pipelineHealth.observability_status) || '—'}</b></span>
            <span><em>零缺陷可解读</em><b>{pipelineHealth.empty_findings_means_no_bugs ? '是' : '否'}</b></span>
          </div>
          {Array.isArray(pipelineHealth.observability_gaps) && pipelineHealth.observability_gaps.length > 0 && (
            <div className="customer-secondary-meta">
              {pipelineHealth.observability_gaps.slice(0, 4).map((item) => {
                const gap = asRecord(item);
                return (
                  <span key={`${asText(gap.kind)}-${asText(gap.status)}-${asText(gap.reason)}`}>
                    <em>{asText(gap.kind) || 'observability'}</em>
                    <b>{asText(gap.status)}{asText(gap.reason) ? ` · ${asText(gap.reason)}` : ''}</b>
                  </span>
                );
              })}
            </div>
          )}
        </article>
      )}
      {benchmarkActive && !benchmarkFailed && (
        <article className={`customer-secondary-card${asNum(benchmarkMetrics.recall) >= 0.8 ? '' : ' muted'}`}>
          <span className="customer-value-kicker">检测能力 Benchmark</span>
          <h3>召回率 {Math.round(asNum(benchmarkMetrics.recall) * 100)}% · 精度 {Math.round(asNum(benchmarkMetrics.precision) * 100)}%</h3>
          <p>
            Ground Truth {asNum(benchmarkMetrics.ground_truth_bug_count)} 个 Seeded Bug · 检出 {asNum(benchmarkMetrics.true_positives)} 个 · 误报 {asNum(benchmarkMetrics.false_positives)} 个 · 漏检 {asNum(benchmarkMetrics.false_negatives)} 个。
            {asNum(benchmarkMetrics.high_value_recall) > 0 ? ` 高价值缺陷 (P0/P1) 召回率 ${Math.round(asNum(benchmarkMetrics.high_value_recall) * 100)}%。` : ''}
          </p>
          <div className="customer-secondary-meta">
            <span><em>F1 Score</em><b>{Math.round(asNum(benchmarkMetrics.f1_score) * 100)}%</b></span>
            <span><em>误报率</em><b>{Math.round(asNum(benchmarkMetrics.false_positive_rate) * 100)}%</b></span>
            <span><em>漏检率</em><b>{Math.round(asNum(benchmarkMetrics.false_negative_rate) * 100)}%</b></span>
            <span><em>证据完整</em><b>{asNum(benchmarkMetrics.evidence_complete_count)}/{asNum(benchmarkMetrics.evidence_total_count)}</b></span>
            <span><em>复现成功率</em><b>{Math.round(asNum(benchmarkMetrics.reproduction_success_rate) * 100)}%</b></span>
            {asNum(benchmarkMetrics.regression_total_count) > 0 && (
              <span><em>回归成功率</em><b>{Math.round(asNum(benchmarkMetrics.regression_success_rate) * 100)}% ({asNum(benchmarkMetrics.regression_passed_count)}/{asNum(benchmarkMetrics.regression_total_count)})</b></span>
            )}
          </div>
          {Object.keys(asRecord(benchmarkMetrics.bug_type_breakdown)).length > 0 && (
            <>
              <h4 style={{ margin: '14px 0 6px', fontSize: '0.95rem', fontWeight: 600 }}>Bug 类型覆盖矩阵</h4>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: '6px' }}>
                {Object.entries(asRecord(benchmarkMetrics.bug_type_breakdown))
                  .sort(([, a], [, b]) => {
                    const ca = asRecord(a); const cb = asRecord(b);
                    const ra = asNum(ca.total) > 0 ? asNum(ca.detected) / asNum(ca.total) : 0;
                    const rb = asNum(cb.total) > 0 ? asNum(cb.detected) / asNum(cb.total) : 0;
                    return ra - rb; // lowest first → attention
                  })
                  .map(([btype, counts]) => {
                    const c = asRecord(counts);
                    const detected = asNum(c.detected);
                    const total = asNum(c.total);
                    const rate = total > 0 ? detected / total : 0;
                    const pct = Math.round(rate * 100);
                    const barColor = rate >= 0.8 ? 'var(--success-color, #16a34a)' : rate >= 0.5 ? 'var(--warning-color, #d97706)' : 'var(--danger-color, #dc2626)';
                    return (
                      <div key={btype} style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '6px 10px', borderRadius: '8px', background: '#f8fafc', border: '1px solid var(--border-color, #e2e8f0)' }}>
                        <span style={{ fontSize: '0.82rem', fontWeight: 500, minWidth: '100px', textTransform: 'capitalize' }}>{btype.replace(/_/g, ' ')}</span>
                        <div style={{ flex: 1, height: '10px', background: '#e2e8f0', borderRadius: '5px', overflow: 'hidden', position: 'relative' }}>
                          <div style={{ width: `${Math.max(pct, 4)}%`, height: '100%', background: barColor, borderRadius: '5px', transition: 'width 0.3s' }} />
                        </div>
                        <span style={{ fontSize: '0.78rem', fontWeight: 600, minWidth: '40px', textAlign: 'right', color: barColor }}>{pct}%</span>
                        <span style={{ fontSize: '0.7rem', color: 'var(--muted-color, #64748b)', minWidth: '36px' }}>{detected}/{total}</span>
                      </div>
                    );
                  })}
              </div>
            </>
          )}
          {/* ── P3: Coverage Matrix from Bug Ontology ── */}
          {Object.keys(asRecord(coverageData.risk_family_coverage)).length > 0 && (
            <>
              <h4 style={{ margin: '14px 0 6px', fontSize: '0.95rem', fontWeight: 600 }}>
                Risk Family 覆盖率 ({asNum(asRecord(coverageData.summary).risk_family_count)} 族)
              </h4>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: '6px' }}>
                {Object.entries(asRecord(coverageData.risk_family_coverage))
                  .sort(([, a], [, b]) => {
                    const ra = asNum(asRecord(a).coverage_rate);
                    const rb = asNum(asRecord(b).coverage_rate);
                    return ra - rb;
                  })
                  .map(([family, dim]) => {
                    const d = asRecord(dim);
                    const rate = asNum(d.coverage_rate);
                    const pct = Math.round(rate * 100);
                    const barColor = rate >= 0.8 ? 'var(--success-color, #16a34a)' : rate >= 0.5 ? 'var(--warning-color, #d97706)' : 'var(--danger-color, #dc2626)';
                    return (
                      <div key={family} style={{ display: 'flex', alignItems: 'center', gap: '6px', padding: '6px 10px', borderRadius: '8px', background: '#f8fafc', border: '1px solid var(--border-color, #e2e8f0)' }}>
                        <span style={{ fontSize: '0.78rem', fontWeight: 500, minWidth: '90px' }}>{family.replace(/_/g, ' ')}</span>
                        <div style={{ flex: 1, height: '8px', background: '#e2e8f0', borderRadius: '4px', overflow: 'hidden' }}>
                          <div style={{ width: `${Math.max(pct, 4)}%`, height: '100%', background: barColor, borderRadius: '4px' }} />
                        </div>
                        <span style={{ fontSize: '0.72rem', fontWeight: 600, color: barColor, minWidth: '32px', textAlign: 'right' }}>{pct}%</span>
                        <span style={{ fontSize: '0.65rem', color: 'var(--muted-color, #64748b)' }}>{asNum(d.covered_items)}/{asNum(d.total_items)}</span>
                      </div>
                    );
                  })}
              </div>
              {/* Evidence Classification Summary */}
              {asRecord(evidenceClass).confirmed !== undefined && (
                <div style={{ marginTop: '12px', display: 'flex', gap: '16px', flexWrap: 'wrap', fontSize: '0.82rem' }}>
                  <span style={{ color: 'var(--muted-color, #64748b)', fontWeight: 600 }}>
                    语义层确认信号: {asNum(evidenceClass.confirmed)}
                  </span>
                  <span style={{ color: 'var(--warning-color, #d97706)', fontWeight: 600 }}>
                    语义层候选: {asNum(evidenceClass.candidate)}
                  </span>
                  <span style={{ color: 'var(--muted-color, #64748b)', fontWeight: 600 }}>
                    待补证线索: {asNum(evidenceClass.clue)}
                  </span>
                  <span style={{ color: 'var(--success-color, #16a34a)', fontWeight: 600 }}>
                    客户可交付缺陷: {currentScanDefects}
                  </span>
                </div>
              )}
              {/* Invariant Coverage Summary */}
              {Object.keys(asRecord(coverageData.invariant_coverage)).length > 0 && (
                <>
                  <h4 style={{ margin: '12px 0 4px', fontSize: '0.85rem', fontWeight: 600 }}>
                    Invariant 覆盖率 ({Object.keys(asRecord(coverageData.invariant_coverage)).length} 类)
                  </h4>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
                    {Object.entries(asRecord(coverageData.invariant_coverage)).map(([inv, dim]) => {
                      const d = asRecord(dim);
                      const rate = asNum(d.coverage_rate);
                      const color = rate >= 0.8 ? '#16a34a' : rate >= 0.5 ? '#d97706' : '#dc2626';
                      return (
                        <span key={inv} style={{ padding: '2px 8px', borderRadius: '12px', fontSize: '0.7rem', background: '#f1f5f9', color, fontWeight: 500 }}>
                          {inv.replace(/_/g, ' ')} {asNum(d.covered_items)}/{asNum(d.total_items)}
                        </span>
                      );
                    })}
                  </div>
                </>
              )}
            </>
          )}
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
          <h3>{regressionGateDisplay}</h3>
          <p>{regressionHeadline}</p>
          <div className="customer-secondary-meta">
            <span><em>已覆盖缺陷</em><b>{regressionCovered}</b></span>
            <span><em>回归通过</em><b>{regressionPassed}</b></span>
            <span><em>回归失败</em><b>{regressionFailed}</b></span>
            <span><em>待执行</em><b>{regressionPending}</b></span>
            <span><em>最近模式</em><b>{asText(regressionSummary.latest_run?.suite_mode_label) || asText(regressionSummary.latest_run?.suite_mode) || '未执行'}</b></span>
            <span><em>最近回归</em><b>{regressionRunAt ? formatScanTime(regressionRunAt) : '暂无'}</b></span>
          </div>
          <div className="customer-showcase-actions">
            <button className="btn btn-primary" onClick={() => void handleRegressionRun('release')} disabled={regressionRunningMode !== ''}>{regressionRunningMode === 'release' ? 'Release 回归中' : '执行 Release 回归'}</button>
            <button className="btn btn-secondary" onClick={() => void handleRegressionRun('smoke')} disabled={regressionRunningMode !== ''}>{regressionRunningMode === 'smoke' ? 'Smoke 回归中' : '执行 Smoke 回归'}</button>
          </div>
        </article>
      )}
      {(regressionHistoryRunCount > 0 || regressionRecentRuns.length > 0) && (
        <article className={`customer-secondary-card${asText(regressionSummary.trend_direction) === 'regressing' ? ' muted' : ''}`}>
          <span className="customer-value-kicker">回归趋势</span>
          <h3>{regressionTrend}</h3>
          <p>{regressionTrendSummary || '当前已开始沉淀多轮回归结果，趋势会随执行轮次自动更新。'}</p>
          <div className="customer-secondary-meta">
            <span><em>历史轮次</em><b>{regressionHistoryRunCount}</b></span>
            <span><em>待回归</em><b>{asNum(regressionLifecycleCounts.pending_regression)}</b></span>
            <span><em>回归失败</em><b>{asNum(regressionLifecycleCounts.regression_failed)}</b></span>
            <span><em>回归通过</em><b>{asNum(regressionLifecycleCounts.verified_fixed)}</b></span>
            <span><em>待复核</em><b>{asNum(regressionLifecycleCounts.manual_review_required)}</b></span>
          </div>
          {regressionRecentRuns.length > 0 && (
            <div className="customer-secondary-meta">
              {regressionRecentRuns.slice(0, 3).map((run, index) => (
                <span key={`${asText(run.generated_at)}-${asText(run.suite_mode)}-${index}`}>
                  <em>{formatScanTime(asText(run.generated_at))}</em>
                  <b>{asText(run.suite_mode_label) || asText(run.suite_mode) || '回归'} · {regressionGateLabel(asText(run.gate_status))} · 失败 {asNum(run.failed_count)}</b>
                </span>
              ))}
            </div>
          )}
        </article>
      )}
      {(releaseRecommendation || regressionHistoryRunCount > 0) && (
        <article className={`customer-secondary-card${asText(regressionSummary.release_recommendation) === 'block_release' ? ' muted' : ''}`}>
          <span className="customer-value-kicker">发布 / 交付建议</span>
          <h3>{releaseRecommendation}</h3>
          <p>{releaseRecommendationReason || '当前建议基于最近多轮真实回归、趋势变化和交付资产状态自动生成。'}</p>
          <div className="customer-secondary-meta">
            <span><em>客户交付</em><b>{customerDeliveryReadiness}</b></span>
            <span><em>历史轮次</em><b>{regressionHistoryRunCount}</b></span>
            <span><em>最小双轮验真</em><b>{regressionDoubleRunVerified ? '已满足' : '未满足'}</b></span>
            <span><em>反复失败缺陷</em><b>{regressionRepeatedFailures}</b></span>
          </div>
        </article>
      )}
      {(regressionHistoryRunCount > 0 || regressionRepeatedFailures > 0) && (
        <article className={`customer-secondary-card${regressionDoubleRunVerified ? '' : ' muted'}`}>
          <span className="customer-value-kicker">真实验真摘要</span>
          <h3>{asText(regressionValidationSummary.headline) || '当前正在沉淀真实多轮回归记录'}</h3>
          <p>{regressionDoubleRunVerified ? '当前已具备最小双轮验真基础，可以开始把回归趋势用于发布和交付判断。' : '当前还不能把一次通过当成稳定结论，建议继续保持真实环境回归。'} </p>
          <div className="customer-secondary-meta">
            <span><em>最小要求</em><b>{asNum(regressionValidationSummary.minimum_required_runs, 2)} 轮</b></span>
            <span><em>当前轮次</em><b>{regressionHistoryRunCount}</b></span>
            <span><em>趋势结论</em><b>{regressionTrend}</b></span>
            <span><em>反复失败</em><b>{regressionRepeatedFailures}</b></span>
          </div>
          {regressionRecentRuns.length > 0 && (
            <div className="customer-secondary-meta">
              {regressionRecentRuns.slice(0, 3).map((run, index) => (
                <span key={`validation-${asText(run.generated_at)}-${asText(run.suite_mode)}-${index}`}>
                  <em>{formatScanTime(asText(run.generated_at))}</em>
                  <b>{asText(run.suite_mode_label) || asText(run.suite_mode) || '回归'} · {regressionGateLabel(asText(run.gate_status))}</b>
                </span>
              ))}
            </div>
          )}
        </article>
      )}
    </section>
  );

  if (!hasMaterializedMetrics) {
    return (
      <div>
        <div className="page-header"><div><h1>{asText(record.project_name) || project} · 成果总览</h1><p>当前项目还没有形成真实风险数据或验证结果。</p></div></div>
        <StatePanel eyebrow="结果状态" title="当前还没有形成可展示的真实指标" description="本项目暂未产生行为发现、执行探针或数据验证结果。运行检测后，页面会自动切换为真实业务视图。" />
      </div>
    );
  }

  const heroPills = [
    { label: `本轮缺陷 ${currentScanDefects}`, strong: true },
    { label: `累计结论 ${familyShelfDefects}`, strong: false },
    { label: `阻断发布 ${currentScanP0Count}`, strong: currentScanP0Count > 0 },
    ...(regressionCovered > 0 || regressionSummary.suite_exists ? [{ label: `回归 ${regressionGateDisplay}`, strong: false }] : []),
    ...(commercialAssets ? [{ label: commercialHandoffLabel(commercialAssets), strong: false }] : []),
    { label: evidenceTrust > 0 ? `证据可信度 ${evidenceTrust}%` : evidenceNormalizationLabel(evidenceNormalizationSummary), strong: false },
  ].slice(0, 5);

  return (
    <div className="customer-results-page">
      <section className="customer-showcase mb-4">
        <div className="customer-showcase-main">
          <span className="panel-kicker">本轮客户成果</span>
          <h1>{asText(record.project_name) || project}</h1>
          <p className="customer-showcase-headline">{executiveHeadline}</p>
          <p>{executiveDescription}</p>
          <div className="page-summary-strip">
            {heroPills.map((pill) => (
              <span key={pill.label} className={`summary-pill${pill.strong ? ' strong' : ''}`}>{pill.label}</span>
            ))}
          </div>
          <div className="customer-showcase-actions">
            <button className="btn btn-primary" onClick={() => navigateToProjectPath('/findings', project)}>查看缺陷清单</button>
            <button className="btn btn-secondary" onClick={() => navigateToProjectPath('/evidence', project)}>查看证据链</button>
            <button className="btn btn-secondary" onClick={handleExport}>导出成果摘要</button>
          </div>
        </div>
        <div className="customer-showcase-side">
          <div className={`customer-status-card ${campaignStatus === 'blocked' || currentScanP0Count > 0 || (hasMainChainContract && !mainChainReady) || evidenceBlockedItemCount > 0 ? 'danger' : campaignStatus === 'coverage_deferred' || currentScanDefects > 0 ? 'warning' : 'success'}`}>
            <span>当前结论</span>
            <strong>{hasMainChainContract && !mainChainReady ? '主链路未闭合' : evidenceBlockedItemCount > 0 ? '证据尚未齐备' : conclusion}</strong>
            <p>{hasMainChainContract && !mainChainReady ? `第一断点：${firstBlockedStageLabel}。下一步：${firstBlockedNextAction}` : evidenceBlockedItemCount > 0 ? evidenceDetail : conclusionDetail}</p>
          </div>
          <div className="customer-status-meta">
            <span><em>最近检测</em><b>{formatScanTime(asText(scanMeta.last_scan_at) || asText(record.updated_at))}</b></span>
            <span><em>证据达标</em><b>{deliveryReadiness}%</b></span>
            <span><em>覆盖模块</em><b>{modulesCount || '—'}</b></span>
            {(regressionCovered > 0 || regressionSummary.suite_exists) && <span><em>回归状态</em><b>{regressionGateDisplay}</b></span>}
            <span><em>本轮说明</em><b>{asNum(scanMeta.run_count) ? `第 ${asNum(scanMeta.run_count)} 轮` : campaignStatus ? campaignStatusLabel(campaignStatus) : '首次结果'}</b></span>
          </div>
        </div>
      </section>

      <div className="customer-summary-grid mb-4">
        {[
          { label: '本轮可交付', val: currentScanDefects, tone: 'primary', note: currentScanDefects > 0 ? `当前确认 ${currentScanDefects} 条；累计 ${familyShelfDefects} 条` : governanceNeedsAction ? '未把未覆盖范围误判为无风险' : '当前没有已确认缺陷' },
          { label: '累计结论', val: familyShelfDefects, tone: familyShelfDefects > currentScanDefects ? 'warning' : 'neutral', note: familyShelfDefects > currentScanDefects ? `含历史延续 ${Math.max(campaignCarryoverDefects, shelfCarryoverCount)} 条` : '当前与本轮结果一致' },
          { label: '阻断发布', val: currentScanP0Count, tone: 'danger', note: currentScanP0Count > 0 ? '需要立即闭环' : governanceNeedsAction ? '需先补齐执行条件' : '当前无阻断项' },
          { label: '证据可信度', val: evidenceTrust > 0 ? `${evidenceTrust}%` : evidenceNormalizationLabel(evidenceNormalizationSummary), tone: evidenceBlockedItemCount > 0 ? 'danger' : evidenceTrust >= 80 ? 'success' : 'neutral', note: evidenceMissingFields.length > 0 ? evidenceMissingFields.slice(0, 2).map(([field, count]) => `${evidenceMissingFieldLabel(field)}×${count}`).join('、') : modulesCount > 0 ? `已覆盖 ${modulesCount} 个业务模块` : '等待证据报告' },
          ...(regressionCovered > 0 ? [{
            label: '回归闭环',
            val: regressionGate,
            tone: regressionFailed > 0 ? 'danger' : regressionPending > 0 ? 'warning' : 'success',
            note: regressionRunAt ? `${formatScanTime(regressionRunAt)} · 已覆盖 ${regressionCovered} 条` : `已覆盖 ${regressionCovered} 条客户缺陷`,
          }] : []),
          ...(releaseRecommendation ? [{
            label: '发布建议',
            val: releaseRecommendation,
            tone: asText(regressionSummary.release_recommendation) === 'block_release' ? 'danger' : asText(regressionSummary.release_recommendation) === 'candidate_release' ? 'success' : 'warning',
            note: releaseRecommendationReason || customerDeliveryReadiness,
          }] : []),
          ...(commercialAssets ? [{
            label: '商业交付',
            val: deliveryPackageLabel(commercialAssets),
            tone: commercialAssets.delivery_package.status === 'created' ? 'success' : 'warning',
            note: `${trackerSyncLabel(commercialAssets)} · 客户复验 ${commercialAssets.customer_ready_reproduction_count} 条`,
          }] : []),
          ...(benchmarkActive ? [{
            label: '检测召回率',
            val: `${Math.round(asNum(benchmarkMetrics.recall) * 100)}%`,
            tone: asNum(benchmarkMetrics.recall) >= 0.8 ? 'success' : asNum(benchmarkMetrics.recall) >= 0.5 ? 'warning' : 'danger',
            note: `精度 ${Math.round(asNum(benchmarkMetrics.precision) * 100)}% · F1 ${Math.round(asNum(benchmarkMetrics.f1_score) * 100)}%`,
          }] : []),
        ].map((item) => (
          <article key={item.label} className={`customer-summary-card tone-${item.tone}`}><span>{item.label}</span><strong>{item.val}</strong><small>{item.note}</small></article>
        ))}
      </div>

      <section className="customer-value-grid mb-4">
        <article className="customer-value-card"><span className="customer-value-kicker">发布建议</span><h2>{pipelineUnhealthy ? '暂不形成「无缺陷」结论，先修复发现链路' : hasMainChainContract && !mainChainReady ? '暂不形成交付结论，先闭合主链路' : evidenceBlockedItemCount > 0 ? '暂不形成交付结论，先补齐证据' : campaignStatus === 'blocked' ? '暂不形成发布结论，先补齐执行条件' : campaignStatus === 'coverage_deferred' ? '先评审递延范围，再决定是否扩大检测' : currentScanP0Count > 0 ? '建议暂停发布，先处理阻断缺陷' : currentScanDefects > 0 ? '建议带着本轮缺陷清单推进整改验收' : '当前没有可交付缺陷，可继续观察后续轮次'}</h2><p>{pipelineUnhealthy ? (asText(pipelineHealth.operator_note) || conclusionDetail) : hasMainChainContract && !mainChainReady ? `第一断点：${firstBlockedStageLabel}。下一步：${firstBlockedNextAction}` : evidenceBlockedItemCount > 0 ? evidenceDetail : campaignStatus === 'blocked' || campaignStatus === 'coverage_deferred' ? campaignDetail(campaignStatus, campaignDeferredReason, nextCampaignReason) : currentScanP0Count > 0 ? '当前存在会直接影响业务履约或发布安全的高风险缺陷。' : currentScanDefects > 0 ? '当前结果已经足以形成客户整改清单，不需要再从线索里筛。' : '本轮输出可以作为当前阶段的风险结论，但建议继续保持持续检测。'}</p></article>
        <article className="customer-value-card"><span className="customer-value-kicker">客户价值</span><h2>{pipelineUnhealthy ? '本轮价值在于暴露发现链路故障，而非宣称无缺陷' : currentScanDefects > 0 ? `本轮交付 ${currentScanDefects} 个已验证缺陷，累计 ${familyShelfDefects} 个` : governanceNeedsAction || (hasMainChainContract && !mainChainReady) || evidenceBlockedItemCount > 0 ? '本轮价值在于明确暴露可测试性边界' : '本轮价值在于给出明确风险结论'}</h2><p>{pipelineUnhealthy ? '系统没有把 FAILED_SAFE / 执行阻断伪装成“通过”或“无缺陷”，可直接用于推动修复账号、探针与执行可观测性。' : currentScanDefects > 0 ? `缺陷集中在 ${topFamilyLabel} 等方向，证据可信度 ${evidenceTrust}% ，其中 ${Math.max(campaignCarryoverDefects, shelfCarryoverCount)} 条为历史延续，可直接进入修复与复验。` : governanceNeedsAction || (hasMainChainContract && !mainChainReady) || evidenceBlockedItemCount > 0 ? '系统没有把缺少来源、环境、测试数据、真实执行或证据链闭合伪装成“通过”，可直接用于推动补齐条件。' : '当前没有把未补证线索冒充成客户缺陷，避免误导对结果质量的判断。'}</p></article>
        <article className="customer-value-card"><span className="customer-value-kicker">交付边界</span><h2>{evidenceBlockedItemCount > 0 ? `证据仍有 ${evidenceBlockedItemCount} 项待补齐` : hasMainChainContract && !mainChainReady ? `主链路断在：${firstBlockedStageLabel}` : coverageGaps > 0 ? `当前有 ${coverageGaps} 项覆盖缺口` : clueCount > 0 ? `内部仍有 ${clueCount} 条待补证线索` : '当前没有待补证线索'}</h2><p>{evidenceBlockedItemCount > 0 ? evidenceDetail : hasMainChainContract && !mainChainReady ? firstBlockedNextAction : coverageGaps > 0 ? '覆盖缺口不会进入客户缺陷数量，需要通过新的资料、环境、数据或授权条件在后续检测中关闭。' : clueCount > 0 ? '这些线索不会进入客户成果展示，只作为内部继续采证与复验的运营池。' : '当前结果已经清晰收口，没有把内部线索混进客户视图。'}</p></article>
      </section>

      <section className="customer-secondary-grid mb-4">
        <article className={`customer-secondary-card${hasDiscoveryFunnel ? '' : ' muted'}`}>
          <span className="customer-value-kicker">发现漏斗</span>
          {hasDiscoveryFunnel ? (
            <>
              <h3>
                {pipelineUnhealthy ? `${pipelineHealthStatus} · ` : ''}
                已验证 Bug {funnelValidated}
                <span style={{ fontWeight: 400, fontSize: '0.85em', marginLeft: 8 }}>
                  · 待确认发现 {funnelPending} · 候选 {funnelCandidates}
                </span>
              </h3>
              <p>{asText(discoveryFunnel.explanation) || '本轮漏斗已生成。'}</p>
              <div className="customer-secondary-meta">
                {funnelStages.map((stage) => {
                  const name = asText(stage.name);
                  const label = FUNNEL_STAGE_LABELS[name] || name || '阶段';
                  const input = asNum(stage.input);
                  const output = asNum(stage.output);
                  const conversion = asNum(stage.conversion);
                  const pct = conversion > 0 ? `${Math.round(conversion * 100)}%` : (input > 0 ? '0%' : '—');
                  return (
                    <span key={name || label}>
                      <em>{label}</em>
                      <b>{output}/{input}（{pct}）</b>
                    </span>
                  );
                })}
              </div>
              {funnelBlockers.length > 0 && (
                <div className="customer-secondary-meta">
                  {funnelBlockers.slice(0, 5).map((item) => (
                    <span key={`${asText(item.reason)}-${asNum(item.count)}`}>
                      <em>阻断 {asText(item.reason) || '未知'}</em>
                      <b>{asNum(item.count)}</b>
                    </span>
                  ))}
                </div>
              )}
            </>
          ) : (
            <>
              <h3>暂无漏斗数据</h3>
              <p>完成一次检测后，将展示转化路径与阻断原因。不会填充假数字。</p>
            </>
          )}
        </article>
      </section>

      {focusFindings.length === 0 && <div className="mb-4">{secondarySummarySection}</div>}

      <section className="customer-focus-section mb-4">
        <div className="customer-section-head"><div><span className="panel-kicker">重点缺陷</span><h2>客户应优先关注的结果</h2></div><button className="btn btn-secondary" onClick={() => navigateToProjectPath('/findings', project)}>查看完整清单</button></div>
        {focusFindings.length === 0 ? (
          <section className="findings-empty-state compact"><span className="findings-empty-kicker">当前结论</span><h3>{hasMainChainContract && !mainChainReady ? '主链路未闭合' : evidenceBlockedItemCount > 0 ? '证据尚未齐备' : governanceNeedsAction ? campaignStatusLabel(campaignStatus) : '当前没有客户可交付缺陷'}</h3><p>{hasMainChainContract && !mainChainReady ? `第一断点：${firstBlockedStageLabel}。下一步：${firstBlockedNextAction}` : evidenceBlockedItemCount > 0 ? evidenceDetail : governanceNeedsAction ? campaignDetail(campaignStatus, campaignDeferredReason, nextCampaignReason) : clueCount > 0 ? `本轮仅有 ${clueCount} 条内部线索仍在补证，客户侧暂不展示。` : familyShelfDefects > 0 ? <>当前仍保留 {familyShelfDefects} 条历史结论，但本轮没有新增已确认缺陷。</> : '当前没有已确认缺陷，说明本轮结果未发现可交付问题。'}</p></section>
        ) : (
          <div className="customer-focus-list">
            {focusFindings.map((finding) => (
              <article key={finding.id} className="customer-focus-card"><div className="customer-focus-head"><span className={`severity ${finding.severity.toLowerCase()}`}>{finding.severity}</span><strong>{finding.title}</strong></div><p>{finding.business_summary || finding.business_impact?.summary || finding.actual || '该问题已形成可交付缺陷结论。'}</p><div className="customer-focus-meta"><span><em>影响模块</em><b>{getFindingModule(finding)}</b></span><span><em>证据状态</em><b>{finding.evidence_quality?.label || '已归档'}</b></span><span><em>复现稳定性</em><b>{finding.proof?.repro_rate ?? 0}%</b></span></div></article>
            ))}
          </div>
        )}
      </section>

      {focusFindings.length > 0 && secondarySummarySection}
    </div>
  );
}

export default Dashboard;
