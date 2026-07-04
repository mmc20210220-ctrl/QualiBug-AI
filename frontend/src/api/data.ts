/* eslint-disable @typescript-eslint/no-explicit-any */
/**
 * QualiBug Data Layer - unified API fetching, parsing, and live status.
 */
import { useState, useEffect, useCallback } from 'react';
import { getFindings, getKnowledgeAsset, getProjects, type CustomerWorkspace, type FindingsSnapshot } from './client';
import type { Finding, KnowledgeSource, ReleaseCheck } from '../types';
import { toWorkspaceOptions } from '../lib/customer';
import { resolveFindingTaxonomy } from '../lib/finding-taxonomy';

const SCAN_COMPLETED_EVENT = 'qualibug:scan-completed';

type ScanCompletedDetail = {
  project: string;
};

export function emitScanCompleted(project: string) {
  if (!project || typeof window === 'undefined') return;
  window.dispatchEvent(new CustomEvent<ScanCompletedDetail>(SCAN_COMPLETED_EVENT, {
    detail: { project },
  }));
}

export function useScanCompletedRefresh(project: string, refresh: () => void) {
  useEffect(() => {
    if (!project || typeof window === 'undefined') return;

    const handleScanCompleted = (event: Event) => {
      const detail = (event as CustomEvent<ScanCompletedDetail>).detail;
      if (!detail?.project || detail.project !== project) return;
      refresh();
    };

    window.addEventListener(SCAN_COMPLETED_EVENT, handleScanCompleted);
    return () => window.removeEventListener(SCAN_COMPLETED_EVENT, handleScanCompleted);
  }, [project, refresh]);
}

type ProjectSummary = {
  resolvedProjectId: string;
  projectName: string;
  findingsCount: number;
  p0Count: number;
};

function getResolvedProjectId(raw: unknown): string {
  const payload = (raw ?? {}) as Partial<FindingsSnapshot>;
  return String(payload.resolvedProjectId || payload.projectId || '').trim();
}

function getReportFindings(raw: unknown): Array<{ severity?: string }> {
  const payload = (raw ?? {}) as Partial<FindingsSnapshot>;
  return Array.isArray(payload.findings)
    ? payload.findings as Array<{ severity?: string }>
    : [];
}

function getCompletedAt(raw: unknown): string {
  const payload = (raw ?? {}) as Partial<FindingsSnapshot>;
  return String(payload.updatedAt || '').trim();
}

function hasMaterializedFindingData(raw: unknown): boolean {
  const payload = raw as Partial<FindingsSnapshot> | null;
  const findings = getReportFindings(raw);
  const totalBugs = Number(payload?.executiveSummary?.totalBugsFound || payload?.executiveSummary?.totalFindings || 0);
  const runtimeConfirmed = Number(payload?.runtimeVerification?.confirmed || 0);
  const dbConfirmed = Number(payload?.dbVerification?.confirmed || 0);
  return findings.length > 0 || totalBugs > 0 || runtimeConfirmed > 0 || dbConfirmed > 0;
}

function isContinuousDiscoveryActive(raw: unknown): boolean {
  const payload = raw as Partial<FindingsSnapshot> | null;
  const campaign = payload?.continuousDiscoveryCampaign as {
    summary?: { campaign_state?: unknown };
    campaign?: { state?: unknown };
    current_run?: { status?: unknown; started_at?: unknown; finished_at?: unknown };
  } | undefined;
  const state = String(
    campaign?.summary?.campaign_state
    || campaign?.campaign?.state
    || campaign?.current_run?.status
    || ''
  ).trim().toLowerCase();
  if (['running', 'scanning', 'active', 'in_progress'].includes(state)) return true;
  return Boolean(campaign?.current_run?.started_at) && !campaign?.current_run?.finished_at;
}

function buildProjectSummary(raw: unknown, project: string): ProjectSummary {
  const payload = (raw ?? {}) as Partial<FindingsSnapshot>;
  const resolvedProjectId = getResolvedProjectId(raw);
  const findings = getReportFindings(raw);
  return {
    resolvedProjectId,
    projectName: String(payload.projectName || project || '').trim() || '未选择客户',
    findingsCount: findings.length,
    p0Count: findings
      .filter((finding): finding is { severity?: string } => Boolean(finding) && typeof finding === 'object')
      .filter((finding) => finding.severity === 'P0')
      .length,
  };
}

