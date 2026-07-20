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
import { AnimatedCounter } from '../components/AnimatedCounter';
import { ValueDashboard } from '../components/ValueDashboard';
import { TechnicalDiagnostics } from '../components/TechnicalDiagnostics';
import type { CommercialAssets, Finding, RegressionSummary } from '../types';

type JsonRecord = Record<string, unknown>;

const MAIN_CHAIN_STAGE_LABELS: Record<string, string> = {
  enterprise_inputs: '企业资料', knowledge_parse: '解析知识', test_plan: '测试计划',
  execution: '真实执行', bug_discovery: 'Bug 发现', evidence_chain: '证据链',
};
const MAIN_CHAIN_STATUS_LABELS: Record<string, string> = { passed: '通过', partial: '部分', missing: '缺失' };
const EVIDENCE_MISSING_FIELD_LABELS: Record<string, string> = {
  issue_id: '稳定 issue_id', request: '原始 request', response: '原始 response',
  expected: 'expected', actual: 'actual', reproduction_or_replay: 'reproduction / replay',
  execution_receipt: 'execution_receipt', non_synthetic_evidence: '非 synthetic 证据',
  missing_raw_request: '原始 request', missing_raw_response: '原始 response',
  missing_expected_actual_pair: 'expected + actual', missing_replay_or_reproduction: 'reproduction / replay',
  missing_execution_receipt: 'execution_receipt', synthetic_evidence_present: 'synthetic 证据',
  strict_evidence_not_linked_to_all_issues: '严格证据未覆盖全部 issue',
};

function Skeleton({ h = 20, w = '100%', br = 4, className = '' }: { h?: number; w?: string | number; br?: number; className?: string }) {
  return <div className={`skeleton-block${className ? ` ${className}` : ''}`} style={{ height: h, width: w, borderRadius: br }} />;
}
function StatePanel({ eyebrow, title, description, action }: { eyebrow: string; title: string; description: string; action?: ReactNode }) {
  return (<section className="state-panel"><div className="state-panel-badge">{eyebrow}</div><h2>{title}</h2><p>{description}</p>{action ? <div className="state-panel-actions">{action}</div> : null}</section>);
}
function asRecord(value: unknown): JsonRecord { return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as JsonRecord : {}; }
function asText(value: unknown): string { return typeof value === 'string' ? value.trim() : ''; }
function asNum(v: unknown, fallback = 0): number { const p = typeof v === 'number' ? v : Number(v); return Number.isFinite(p) ? p : fallback; }
function firstNum(...values: unknown[]): number { for (const v of values) { const p = typeof v === 'number' ? v : Number(v); if (Number.isFinite(p)) return p; } return 0; }
function formatScanTime(value: string) { if (!value) return '暂无'; const d = new Date(value); if (Number.isNaN(d.getTime())) return value; return d.toLocaleString('zh-CN', { hour12: false }); }
function getSeverityWeight(s: Finding['severity']) { if (s === 'P0') return 3; if (s === 'P1') return 2; return 1; }
function getFindingModule(f: Finding) { return String(f.business_impact?.module || f.source_entity || f.defect_family_label || '核心业务').trim() || '核心业务'; }

function riskLevel(conclusion: string): 'safe' | 'attention' | 'blocked' {
  if (conclusion.includes('阻断') || conclusion.includes('暂停') || conclusion.includes('异常') || conclusion.includes('阻塞')) return 'blocked';
  if (conclusion.includes('整改') || conclusion.includes('关注') || conclusion.includes('递延') || conclusion.includes('未完成')) return 'attention';
  return 'safe';
}
function releaseDecision(p0: number, defects: number, unhealthy: boolean, blocked: boolean): { color: 'red' | 'yellow' | 'green'; label: string; advice: string } {
  if (unhealthy || blocked) return { color: 'yellow', label: '待确认', advice: '检测流程尚未完成，暂不能形成发布结论' };
  if (p0 > 0) return { color: 'red', label: '建议阻断', advice: `${p0} 个严重问题需优先修复，建议暂停发布` };
  if (defects > 0) return { color: 'yellow', label: '有条件发布', advice: `${defects} 个已确认问题建议评估后决策` };
  return { color: 'green', label: '可以发布', advice: '当前未发现阻断性问题，可正常推进发布' };
}
function campaignStatusLabel(s: string): string {
  if (s === 'blocked') return '检测暂停（需补充条件）';
  if (s === 'coverage_deferred') return '部分范围待后续检测';
  if (s === 'completed') return '本轮检测已完成';
  if (s === 'active') return '检测进行中';
  return '检测状态待同步';
}
function campaignDetail(s: string, reason: string, next: string): string {
  if (s === 'blocked') return `当前检测未进入执行阶段：${reason || '缺少必要条件'}。${next ? ` 下一步：${next}。` : ''}`;
  if (s === 'coverage_deferred') return `自动范围已到边界，剩余覆盖被明确递延。${reason ? ` 原因：${reason}。` : ''}`;
  if (s === 'completed') return '本轮可执行范围已完成。';
  if (s === 'active') return '当前检测仍在执行中。';
  return '当前没有可用的检测治理状态。';
}
function commercialHandoffLabel(a: CommercialAssets | null): string { const s = a?.commercial_handoff.status || a?.status || ''; if (s === 'commercial_handoff_ready_with_validated_findings') return '交付已就绪'; if (s === 'ready_for_customer_acceptance') return '待验收'; if (s === 'materialized') return '交付资产已生成'; if (s === 'empty') return '尚未生成'; return s || '未上报'; }
function trackerSyncLabel(a: CommercialAssets | null): string { const s = a?.tracker_sync.payload_status || ''; if (s === 'external_tracker_sync_payloads_blocked_or_empty') return '待同步草稿'; if (s === 'external_tracker_sync_payloads_ready') return '同步已就绪'; return s || '未上报'; }
function deliveryPackageLabel(a: CommercialAssets | null): string { const s = a?.delivery_package.status || ''; if (s === 'created') return '交付包已创建'; if (s === 'not_created') return '交付包未生成'; return s || '未上报'; }
function regressionGateLabel(s: string): string { const n = s.trim().toLowerCase(); if (n === 'failed') return '回归失败'; if (n === 'passed') return '回归通过'; if (n === 'manual_approval_required') return '回归待审批'; return n ? '回归待执行' : '回归未上报'; }
function regressionTrendLabel(d: string): string { const n = d.trim().toLowerCase(); if (n === 'improving') return '趋势向好'; if (n === 'regressing') return '风险上升'; if (n === 'stable') return '趋势持平'; return '趋势待观察'; }
function releaseRecommendationLabel(v: string, fb: string): string { if (fb) return fb; const n = v.trim().toLowerCase(); if (n === 'block_release') return '建议阻断发布'; if (n === 'continue_regression') return '建议继续回归'; if (n === 'hold_for_validation') return '建议先完成回归'; if (n === 'candidate_release') return '可进入候选发布'; if (n === 'candidate_acceptance') return '可进入验收'; return '继续观察'; }
function getExecutiveHeadline(defects: number, family: number, p0: number, clues: number, cs: string, reason: string) {
  if (cs === 'blocked') return `检测已暂停：${reason || '还需补齐必要条件'}。`;
  if (cs === 'coverage_deferred') return `本轮覆盖已到边界：${reason || '剩余范围已明确递延'}。`;
  if (defects > 0 && p0 > 0) return family > defects ? `已确认 ${defects} 个问题，累计 ${family} 个，其中 ${p0} 个会直接影响发布。` : `已确认 ${defects} 个问题，其中 ${p0} 个会直接影响发布。`;
  if (defects > 0) return family > defects ? `已确认 ${defects} 个问题，累计 ${family} 个，可直接进入整改。` : `已确认 ${defects} 个问题，可直接进入整改与验收。`;
  if (clues > 0) return `本轮尚未形成确认问题，仍有 ${clues} 条线索在补证中。`;
  return '当前未发现问题，可结合覆盖状态判断发布结论。';
}
function getGatePatchStatus(r: JsonRecord): JsonRecord { const d = asRecord(r.customer_delivery_gate_patch); if (Object.keys(d).length > 0) return d; return asRecord(asRecord(r.data_contract).customer_delivery_gate_patch); }
function getMainChainContract(r: JsonRecord): JsonRecord { const d = asRecord(r.main_chain_contract); if (Object.keys(d).length > 0) return d; return asRecord(asRecord(r.data_contract).main_chain_contract); }
function getMainChainSummary(r: JsonRecord, c: JsonRecord): JsonRecord { const d = asRecord(r.main_chain_contract_summary); if (Object.keys(d).length > 0) return d; const s = asRecord(c.summary); if (Object.keys(s).length > 0) return { chain_ready: c.chain_ready, first_blocked_stage: s.first_blocked_stage, first_blocked_next_action: s.first_blocked_next_action, passed_stage_count: s.passed_stage_count, partial_stage_count: s.partial_stage_count, missing_stage_count: s.missing_stage_count }; return {}; }
function getMainChainStages(c: JsonRecord): JsonRecord[] { if (!Array.isArray(c.stages)) return []; return c.stages.map(asRecord).filter((s) => Object.keys(s).length > 0); }
function getEvidenceNormalizationSummary(r: JsonRecord): JsonRecord { const d = asRecord(r.evidence_bundle_normalization_summary); if (Object.keys(d).length > 0) return d; return asRecord(asRecord(r.data_contract).evidence_bundle_normalization_summary); }
function getEvidenceNormalizationReport(r: JsonRecord): JsonRecord { const d = asRecord(r.evidence_bundle_normalization_report); if (Object.keys(d).length > 0) return d; return asRecord(asRecord(r.data_contract).evidence_bundle_normalization_report); }
function evidenceNormalizationItems(rep: JsonRecord): JsonRecord[] { if (!Array.isArray(rep.items)) return []; return rep.items.map(asRecord).filter((i) => Object.keys(i).length > 0); }
function evidenceMissingEntries(s: JsonRecord): Array<[string, number]> { const m = asRecord(s.missing_fields); return Object.entries(m).map(([f, c]) => [f, asNum(c)] as [string, number]).filter(([, c]) => c > 0).sort((a, b) => b[1] - a[1]); }
function evidenceMissingFieldLabel(f: string): string { return EVIDENCE_MISSING_FIELD_LABELS[f] || f; }
function evidenceItemTitle(i: JsonRecord): string { return [asText(i.evidence_id), asText(i.issue_id), asText(i.probe_id)].filter(Boolean).join(' / ') || '未命名证据项'; }
function evidenceItemAction(i: JsonRecord): string { return asText(i.next_action) || '补齐缺失字段后重新运行。'; }
function evidenceNormalizationLabel(s: JsonRecord): string { if (Object.keys(s).length === 0) return '未上报'; const b = asNum(s.blocked_item_count); return b > 0 ? `未完成：缺 ${b} 项` : '已标准化'; }
function mainChainStageLabel(s: JsonRecord): string { const k = asText(s.stage); return MAIN_CHAIN_STAGE_LABELS[k] || k || '未知'; }
function mainChainStatusLabel(s: JsonRecord): string { const st = asText(s.status) || (s.ok === true ? 'passed' : 'missing'); return MAIN_CHAIN_STATUS_LABELS[st] || st || '未知'; }
function mainChainReadyLabel(ready: boolean, has: boolean): string { if (!has) return '检测流程未上报'; return ready ? '检测流程已完成' : '检测流程未完成'; }
function gatePatchLabel(p: boolean): string { return p ? '严格质量门已启用' : '严格质量门未确认'; }