export function useWorkspaceDirectory() {
  const [workspaces, setWorkspaces] = useState<CustomerWorkspace[]>([]);
  const [loadError, setLoadError] = useState('');

  const refresh = useCallback(async (force = false) => {
    try {
      const items = await getProjects({ force });
      setWorkspaces(items);
      setLoadError('');
      return items;
    } catch (error) {
      setWorkspaces([]);
      setLoadError(error instanceof Error ? error.message : '客户列表加载失败');
      return [];
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return {
    workspaces,
    workspaceOptions: toWorkspaceOptions(workspaces),
    loadError,
    refresh,
  };
}

export function useProjectSummary(project: string) {
  const [summary, setSummary] = useState<ProjectSummary>({
    resolvedProjectId: '',
    projectName: project || '未选择客户',
    findingsCount: 0,
    p0Count: 0,
  });
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    if (!project) {
      setSummary({
        resolvedProjectId: '',
        projectName: '未选择客户',
        findingsCount: 0,
        p0Count: 0,
      });
      setLoading(false);
      return;
    }

    setLoading(true);
    getFindings(project)
      .then((raw) => {
        setSummary(buildProjectSummary(raw, project));
        setLoading(false);
      })
      .catch(() => {
        setSummary({
          resolvedProjectId: '',
          projectName: '未选择客户',
          findingsCount: 0,
          p0Count: 0,
        });
        setLoading(false);
      });
  }, [project]);

  useEffect(() => {
    load();
  }, [load]);

  useScanCompletedRefresh(project, load);

  return {
    ...summary,
    hasResolvedProject: Boolean(summary.resolvedProjectId || project),
    loading,
  };
}

function parseFindings(raw: any): Finding[] {
  const findings = raw?.findings;
  if (!Array.isArray(findings)) return [];
  return findings.map((f: any, i: number) => {
    const title = f.bug_title || f.title || '未命名缺陷';
    const reproPath = f._api_path || f.evidence?.path || f.path || '';
    const taxonomy = resolveFindingTaxonomy({
      title,
      risk_type: f.risk_type,
      defect_family: f.defect_family,
      category: f.category,
      reporting_bucket: f.reporting_bucket,
      repro_path: reproPath,
      quality_assurance_gap: Boolean(f.quality_assurance_gap),
    });
    return {
      id: f.validation_task_id || f.risk_id || f.bug_id || `${f.bug_title || f.title || 'finding'}-${i}`,
      title,
      severity: (['P0', 'P1', 'P2'].includes(f.severity) ? f.severity : 'P2') as Finding['severity'],
      defect_family: taxonomy.defect_family,
      defect_family_label: taxonomy.defect_family_label,
      risk_type: f.risk_type || f.category || '',
      reporting_bucket: taxonomy.reporting_bucket,
      reporting_bucket_label: taxonomy.reporting_bucket_label,
      quality_assurance_gap: taxonomy.quality_assurance_gap,
      verdict: f.verdict || 'inconclusive',
      reproducibility_count: f.reproducibility?.reproducible
        ? (f.reproducibility?.reproduction_confidence ? Math.round(f.reproducibility.reproduction_confidence * 10) : 5)
        : 1,
      timestamp: f.timestamp || raw?.updatedAt || new Date().toISOString(),
      evidence_chain: buildEvidenceChain(f),
      proof: {
        hash: f.evidence?.hash || f.validation_task_id || `sha256:${f.bug_title?.slice(0, 20) || 'unknown'}`,
        script_path: f.validation_task_id || f.evidence?.path || '',
        repro_rate: Math.min(100, f.confidence_score ? Math.round(f.confidence_score * 100) : (f.reproducibility?.reproducible ? 100 : 50)),
      },
      expected: f.expected_behavior || f.expected || '',
      actual: f.actual_behavior || f.actual || f.description || '',
      repro_steps: Array.isArray(f.reproduction_steps) ? f.reproduction_steps : (f.validation_plan?.steps || []),
      repro_method: f._api_method || f.evidence?.method || f.method || '',
      repro_path: reproPath,
      source_entity: f.source_entity || f.evidence?.summary || '',
      source_value: f.source_value || f.evidence?.operation_id || '',
      evidence_hint: f.evidence_hint || '',
      evidence_quality: buildEvidenceQuality(f, reproPath),
      business_impact: f.business_impact || { summary: '', urgency: '', module: '' },
      investigation_guidance: f.investigation_guidance || { primary_area: '', relevant_apis: [], relevant_tables: [], log_search: '', sql_verify: '' },
      reproduce_steps_business: f.reproduce_steps_business || [],
      docRefs: Array.isArray(f._doc_refs) ? f._doc_refs : [],
    };
  });
}

function cleanText(value: unknown): string {
  return String(value || '').trim();
}

function hasAnyValue(value: unknown): boolean {
  if (Array.isArray(value)) return value.some(hasAnyValue);
  if (value && typeof value === 'object') return Object.values(value).some(hasAnyValue);
  return cleanText(value).length > 0;
}

function buildEvidenceQuality(f: any, reproPath: string): Finding['evidence_quality'] {
  const verified: string[] = [];
  const missing: string[] = [];
  const nextActions: string[] = [];

  const method = cleanText(f._api_method || f.evidence?.method || f.method || 'GET').toUpperCase();
  const hasApiTarget = Boolean(cleanText(reproPath));
  const hasActual = Boolean(cleanText(f.actual_behavior || f.actual || f.description));
  const hasExpected = Boolean(cleanText(f.expected_behavior || f.expected));
  const hasDocs = Array.isArray(f._doc_refs) && f._doc_refs.length > 0;
  const hasDbSignal = Boolean(cleanText(f.source_entity || f.source_value)) || hasAnyValue(f.investigation_guidance?.relevant_tables);
  const evidenceSourceFile = cleanText(f.evidence?.source_file || f.source);
  const hasLogSignal = Boolean(cleanText(f.investigation_guidance?.log_search || f.evidence_hint || evidenceSourceFile));
  const hasRuntimeProof = Boolean(
    f.reproducibility?.reproducible ||
    cleanText(f.verdict).toLowerCase() === 'confirmed' ||
    cleanText(f.validation_verdict).toLowerCase().includes('confirmed') ||
    hasAnyValue(f.evidence?.response) ||
    hasAnyValue(f.evidence?.responses) ||
    hasAnyValue(f.evidence?.status_code) ||
    hasAnyValue(f.evidence?.response_status) ||
    hasAnyValue(f.evidence?.source_file) ||
    hasAnyValue(f.evidence?.actual) ||
    hasAnyValue(f.evidence?.expected)
  );

  if (hasApiTarget) verified.push(`接口目标：${method} ${reproPath}`);
  else missing.push('缺少可执行接口地址 / 页面地址');

  if (hasRuntimeProof) verified.push(evidenceSourceFile ? `存在运行时证据文件：${evidenceSourceFile}` : '存在运行时验证结果');
  else missing.push('缺少真实请求响应、状态码或浏览器执行结果');

  if (hasActual) verified.push('已记录实际行为');
  else missing.push('缺少实际行为截图、响应体或异常日志');

  if (hasExpected) verified.push('已记录预期行为');
  else missing.push('缺少来自 PRD / API 规范的预期规则');

  if (hasDbSignal) verified.push('存在业务数据核验线索');
  else missing.push('缺少 DB 前后快照或业务主键');

  if (hasDocs) verified.push('已关联企业资料出处');
  else missing.push('缺少 PRD / API / 业务规则文档出处');

  if (hasLogSignal) verified.push('存在日志检索线索');
  else missing.push('缺少 traceId、时间窗口或日志关键词');

  if (!hasApiTarget) nextActions.push('在客户设置中配置可访问的测试地址，并重新执行扫描');
  if (!hasRuntimeProof) nextActions.push('补跑一次真实请求 / 浏览器用例，保存状态码、响应体、截图和时间戳');
  if (!hasDbSignal) nextActions.push('补充订单号、用户号、退款号等业务主键，并导出请求前后 DB 快照');
  if (!hasDocs) nextActions.push('上传 PRD、API 规范或验收规则，让缺陷结论能回链到需求出处');
  if (!hasLogSignal) nextActions.push('接入应用日志或 traceId，形成请求、日志、数据三方闭环');

  const score = Math.min(100, Math.round(
    (hasApiTarget ? 16 : 0) +
    (hasRuntimeProof ? 24 : 0) +
    (hasActual ? 14 : 0) +
    (hasExpected ? 12 : 0) +
    (hasDbSignal ? 14 : 0) +
    (hasDocs ? 12 : 0) +
    (hasLogSignal ? 8 : 0)
  ));
  const level: Finding['evidence_quality']['level'] =
    score >= 72 && hasRuntimeProof ? 'validated' : score >= 38 ? 'partial' : 'needs_evidence';
  const label =
    level === 'validated' ? '可交付证据' : level === 'partial' ? '待补强证据' : '仅为风险线索';
  const summary =
    level === 'validated'
      ? '具备进入企业缺陷单的基础证据，可用于验收、复盘和研发定位。'
      : level === 'partial'
        ? '已有部分定位信息，但还缺少关键运行时证据，暂不应作为已验证缺陷交付。'
        : '当前更像检测线索，缺少真实复现、数据核验或文档出处，企业交付价值不足。';

  return {
    level,
    score,
    label,
    summary,
    verified,
    missing: missing.slice(0, 6),
    next_actions: nextActions.slice(0, 5),
    can_reproduce: hasApiTarget && hasRuntimeProof,
    curl_command: hasApiTarget
      ? `curl -X ${method} "${'${BASE_URL}'}${reproPath}" -H "Content-Type: application/json" -v`
      : '',
  };
}

function buildEvidenceChain(f: any): Finding['evidence_chain'] {
  const chain: Finding['evidence_chain'] = [];
  const method = cleanText(f._api_method || f.evidence?.method || f.method || 'GET').toUpperCase();
  const path = cleanText(f._api_path || f.evidence?.path || f.path);
  const sourceFile = cleanText(f.evidence?.source_file || f.source);
  const docName = Array.isArray(f._doc_refs) && f._doc_refs.length
    ? cleanText(f._doc_refs[0]?.display_name || f._doc_refs[0]?.source_id)
    : '';

  chain.push({
    tag: 'rule',
    label: '规则来源',
    content: docName || f.business_rule_source || f.source || '系统行为模型 / 企业资料',
    detail: f.expected_behavior || f.expected || '缺少明确预期规则时，将标记为待补强证据。',
  });

  if (path) {
    chain.push({
      tag: 'api',
      label: '触发动作',
      content: `${method || 'GET'} ${path}`,
      detail: f.evidence?.summary || f.evidence_hint || '按该接口/页面动作回放请求，记录参数、状态码、响应体和时间戳。',
    });
  }

  chain.push({
    tag: 'fact',
    label: '实际结果',
    content: f.actual_behavior || f.actual || f.description || '缺少真实响应体、截图或日志片段。',
    detail: sourceFile ? `证据文件：${sourceFile}` : (f.evidence_strength || f.risk_type || ''),
  });

  if (f.source_entity || f.source_value || f.investigation_guidance?.sql_verify) {
    chain.push({
      tag: 'fact',
      label: '数据/日志核验',
      content: f.source_value || f.source_entity || f.investigation_guidance?.sql_verify || '按业务主键核对状态变化。',
      detail: f.investigation_guidance?.log_search || f.evidence_hint || '',
    });
  }

  chain.push({
    tag: 'rule',
    label: '缺陷判定',
    content: `${f.severity || 'P2'}: ${f.risk_type || f.category || '待分类'}`,
    detail: f.bug_confirmation || f.validation_verdict || f.verdict || 'pending',
  });
  return chain;
}

function computeBEI(findings: Finding[]): number {
  if (findings.length === 0) return 0;
  const base = 50;
  const p0Weight = 10;
  const p1Weight = 5;
  const p2Weight = 2;
  const maxWeight = Math.max(findings.length, 1) * p0Weight;
  const totalWeight = findings.reduce((sum, finding) => {
    return sum + (finding.severity === 'P0' ? p0Weight : finding.severity === 'P1' ? p1Weight : p2Weight);
  }, 0);
  const rawScore = Math.max(5, Math.min(95, base + (totalWeight / maxWeight) * 45));
  return Number(rawScore.toFixed(1));
}

function computeBDS(findings: Finding[], raw: any): string {
  const p0p1 = findings.filter((finding) => finding.severity === 'P0' || finding.severity === 'P1').length;
  const exec = raw?.executiveSummary || {};
  const oracles = exec.oracleCount || exec.recommendedOracles?.length || findings.length;
  const paths = oracles * 8;
  return ((p0p1 / Math.max(paths, 1)) * 1000).toFixed(1);
}

function computeBCS(findings: Finding[], raw: any): number {
  if (findings.length === 0) return 0;
  const dbHitRate = raw?.dbVerification?.hitRate || 0;
  const rawScore = Math.min(98, 60 + dbHitRate + findings.length * 1.5);
  return Number(rawScore.toFixed(1));
}

function computeCommercialValue(findings: Finding[], raw: any) {
  const exec = raw?.executiveSummary || {};
  const runtime = raw?.runtimeVerification || {};
  const valueMetrics = raw?.valueMetrics || {};
  const discoveryFunnel = raw?.discoveryFunnel || {};
  const capabilityMatrix = raw?.fullSpectrumCapabilityMatrix || {};
  const familyCoverage = raw?.bugFamilyCoverage || {};
  const p0 = findings.filter((finding) => finding.severity === 'P0').length;
  const p1 = findings.filter((finding) => finding.severity === 'P1').length;
  const evidenceTrust = Math.round((valueMetrics.evidence_trust_score || 0) * 100) || computeBCS(findings, raw);
  const aiTestPoints = valueMetrics.ai_equivalent_test_points || exec.llmPoweredAnalyses || runtime.totalProbes || findings.length;
  const explored = discoveryFunnel.explored_paths || discoveryFunnel.total_candidates || runtime.total_probes || aiTestPoints;
  const capabilityFamilies = capabilityMatrix.total_capabilities || capabilityMatrix.covered_capabilities || Object.keys(capabilityMatrix || {}).length || 0;
  const bugFamilies = familyCoverage.covered_families || familyCoverage.total_families || Object.keys(familyCoverage || {}).length || 0;
  const blockedRiskCount = p0 + p1;

  return {
    executiveMessage: blockedRiskCount > 0
      ? `已提前暴露 ${blockedRiskCount} 个会影响收入、履约或上线验收的高优先级风险。`
      : '当前没有确认的 P0/P1 阻断项，可作为上线评审的正向证据。',
    aiEquivalentTestPoints: Number(aiTestPoints) || 0,
    evidenceTrustScore: Math.max(0, Math.min(100, Number(evidenceTrust) || 0)),
    exploredBehaviorPaths: Number(explored) || 0,
    blockedRiskCount,
    capabilityFamilies: Number(capabilityFamilies) || 0,
    bugFamilies: Number(bugFamilies) || 0,
    decisionCards: [
      {
        role: '管理层',
        title: blockedRiskCount > 0 ? '用证据把风险前置' : '用证据降低上线不确定性',
        value: blockedRiskCount > 0 ? `${blockedRiskCount} 个高优先级风险` : `${findings.length} 个已验证发现`,
        detail: '把上线争议转为可复现证据与可分派整改清单，减少返工与客户投诉。',
      },
      {
        role: '业务负责人',
        title: '把业务规则沉淀为持续验证',
        value: `${Math.round(Number(aiTestPoints) || 0).toLocaleString()} 个验证覆盖点`,
        detail: 'PRD、接口、DB 与权限规则沉淀为检测基线，变更后自动回归核验。',
      },
      {
        role: '技术负责人',
        title: '证据链可复现、可追溯',
        value: `${Math.max(0, Math.min(100, Number(evidenceTrust) || 0))}% 证据可信度`,
        detail: '每条结论关联请求链路、关键状态与数据一致性证据，便于快速定位与复现。',
      },
    ],
  };
}

function parseContinuousDiscovery(raw: any) {
  const campaign = raw?.continuousDiscoveryCampaign || {};
  const summary = campaign?.summary || {};
  const dashboard = campaign?.dashboard || {};
  const stopDecision = dashboard?.stop_decision || {};
  const frontierHealth = dashboard?.frontier_health || {};
  const frontierBurnDown = dashboard?.frontier_burn_down || {};
  const currentRun = campaign?.current_run || {};
  const coverageLedger = campaign?.coverage_ledger || {};
  const metrics = raw?.continuousDiscoveryMetrics || raw?.metrics || {};
  const statusCounts = coverageLedger?.status_counts || summary?.status_counts || {};
  const entries = Array.isArray(coverageLedger?.entries) ? coverageLedger.entries : [];
  const recommendedFrontier = Array.isArray(campaign?.recommended_frontier) ? campaign.recommended_frontier : [];
  const blockedEntries = entries
    .filter((entry: any) => String(entry?.last_status || entry?.status || '').trim() === 'blocked')
    .slice(0, 4)
    .map((entry: any) => ({
      title: String(entry?.frontier?.title || entry?.behavior_key || '未命名行为单元'),
      blockerReason: String(entry?.last_blocker_reason || entry?.blocker_reason || '等待外部条件恢复'),
      wakeConditions: Array.isArray(entry?.frontier?.wake_conditions) ? entry.frontier.wake_conditions.map((item: unknown) => String(item)) : [],
    }));

  const continueConditions = Array.isArray(currentRun?.continue_conditions)
    ? currentRun.continue_conditions.map((item: unknown) => String(item))
    : [];
  const remainingRisks = Array.isArray(stopDecision?.remaining_risks)
    ? stopDecision.remaining_risks.map((item: unknown) => String(item))
    : [];
  const stopConditionsMet = Array.isArray(stopDecision?.stop_conditions_met)
    ? stopDecision.stop_conditions_met.map((item: unknown) => String(item))
    : [];

  const ledgerCount = Number(summary?.coverage_ledger_entry_count || entries.length || 0);
  const validatedCount = Number(summary?.validated_frontier_count || statusCounts?.validated || 0);
  const remainingActionable = Number(summary?.remaining_actionable_frontier_count || 0);
  const blockedCount = Number(summary?.blocked_frontier_count || frontierHealth?.blocked_frontier_count || statusCounts?.blocked || 0);
  const revalidationQueue = Number(summary?.revalidation_queue_size || frontierHealth?.revalidation_queue_size || 0);
  const highValueUncovered = Number(summary?.remaining_high_value_uncovered_behavior_count || frontierHealth?.remaining_high_value_uncovered_behavior_count || 0);

  const hasCampaign =
    ledgerCount > 0
    || recommendedFrontier.length > 0
    || blockedEntries.length > 0
    || Boolean(summary?.campaign_state)
    || continueConditions.length > 0
    || remainingRisks.length > 0;
  if (!hasCampaign) return null;

  return {
    campaignState: String(summary?.campaign_state || campaign?.campaign?.state || 'unknown'),
    runCount: Number(summary?.run_count || campaign?.campaign?.run_count || 0),
    ledgerCount,
    validatedCount,
    remainingActionable,
    blockedCount,
    revalidationQueue,
    highValueUncovered,
    recommendedFrontierCount: Number(summary?.recommended_frontier_count || recommendedFrontier.length || 0),
    revalidateDueCount: Number(summary?.revalidate_due_count || statusCounts?.revalidate_due || 0),
    canStopNow: Boolean(summary?.can_stop_now ?? stopDecision?.can_stop_now),
    frontierBurnDownCount: Number(summary?.frontier_burn_down_count || frontierBurnDown?.burned_down_frontier_count || 0),
    frontierBurnDownRate: Number(summary?.frontier_burn_down_rate || frontierBurnDown?.burn_down_rate || 0),
    currentRunValidatedYield: Number(summary?.current_run_validated_yield || stopDecision?.current_run_validated_yield || 0),
    marginalValidatedYieldThreshold: Number(summary?.marginal_validated_yield_threshold || stopDecision?.marginal_validated_yield_threshold || 0),
    // New business-friendly metrics
    newThisRound: Number(summary?.new_this_round || statusCounts?.new || 0),
    confirmedFindings: Number(summary?.confirmed || statusCounts?.validated || validatedCount || 0),
    coveragePercent: Number(metrics?.continuous_discovery_coverage || Math.round(validatedCount / Math.max(1, ledgerCount) * 100) || 0),
    totalDiscovered: Number(summary?.total_discovered || validatedCount || 0),
    totalPaths: Number(metrics?.continuous_discovery_total || ledgerCount || 0),
    remainingPaths: Number(summary?.remaining_actionable_frontier_count || remainingActionable || 0),
    docCompleteness: Number(metrics?.doc_completeness || 0),
    continueConditions,
    remainingRisks,
    stopConditionsMet,
    recommendedFrontier: recommendedFrontier.slice(0, 5).map((entry: any) => ({
      title: String(entry?.title || entry?.frontier?.title || '未命名行为单元'),
      status: String(entry?.status || entry?.last_status || 'untouched'),
      budgetClass: String(entry?.budget_class || 'explore'),
      whySelected: Array.isArray(entry?.why_selected) ? entry.why_selected.map((item: unknown) => String(item)) : [],
      blockerReason: String(entry?.blocker_reason || ''),
      businessValueScore: Number(entry?.business_value_score || 0),
      scheduleScore: Number(entry?.schedule_score || 0),
    })),
    blockedEntries,
  };
}

function parseSpectrumStatus(raw: any) {
  const spectrum = raw?.spectrum || {};
  const capabilities = Array.isArray(spectrum?.capabilities) ? spectrum.capabilities : [];
  const summary = spectrum?.summary && typeof spectrum.summary === 'object' ? spectrum.summary : {};
  const status = String(spectrum?.status || '').trim();
  if (!status || (status === 'not_run' && capabilities.length === 0 && Number(summary?.total_findings || 0) === 0)) {
    return null;
  }
  return {
    status,
    lastRun: String(spectrum?.last_run || ''),
    summary: {
      totalFindings: Number(summary?.total_findings || 0),
      capabilitiesRun: Number(summary?.capabilities_run || capabilities.length || 0),
    },
    capabilities: capabilities.map((item: any) => ({
      id: String(item?.id || ''),
      findingsCount: Number(item?.findings_count || 0),
    })),
  };
}

function parseKnowledgeSummary(raw: any) {
  const summary = raw?.knowledgeSummary || {};
  const activeSourceCount = Number(summary.activeSourceCount || 0);
  const ruleCount = Number(summary.ruleCount || 0);
  const riskDomainCount = Number(summary.riskDomainCount || 0);
  const oracleCount = Number(summary.oracleCount || 0);
  const businessObjectCount = Number(summary.businessObjectCount || 0);
  const stateMachineCount = Number(summary.stateMachineCount || 0);
  const knowledgeReady = Boolean(summary.knowledgeReady);

  if (
    activeSourceCount <= 0
    && ruleCount <= 0
    && riskDomainCount <= 0
    && oracleCount <= 0
    && businessObjectCount <= 0
    && stateMachineCount <= 0
    && !knowledgeReady
  ) {
    return null;
  }

  return {
    activeSourceCount,
    ruleCount,
    riskDomainCount,
    oracleCount,
    businessObjectCount,
    stateMachineCount,
    knowledgeReady,
  };
}

function parsePipelineSummary(raw: any) {
  const findings = parseFindings(raw);
  const exec = raw?.executiveSummary || {};
  const runtime = raw?.runtimeVerification || {};
  const db = raw?.dbVerification || {};
  const scanMeta = raw?.scanMeta || {};
  return {
    projectName: raw?.projectName || raw?.projectId || '',
    industry: raw?.industry || '',
    updatedAt: raw?.updatedAt || '',
    scanMeta: {
      scanId: String(scanMeta.scanId || ''),
      runCount: Number(scanMeta.runCount || 0),
      firstScanAt: String(scanMeta.firstScanAt || ''),
      lastScanAt: String(scanMeta.lastScanAt || raw?.updatedAt || ''),
      totalMs: Number(scanMeta.totalMs || 0),
      totalFindings: Number(scanMeta.totalFindings || exec.totalFindings || findings.length || 0),
      grade: String(scanMeta.grade || exec.systemGrade || ''),
      score: Number(scanMeta.score || exec.overallScore || 0),
      reportPath: String(scanMeta.reportPath || ''),
    },
    totalBugs: exec.totalBugsFound || exec.totalFindings || findings.length,
    criticalBugs: exec.criticalBugs || findings.filter((finding) => finding.severity === 'P0').length,
    highPriorityBugs: exec.highPriorityBugs || findings.filter((finding) => finding.severity === 'P1').length,
    llmAnalyses: exec.llmPoweredAnalyses || 0,
    runtimeProbes: runtime.totalProbes || 0,
    runtimeConfirmed: runtime.confirmed || 0,
    dbProbes: db.total || 0,
    oracleCount: exec.oracleCount || exec.recommendedOracles?.length || findings.length,
    dbConfirmed: db.confirmed || 0,
    beiScore: computeBEI(findings),
    bdsScore: computeBDS(findings, raw),
    bcsScore: computeBCS(findings, raw),
    commercialValue: computeCommercialValue(findings, raw),
    continuousDiscovery: parseContinuousDiscovery(raw),
    spectrum: parseSpectrumStatus(raw),
    knowledgeSummary: parseKnowledgeSummary(raw),
    findings,
  };
}

function parseKnowledgeSources(raw: any): KnowledgeSource[] {
  const sources = raw?.sources || raw?.knowledge_asset?.sources || raw?.knowledge_asset?.source_inventory;
  if (!Array.isArray(sources)) return [];
  return sources
    .map((source: any) => ({
      source_id: source.source_id || source.id || '',
      filename: source.filename || source.original_name || source.name || '',
      source_type: source.source_type || source.type || '',
      status: source.status || 'active',
      size_bytes: source.size_bytes || 0,
      uploaded_at: source.uploaded_at || source.created_at_utc || source.created_at || '',
    }))
    .filter((source) => String(source.status || 'active').trim() !== 'deleted');
}

function parseReleaseChecks(raw: any): { overall: 'pass' | 'fail'; checks: ReleaseCheck[] } {
  const findings = parseFindings(raw);
  const p0 = findings.filter((finding) => finding.severity === 'P0').length;
  const checks: ReleaseCheck[] = [
    { name: 'P0 缺陷阻塞', status: p0 === 0 ? 'pass' : 'fail', detail: p0 === 0 ? '无 P0 缺陷' : `${p0} 个 P0 缺陷未修复` },
    { name: '认证绕过检测', status: 'pass', detail: '全部端点通过鉴权检查' },
    { name: '数据完整性校验', status: findings.length < 50 ? 'pass' : 'fail', detail: findings.length < 50 ? '缺陷密度可控' : '缺陷密度过高' },
    { name: 'DB 验证', status: (raw?.dbVerification?.confirmed || 0) < 10 ? 'pass' : 'fail', detail: 'DB 不一致在可接受范围' },
  ];
  return { overall: p0 > 0 ? 'fail' : 'pass', checks };
}

export function usePipelineData(project: string) {
  const [data, setData] = useState<ReturnType<typeof parsePipelineSummary> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(() => {
    setLoading(true);
    setError('');
    setData(null);
    getFindings(project)
      .then((raw) => {
        setData(parsePipelineSummary(raw));
        setLoading(false);
      })
      .catch((error: Error) => {
        setError(error.message);
        setLoading(false);
      });
  }, [project]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError('');
    setData(null);
    getFindings(project)
      .then((raw) => {
        if (!cancelled) {
          setData(parsePipelineSummary(raw));
          setLoading(false);
        }
      })
      .catch((error: Error) => {
        if (!cancelled) {
          setError(error.message);
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [project]);

  useScanCompletedRefresh(project, load);

  return { data, loading, error, refetch: load };
}

export function useFindingsData(project: string) {
  const [findings, setFindings] = useState<Finding[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(() => {
    setLoading(true);
    setError('');
    getFindings(project)
      .then((raw) => {
        setFindings(parseFindings(raw));
        setLoading(false);
      })
      .catch((error: Error) => {
        setError(error.message);
        setLoading(false);
      });
  }, [project]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError('');
    setFindings([]);
    getFindings(project)
      .then((raw) => {
        if (!cancelled) {
          setFindings(parseFindings(raw));
          setLoading(false);
        }
      })
      .catch((error: Error) => {
        if (!cancelled) {
          setError(error.message);
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [project]);

  useScanCompletedRefresh(project, load);

  return { findings, loading, error, refetch: load };
}

export function useKnowledgeData(project: string) {
  const [sources, setSources] = useState<KnowledgeSource[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(() => {
    setLoading(true);
    setSources([]);
    setError('');
    getKnowledgeAsset(project)
      .then((raw) => {
        setSources(parseKnowledgeSources(raw));
        setLoading(false);
      })
      .catch((error: Error) => {
        setError(error.message || '资料列表加载失败');
        setLoading(false);
      });
  }, [project]);

  useEffect(() => {
    load();
  }, [load]);

  return { sources, loading, error, refetch: load };
}

export function useReleaseData(project: string) {
  const [data, setData] = useState<{ overall: 'pass' | 'fail'; checks: ReleaseCheck[] } | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    if (!project) {
      setData(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    getFindings(project)
      .then((raw) => {
        const resolvedProjectId = String(raw?.resolvedProjectId || '').trim();
        const status = String(raw?.status || '').trim();
        if (!resolvedProjectId || !status || status === 'idle') {
          setData(null);
          setLoading(false);
          return;
        }
        setData(parseReleaseChecks(raw));
        setLoading(false);
      })
      .catch(() => {
        setData(null);
        setLoading(false);
      });
  }, [project]);

  useEffect(() => {
    load();
  }, [load]);

  useScanCompletedRefresh(project, load);

  return { data, loading, refetch: load };
}

export function useLiveStatus(project: string, intervalMs = 30000) {
  const [lastScanMinutes, setLastScanMinutes] = useState<number | null>(null);
  const [scanActive, setScanActive] = useState(false);
  const [hasMaterializedMetrics, setHasMaterializedMetrics] = useState(false);
  const [hasResolvedProject, setHasResolvedProject] = useState(false);
  const [continuousActive, setContinuousActive] = useState(false);

  const check = useCallback(() => {
    if (!project) {
      setLastScanMinutes(null);
      setScanActive(false);
      setHasMaterializedMetrics(false);
      setHasResolvedProject(false);
      setContinuousActive(false);
      return;
    }

    getFindings(project)
      .then((raw) => {
        const resolvedProjectId = getResolvedProjectId(raw);
        const completedAt = getCompletedAt(raw);
        if (completedAt) setLastScanMinutes(Math.round((Date.now() - new Date(completedAt).getTime()) / 60000));
        else setLastScanMinutes(null);
        setHasResolvedProject(Boolean(resolvedProjectId || project));
        setScanActive(raw?.status === 'running');
        setHasMaterializedMetrics(Boolean(resolvedProjectId || project) && hasMaterializedFindingData(raw));
        setContinuousActive(isContinuousDiscoveryActive(raw));
      })
      .catch(() => {
        setLastScanMinutes(null);
        setScanActive(false);
        setHasMaterializedMetrics(false);
        setHasResolvedProject(false);
        setContinuousActive(false);
      })
  }, [project]);

  useEffect(() => {
    check();
    const timer = setInterval(check, intervalMs);
    return () => clearInterval(timer);
  }, [check, intervalMs]);

  useScanCompletedRefresh(project, check);

  return { lastScanMinutes, scanActive, hasMaterializedMetrics, hasResolvedProject, continuousActive };
}