function RiskRing({ level, size = 88 }: { level: 'safe' | 'attention' | 'blocked'; size?: number }) {
  const colors = { safe: '#0c9a6a', attention: '#c9780a', blocked: '#d91f45' };
  const labels = { safe: '安全', attention: '关注', blocked: '阻断' };
  const color = colors[level]; const r = (size - 12) / 2; const circ = 2 * Math.PI * r;
  const progress = level === 'safe' ? 1 : level === 'attention' ? 0.6 : 0.3;
  return (<div className="risk-ring" style={{ width: size, height: size }}><svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}><circle cx={size/2} cy={size/2} r={r} fill="none" stroke="rgba(255,255,255,0.1)" strokeWidth="7" /><circle cx={size/2} cy={size/2} r={r} fill="none" stroke={color} strokeWidth="7" strokeLinecap="round" strokeDasharray={`${circ * progress} ${circ}`} transform={`rotate(-90 ${size/2} ${size/2})`} style={{ transition: 'stroke-dasharray 1s ease' }} /></svg><span className="risk-ring-label" style={{ color }}>{labels[level]}</span></div>);
}
function ReleaseLight({ color, label, advice }: { color: 'red' | 'yellow' | 'green'; label: string; advice: string }) {
  const cm = { red: '#d91f45', yellow: '#c9780a', green: '#0c9a6a' };
  return (<div className="release-light"><div className="release-light-indicator" style={{ background: cm[color], boxShadow: `0 0 20px ${cm[color]}66` }} /><strong>{label}</strong><p>{advice}</p></div>);
}

export function Dashboard() {
  usePageTitle('价值总览');
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
      toast.show('正在生成价值报告...', 'info');
      const findings = ((record.defects || record.risks || []) as Finding[]);
      const valueMetrics = asRecord(record.value_metrics);
      const scores = asRecord(valueMetrics.scores);
      const reportData = buildReportData({ projectName: asText(record.project_name) || project, industry: asText(record.industry), totalBugs: findings.length, beiScore: asNum(scores.bei), bdsScore: String(scores.bds || '0.0'), bcsScore: asNum(scores.bcs), runtimeProbes: asNum(asRecord(record.business_flow_summary).total), dbConfirmed: asNum(asRecord(record.db_verification).confirmed), findings, dbFindings: [] });
      const html = renderReportHTML(reportData);
      const blob = new Blob([html], { type: 'text/html;charset=utf-8' });
      window.open(URL.createObjectURL(blob), '_blank');
      toast.show('价值报告已在新标签页打开', 'success');
    } catch (caught: unknown) { toast.show(`导出失败: ${caught instanceof Error ? caught.message : '未知错误'}`, 'danger'); }
  }, [project, data, toast]);

  const handleRegressionRun = useCallback(async (mode: 'smoke' | 'release') => {
    if (!project) return;
    setRegressionRunningMode(mode);
    try {
      toast.show(`正在执行 ${mode === 'smoke' ? 'Smoke' : 'Release'} 回归...`, 'info');
      const result = await runRegression(project, { mode });
      emitScanCompleted(project);
      await refetch();
      const gs = asText(result.ci_feedback?.gate_status) || 'unknown';
      const fc = asNum(result.summary?.failed_count);
      toast.show(`回归完成：${gs}${fc > 0 ? `，失败 ${fc} 项` : ''}`, gs === 'failed' ? 'danger' : gs === 'passed' ? 'success' : 'warning');
    } catch (caught: unknown) { toast.show(caught instanceof Error ? caught.message : '回归执行失败', 'danger'); }
    finally { setRegressionRunningMode(''); }
  }, [project, refetch, toast]);

  if (loading) return (<div><div className="executive-hero"><div className="executive-hero-conclusion"><Skeleton h={20} w={100} br={10} /><div style={{marginTop:12}}><Skeleton h={32} w="70%" br={6} /></div><div style={{marginTop:8}}><Skeleton h={16} w="90%" /></div></div><div className="executive-hero-metrics">{[1,2,3,4].map((i) => <div key={i} className="executive-metric"><Skeleton h={36} w={60} br={6} /><Skeleton h={14} w={80} /></div>)}</div><div className="executive-hero-decision"><Skeleton h={80} w={120} br={12} /></div></div></div>);
  if (error && !data) return <StatePanel eyebrow="连接状态" title="后端暂时不可用" description={error} action={<button className="btn btn-primary" onClick={refetch}>重新连接</button>} />;
  if (!project) return <StatePanel eyebrow="开始使用" title="选择客户项目，查看检测价值" description="选择客户后，您将看到：已确认的问题清单、发布安全建议、AI 检测节省的人力成本量化，以及完整的证据链。" />;

  const record = asRecord(data);
  const commercialAssets = getCommercialAssets(record);
  const findings = ((record.defects || record.risks || []) as Finding[]).filter(isCustomerReadyFinding);
  const clues = ((record.clues || []) as Finding[]);
  const regressionSummary = asRecord(record.regression_summary) as unknown as RegressionSummary;
  const valueMetrics = asRecord(record.value_metrics);
  const scanMeta = asRecord(record.scan_meta);
  const formalCounts = asRecord(record.formal_count_projection);
  const benchmarkMetrics = asRecord(scanMeta.benchmark_metrics);
  const externalEvaluation = asRecord(Object.keys(asRecord(record.external_evaluation)).length > 0 ? record.external_evaluation : scanMeta.external_evaluation);
  const qualityClaimStatus = asText(record.quality_claim_status) || asText(scanMeta.quality_claim_status) || asText(externalEvaluation.measurement_status) || 'NOT_MEASURED';
  const externalMeasured = qualityClaimStatus === 'MEASURED' && asText(externalEvaluation.measurement_status) === 'MEASURED';
  const externalDisplay = asRecord(externalEvaluation.display);
  const qualitySuppressed = !externalMeasured || Boolean(externalDisplay.suppress_quality_score) || Boolean(benchmarkMetrics.commercial_quality_suppressed);
  const benchmarkActive = Boolean(benchmarkMetrics.benchmark_active) && !qualitySuppressed;
  const benchmarkFailed = asText(benchmarkMetrics.status) === 'FAILED_SAFE';
  const gatePatch = getGatePatchStatus(record);
  const gatePatchEnabled = Boolean(gatePatch.core_gate_direct);
  const mainChainContract = getMainChainContract(record);
  const mainChainSummary = getMainChainSummary(record, mainChainContract);
  const mainChainStages = getMainChainStages(mainChainContract);
  const hasMainChainContract = Object.keys(mainChainContract).length > 0 || Object.keys(mainChainSummary).length > 0;
  const mainChainReady = Boolean(mainChainSummary.chain_ready);
  const firstBlockedStage = asText(mainChainSummary.first_blocked_stage);
  const firstBlockedStageLabel = firstBlockedStage ? (MAIN_CHAIN_STAGE_LABELS[firstBlockedStage] || firstBlockedStage) : '暂无';
  const firstBlockedNextAction = asText(mainChainSummary.first_blocked_next_action) || '等待上报。';
  const evidenceNormalizationSummary = getEvidenceNormalizationSummary(record);
  const evidenceNormalizationReport = getEvidenceNormalizationReport(record);
  const evidenceNormalizationItemReports = evidenceNormalizationItems(evidenceNormalizationReport);
  const blockedEvidenceActionItems = evidenceNormalizationItemReports.filter((i) => i.normalized !== true);
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
  const p0Count = findings.filter((f) => f.severity === 'P0').length;
  const highPriorityCount = p0Count + findings.filter((f) => f.severity === 'P1').length;
  const currentScanDefects = asNum(formalCounts.formal_customer_deliverable_count, totalRiskCount);
  const familyShelfDefects = currentScanDefects;
  const currentScanP0Count = Math.min(p0Count, currentScanDefects);
  const currentScanHighPriorityCount = Math.min(highPriorityCount, currentScanDefects);
  const coverageGaps = Array.isArray(record.coverage_gaps) ? record.coverage_gaps.length : 0;
  const discoveryFunnel = asRecord(record.discovery_funnel);
  const pipelineHealth = asRecord(Object.keys(asRecord(record.pipeline_health)).length > 0 ? record.pipeline_health : Object.keys(asRecord(discoveryFunnel.pipeline_health)).length > 0 ? discoveryFunnel.pipeline_health : scanMeta.pipeline_health);
  const pipelineHealthStatus = asText(pipelineHealth.status) || asText(scanMeta.pipeline_health_status);
  const pipelineFailedSafe = pipelineHealthStatus === 'FAILED_SAFE';
  const pipelineBlocked = pipelineHealthStatus === 'BLOCKED';
  const pipelineUnhealthy = pipelineFailedSafe || pipelineBlocked;
  const funnelStages = Array.isArray(discoveryFunnel.stages) ? discoveryFunnel.stages.filter((i): i is JsonRecord => i !== null && typeof i === 'object' && !Array.isArray(i)) : [];
  const funnelBlockers = Array.isArray(discoveryFunnel.top_blocking_reasons) ? discoveryFunnel.top_blocking_reasons.filter((i): i is JsonRecord => i !== null && typeof i === 'object' && !Array.isArray(i)) : [];
  const hasDiscoveryFunnel = funnelStages.length > 0 || Boolean(asText(discoveryFunnel.explanation));
  const funnelValidated = asNum(discoveryFunnel.validated_bug_count);
  const funnelPending = asNum(discoveryFunnel.pending_finding_count);
  const funnelCandidates = asNum(discoveryFunnel.candidate_count);
  const FUNNEL_STAGE_LABELS: Record<string, string> = { candidate_generation: '候选生成', probe_selection: '探针入选', execution: '执行', verification: '验证', formal_accounting: '正式记账' };
  const governanceNeedsAction = campaignStatus === 'blocked' || campaignStatus === 'coverage_deferred' || pipelineUnhealthy;
  const clueCount = clues.length;
  const evidenceTrust = asNum(valueMetrics.evidence_trust_score, 0);
  const modules = Array.from(new Set(findings.map(getFindingModule).filter(Boolean)));
  const modulesCount = modules.length;
  const regressionCovered = asNum(regressionSummary.covered_defect_count);
  const regressionFailed = asNum(regressionSummary.failed_defect_count);
  const regressionPending = asNum(regressionSummary.pending_defect_count);
  const regressionPassed = asNum(regressionSummary.passed_defect_count);
  const regressionRunAt = asText(regressionSummary.latest_run?.generated_at);
  const regressionGate = regressionGateLabel(asText(regressionSummary.latest_run?.gate_status));
  const regressionHasLinkedDefects = regressionCovered > 0 || regressionPassed > 0 || regressionFailed > 0 || regressionPending > 0;
  const regressionGateDisplay = regressionHasLinkedDefects ? regressionGate : '回归待观察';
  const regressionTrend = regressionTrendLabel(asText(regressionSummary.trend_direction));
  const regressionHistoryRunCount = asNum(regressionSummary.history_run_count);
  const regressionValidationSummary = asRecord(regressionSummary.validation_summary);
  const regressionDoubleRunVerified = Boolean(regressionValidationSummary.double_run_verified);
  const releaseRecommendation = releaseRecommendationLabel(asText(regressionSummary.release_recommendation), asText(regressionSummary.release_recommendation_label));
  const releaseRecommendationReason = asText(regressionSummary.release_recommendation_reason);
  const customerDeliveryReadiness = asText(regressionSummary.customer_delivery_readiness_label) || asText(regressionSummary.customer_delivery_readiness) || '持续观察中';
  const hasMaterializedMetrics = totalRiskCount > 0 || clueCount > 0 || asNum(asRecord(record.business_flow_summary).total, 0) > 0 || Boolean(campaignStatus) || coverageGaps > 0 || hasMainChainContract || hasEvidenceNormalizationSummary;
  const topFindings = [...findings].sort((a, b) => { const sg = getSeverityWeight(b.severity) - getSeverityWeight(a.severity); return sg !== 0 ? sg : (b.evidence_quality?.score || 0) - (a.evidence_quality?.score || 0); }).slice(0, 3);
  const focusFindings = currentScanDefects > 0 ? topFindings : [];
  const executiveHeadline = getExecutiveHeadline(currentScanDefects, familyShelfDefects, currentScanP0Count, clueCount, campaignStatus, campaignDeferredReason);
  const conclusion = pipelineFailedSafe ? '检测异常（非"无问题"）' : pipelineBlocked ? '检测执行被阻断' : campaignStatus === 'blocked' ? '检测暂停' : campaignStatus === 'coverage_deferred' ? '部分范围待后续检测' : currentScanP0Count > 0 ? '存在阻断发布问题' : currentScanDefects > 0 ? '建议进入整改验收' : '当前未发现阻断性问题';
  const level = riskLevel(conclusion);
  const decision = releaseDecision(currentScanP0Count, currentScanDefects, pipelineUnhealthy, campaignStatus === 'blocked');
  const aiTestPoints = asNum(valueMetrics.ai_equivalent_test_points, asNum(asRecord(record.business_flow_summary).total, 0));
  const savedHours = Math.round(aiTestPoints * 0.5);
  const hasValueData = Boolean(valueMetrics.executive_message || aiTestPoints > 0 || evidenceTrust > 0);

  if (!hasMaterializedMetrics) {
    return (<div><div className="page-header"><div><h1>{asText(record.project_name) || project} · 价值总览</h1><p>当前项目还没有形成真实检测数据。</p></div></div>
      <section className="empty-value-promise"><h2>运行首次检测后，您将看到：</h2><div className="empty-value-grid"><div className="empty-value-card"><strong>已确认问题清单</strong><span>每个问题都有原始证据和复现路径</span></div><div className="empty-value-card"><strong>发布安全建议</strong><span>基于真实检测结果的发布决策参考</span></div><div className="empty-value-card"><strong>价值量化报告</strong><span>AI 检测节省的人力成本和时间</span></div><div className="empty-value-card"><strong>覆盖度分析</strong><span>业务模块覆盖和风险分布全景</span></div></div><button className="btn btn-primary" onClick={() => navigateToProjectPath('/campaigns', project)}>启动首次检测</button></section></div>);
  }

  return (
    <div className="customer-results-page">
      <section className="executive-hero mb-4">
        <div className="executive-hero-conclusion">
          <span className="executive-eyebrow">{asText(record.project_name) || project}</span>
          <div className="executive-conclusion-row"><RiskRing level={level} /><div><h1>{conclusion}</h1><p>{executiveHeadline}</p></div></div>
          <div className="executive-meta"><span>最近检测 {formatScanTime(asText(scanMeta.last_scan_at) || asText(record.updated_at))}</span><span>耗时 {formatDurationMs(asNum(scanMeta.total_ms))}</span><span>结论可靠度 {evidenceTrust > 0 ? `${evidenceTrust}%` : '待评估'}</span></div>
        </div>
        <div className="executive-hero-metrics">
          <div className="executive-metric"><AnimatedCounter value={currentScanDefects} className="executive-metric-value" /><span>已确认问题</span></div>
          <div className="executive-metric"><AnimatedCounter value={currentScanP0Count} className="executive-metric-value danger" /><span>阻断发布</span></div>
          <div className="executive-metric"><AnimatedCounter value={aiTestPoints} className="executive-metric-value" /><span>等效测试点</span></div>
          <div className="executive-metric"><AnimatedCounter value={modulesCount} className="executive-metric-value" /><span>覆盖模块</span></div>
        </div>
        <div className="executive-hero-decision"><ReleaseLight color={decision.color} label={decision.label} advice={decision.advice} /></div>
      </section>

      {hasValueData && <ValueDashboard value={{ executive_message: asText(valueMetrics.executive_message) || `AI 已完成 ${aiTestPoints} 个等效测试点的验证，相当于人工团队约 ${savedHours} 小时的工作量。`, ai_equivalent_test_points: aiTestPoints, evidence_trust_score: evidenceTrust, explored_behavior_paths: asNum(valueMetrics.explored_behavior_paths, modulesCount), blocked_risk_count: currentScanP0Count + currentScanHighPriorityCount, capability_families: asNum(valueMetrics.capability_families, 0), bug_families: asNum(valueMetrics.bug_families, 0), decision_cards: [{ role: 'CTO / 技术VP', title: '发布决策', value: decision.label, detail: decision.advice }, { role: '测试经理', title: '效率提升', value: `节省 ${savedHours}h`, detail: `AI 30分钟完成人工团队约 ${Math.max(1, Math.round(savedHours / 8))} 天的验证工作` }, { role: '项目经理', title: '风险拦截', value: `${currentScanP0Count} 个 P0`, detail: currentScanP0Count > 0 ? '阻断性问题已拦截，避免流入生产环境' : '当前无阻断性问题' }] }} />}

      <div className="customer-summary-grid mb-4">
        {[
          { label: '已确认问题', val: currentScanDefects, tone: currentScanDefects > 0 ? 'primary' : 'neutral', note: currentScanDefects > 0 ? `累计 ${familyShelfDefects} 个` : '当前没有已确认问题' },
          { label: '阻断发布', val: currentScanP0Count, tone: currentScanP0Count > 0 ? 'danger' : 'neutral', note: currentScanP0Count > 0 ? '需要立即处理' : '当前无阻断项' },
          { label: '结论可靠度', val: evidenceTrust > 0 ? `${evidenceTrust}%` : '待评估', tone: evidenceTrust >= 80 ? 'success' : evidenceTrust > 0 ? 'warning' : 'neutral', note: modulesCount > 0 ? `已覆盖 ${modulesCount} 个业务模块` : '等待检测完成' },
          ...(regressionCovered > 0 ? [{ label: '回归验证', val: regressionGate, tone: regressionFailed > 0 ? 'danger' : regressionPending > 0 ? 'warning' : 'success', note: `已覆盖 ${regressionCovered} 个问题` }] : []),
          ...(releaseRecommendation ? [{ label: '发布建议', val: releaseRecommendation, tone: asText(regressionSummary.release_recommendation) === 'block_release' ? 'danger' : asText(regressionSummary.release_recommendation) === 'candidate_release' ? 'success' : 'warning', note: releaseRecommendationReason || customerDeliveryReadiness }] : []),
          ...(commercialAssets ? [{ label: '交付状态', val: deliveryPackageLabel(commercialAssets), tone: commercialAssets.delivery_package.status === 'created' ? 'success' : 'warning', note: trackerSyncLabel(commercialAssets) }] : []),
        ].map((item) => (<article key={item.label} className={`customer-summary-card tone-${item.tone}`}><span>{item.label}</span><strong>{item.val}</strong><small>{item.note}</small></article>))}
      </div>

      <section className="customer-focus-section mb-4">
        <div className="customer-section-head"><div><span className="panel-kicker">重点关注</span><h2>优先处理的问题</h2></div><button className="btn btn-secondary" onClick={() => navigateToProjectPath('/findings', project)}>查看完整清单</button></div>
        {focusFindings.length === 0 ? (
          <section className="findings-empty-state compact"><span className="findings-empty-kicker">当前结论</span><h3>{governanceNeedsAction ? campaignStatusLabel(campaignStatus) : '当前没有需要优先处理的问题'}</h3><p>{governanceNeedsAction ? campaignDetail(campaignStatus, campaignDeferredReason, nextCampaignReason) : clueCount > 0 ? `本轮仅有 ${clueCount} 条内部线索仍在补证。` : '当前没有已确认问题。'}</p></section>
        ) : (
          <div className="customer-focus-list">{focusFindings.map((f) => (
            <article key={f.id} className={`customer-focus-card severity-${f.severity.toLowerCase()}`}>
              <div className="customer-focus-head"><span className={`severity-dot ${f.severity.toLowerCase()}`} /><span className={`severity ${f.severity.toLowerCase()}`}>{f.severity}</span><strong>{f.title}</strong></div>
              <p>{f.business_summary || f.business_impact?.summary || f.actual || '该问题已形成确认结论。'}</p>
              <div className="customer-focus-meta"><span><em>影响模块</em><b>{getFindingModule(f)}</b></span><span><em>证据状态</em><b>{f.evidence_quality?.label || '已归档'}</b></span><span><em>复现稳定性</em><b>{f.proof?.repro_rate ?? 0}%</b></span></div>
            </article>
          ))}</div>
        )}
      </section>

      <section className="customer-actions-bar mb-4">
        <button className="btn btn-primary" onClick={() => navigateToProjectPath('/findings', project)}>查看问题清单</button>
        <button className="btn btn-secondary" onClick={() => navigateToProjectPath('/evidence', project)}>查看证据链</button>
        <button className="btn btn-secondary" onClick={handleExport}>导出价值报告</button>
        {(regressionCovered > 0 || regressionSummary.suite_exists) && <><button className="btn btn-secondary" onClick={() => void handleRegressionRun('release')} disabled={regressionRunningMode !== ''}>{regressionRunningMode === 'release' ? 'Release 回归中' : '执行 Release 回归'}</button><button className="btn btn-secondary" onClick={() => void handleRegressionRun('smoke')} disabled={regressionRunningMode !== ''}>{regressionRunningMode === 'smoke' ? 'Smoke 回归中' : '执行 Smoke 回归'}</button></>}
      </section>

      <TechnicalDiagnostics>
        <section className="customer-secondary-grid">
          <article className="customer-secondary-card"><span className="customer-value-kicker">本轮检测说明</span><div className="customer-secondary-meta"><span><em>最近扫描</em><b>{formatScanTime(asText(scanMeta.last_scan_at) || asText(record.updated_at))}</b></span><span><em>本轮耗时</em><b>{formatDurationMs(asNum(scanMeta.total_ms))}</b></span><span><em>质量门</em><b>{gatePatchLabel(gatePatchEnabled)}</b></span><span><em>检测流程</em><b>{mainChainReadyLabel(mainChainReady, hasMainChainContract)}</b></span><span><em>证据标准化</em><b>{evidenceNormalizationLabel(evidenceNormalizationSummary)}</b></span></div></article>
          {campaignStatus && <article className="customer-secondary-card"><span className="customer-value-kicker">检测治理</span><h3>{campaignStatusLabel(campaignStatus)}</h3><p>{campaignDetail(campaignStatus, campaignDeferredReason, nextCampaignReason)}</p><div className="customer-secondary-meta"><span><em>范围</em><b>{campaignScope || '待登记'}</b></span><span><em>环境</em><b>{campaignEnvironment || '待登记'}</b></span><span><em>确认回执</em><b>{campaignConfirmed}/{campaignAttempted || 0}</b></span><span><em>本轮问题</em><b>{currentScanDefects} 条</b></span><span><em>覆盖缺口</em><b>{coverageGaps}</b></span></div></article>}
          <article className="customer-secondary-card"><span className="customer-value-kicker">检测流程状态</span><h3>{mainChainReadyLabel(mainChainReady, hasMainChainContract)}</h3><p>{hasMainChainContract ? (mainChainReady ? '全部阶段已完成。' : `第一断点：${firstBlockedStageLabel}。下一步：${firstBlockedNextAction}`) : '尚未返回检测流程数据。'}</p><div className="customer-secondary-meta">{mainChainStages.length > 0 ? mainChainStages.map((s) => <span key={`${asText(s.stage)}-${asText(s.status)}`}><em>{mainChainStageLabel(s)}</em><b>{mainChainStatusLabel(s)}</b></span>) : <><span><em>通过</em><b>{asNum(mainChainSummary.passed_stage_count)}</b></span><span><em>部分</em><b>{asNum(mainChainSummary.partial_stage_count)}</b></span><span><em>缺失</em><b>{asNum(mainChainSummary.missing_stage_count)}</b></span></>}</div></article>
          {hasEvidenceNormalizationSummary && <article className="customer-secondary-card"><span className="customer-value-kicker">证据标准化</span><h3>{evidenceNormalizationLabel(evidenceNormalizationSummary)}</h3><p>{evidenceMissingFields.length > 0 ? `仍缺字段：${evidenceMissingFields.map(([f, c]) => `${evidenceMissingFieldLabel(f)}×${c}`).join('、')}。` : `已标准化：${evidenceFullyNormalizedCount} 项。`}</p><div className="customer-secondary-meta"><span><em>已标准化</em><b>{evidenceFullyNormalizedCount}</b></span><span><em>仍阻断</em><b>{evidenceBlockedItemCount}</b></span></div>{blockedEvidenceActionItems.length > 0 && <div className="customer-secondary-meta">{blockedEvidenceActionItems.slice(0, 3).map((i) => <span key={`${evidenceItemTitle(i)}-${asText(i.trace_id)}`}><em>{evidenceItemTitle(i)}</em><b>{evidenceItemAction(i)}</b></span>)}</div>}</article>}
          {pipelineUnhealthy && <article className="customer-secondary-card"><span className="customer-value-kicker">检测链路健康</span><h3>{pipelineFailedSafe ? '检测异常：空结果 ≠ 无问题' : '执行被阻断'}</h3><p>{asText(pipelineHealth.operator_note) || '检测链路存在问题。'}</p><div className="customer-secondary-meta"><span><em>链路状态</em><b>{pipelineHealthStatus || '未知'}</b></span><span><em>执行状态</em><b>{asText(pipelineHealth.execution_status) || '—'}</b></span></div></article>}
          {hasDiscoveryFunnel && <article className="customer-secondary-card"><span className="customer-value-kicker">发现漏斗</span><h3>已验证 {funnelValidated} · 待确认 {funnelPending} · 候选 {funnelCandidates}</h3><p>{asText(discoveryFunnel.explanation) || '本轮漏斗已生成。'}</p><div className="customer-secondary-meta">{funnelStages.map((s) => { const name = asText(s.name); const label = FUNNEL_STAGE_LABELS[name] || name; const input = asNum(s.input); const output = asNum(s.output); const conv = asNum(s.conversion); const pct = conv > 0 ? `${Math.round(conv * 100)}%` : (input > 0 ? '0%' : '—'); return <span key={name || label}><em>{label}</em><b>{output}/{input}（{pct}）</b></span>; })}</div>{funnelBlockers.length > 0 && <div className="customer-secondary-meta">{funnelBlockers.slice(0, 5).map((i) => <span key={`${asText(i.reason)}-${asNum(i.count)}`}><em>阻断 {asText(i.reason) || '未知'}</em><b>{asNum(i.count)}</b></span>)}</div>}</article>}
          {qualitySuppressed && <article className="customer-secondary-card"><span className="customer-value-kicker">外部质量评测</span><h3>{asText(externalDisplay.quality_label) || '尚未完成外部质量评测'}</h3><p>商业召回率/精度仅来自外部评测。当前状态为 {qualityClaimStatus}。</p></article>}
          {benchmarkActive && !benchmarkFailed && <article className="customer-secondary-card"><span className="customer-value-kicker">检测能力 Benchmark</span><h3>召回率 {Math.round(asNum(benchmarkMetrics.recall) * 100)}% · 精度 {Math.round(asNum(benchmarkMetrics.precision) * 100)}%</h3><div className="customer-secondary-meta"><span><em>F1</em><b>{Math.round(asNum(benchmarkMetrics.f1_score) * 100)}%</b></span><span><em>误报率</em><b>{Math.round(asNum(benchmarkMetrics.false_positive_rate) * 100)}%</b></span><span><em>GT Bug</em><b>{asNum(benchmarkMetrics.ground_truth_bug_count)}</b></span><span><em>检出</em><b>{asNum(benchmarkMetrics.true_positives)}</b></span></div></article>}
          {commercialAssets && <article className="customer-secondary-card"><span className="customer-value-kicker">商业交付资产</span><h3>{commercialHandoffLabel(commercialAssets)}</h3><div className="customer-secondary-meta"><span><em>交付包</em><b>{deliveryPackageLabel(commercialAssets)}</b></span><span><em>Tracker</em><b>{trackerSyncLabel(commercialAssets)}</b></span><span><em>客户复验</em><b>{commercialAssets.customer_ready_reproduction_count}</b></span></div></article>}
          {(regressionCovered > 0 || regressionSummary.suite_exists) && <article className="customer-secondary-card"><span className="customer-value-kicker">回归验证</span><h3>{regressionGateDisplay}</h3><div className="customer-secondary-meta"><span><em>已覆盖</em><b>{regressionCovered}</b></span><span><em>通过</em><b>{regressionPassed}</b></span><span><em>失败</em><b>{regressionFailed}</b></span><span><em>待执行</em><b>{regressionPending}</b></span><span><em>最近回归</em><b>{regressionRunAt ? formatScanTime(regressionRunAt) : '暂无'}</b></span></div></article>}
          {(regressionHistoryRunCount > 0) && <article className="customer-secondary-card"><span className="customer-value-kicker">回归趋势</span><h3>{regressionTrend}</h3><div className="customer-secondary-meta"><span><em>历史轮次</em><b>{regressionHistoryRunCount}</b></span><span><em>趋势</em><b>{regressionTrend}</b></span><span><em>发布建议</em><b>{releaseRecommendation}</b></span><span><em>双轮验真</em><b>{regressionDoubleRunVerified ? '已满足' : '未满足'}</b></span></div></article>}
          {clueCount > 0 && <article className="customer-secondary-card"><span className="customer-value-kicker">内部待跟进</span><h3>{clueCount} 条线索仍在补证</h3><p>只供内部运营使用，不进入客户问题交付。</p><button className="btn btn-secondary" onClick={() => navigateToProjectPath('/clues', project)}>进入内部工作台</button></article>}
        </section>
      </TechnicalDiagnostics>
    </div>
  );
}

export default Dashboard;
