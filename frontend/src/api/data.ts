import { useCallback, useEffect, useState } from 'react';
import { getFindings, getKnowledgeAsset, getProjects, type CustomerWorkspace } from './client';
import type { CommercialAssets, Finding, KnowledgeSource, ReleaseCheck } from '../types';
import { toWorkspaceOptions } from '../lib/customer';

const SCAN_COMPLETED_EVENT = 'qualibug:scan-completed';
const CUSTOMER_READY_MIN_EVIDENCE_SCORE = 90;

type JsonRecord = Record<string, unknown>;
type ScanCompletedDetail = { project: string };
type ProjectSummary = { resolvedProjectId: string; projectName: string; findingsCount: number; currentDefectCount: number; clueCount: number; p0Count: number };

function asRecord(value: unknown): JsonRecord { return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as JsonRecord : {}; }
function asArray(value: unknown): unknown[] { return Array.isArray(value) ? value : []; }
function asString(value: unknown): string { return typeof value === 'string' ? value : ''; }
function asFiniteNumber(value: unknown, fallback = 0): number { const parsed = typeof value === 'number' ? value : Number(value); return Number.isFinite(parsed) ? parsed : fallback; }
function firstFiniteNumber(...values: unknown[]): number { for (const value of values) { if (value === null || value === undefined || value === '') continue; const parsed = Number(value); if (Number.isFinite(parsed)) return parsed; } return 0; }
function field(value: unknown, name: string): unknown { return asRecord(value)[name]; }
function findingFrom(value: unknown): Finding | null { const record = asRecord(value); return asString(record.id) || asString(record.title) ? record as unknown as Finding : null; }

export function emitScanCompleted(project: string): void { if (!project || typeof window === 'undefined') return; window.dispatchEvent(new CustomEvent<ScanCompletedDetail>(SCAN_COMPLETED_EVENT, { detail: { project } })); }

export function useScanCompletedRefresh(project: string, refresh: () => void): void {
  useEffect(() => {
    if (!project || typeof window === 'undefined') return;
    const handler = (event: Event) => { const detail = (event as CustomEvent<ScanCompletedDetail>).detail; if (detail?.project === project) refresh(); };
    window.addEventListener(SCAN_COMPLETED_EVENT, handler);
    return () => window.removeEventListener(SCAN_COMPLETED_EVENT, handler);
  }, [project, refresh]);
}

function getResolvedProjectId(raw: unknown): string { const record = asRecord(raw); return (asString(record.resolvedProjectId) || asString(record.projectId)).trim(); }
function getReportFindings(raw: unknown): Finding[] { return asArray(field(raw, 'defects')).map(findingFrom).filter((value): value is Finding => value !== null); }
function getReportClues(raw: unknown): Finding[] { return asArray(field(raw, 'clues')).map(findingFrom).filter((value): value is Finding => value !== null); }
export function getCommercialAssets(raw: unknown): CommercialAssets | null {
  const record = asRecord(raw);
  const assets = asRecord(record.commercial_assets);
  if (!Object.keys(assets).length) return null;
  const handoff = asRecord(assets.commercial_handoff);
  const tracker = asRecord(assets.tracker_sync);
  const delivery = asRecord(assets.delivery_package);
  const refs = asRecord(assets.artifact_refs);
  return {
    status: asString(assets.status),
    finding_count: asFiniteNumber(assets.finding_count),
    customer_ready_reproduction_count: asFiniteNumber(assets.customer_ready_reproduction_count),
    commercial_handoff: {
      status: asString(handoff.status),
      acceptance_status: asString(handoff.acceptance_status),
      safe_for_customer: Boolean(handoff.safe_for_customer),
    },
    tracker_sync: {
      payload_status: asString(tracker.payload_status),
      payload_gate_status: asString(tracker.payload_gate_status),
    },
    delivery_package: {
      status: asString(delivery.status),
      package_id: asString(delivery.package_id),
      package_ref: asString(delivery.package_ref),
      release_verdict: asString(delivery.release_verdict),
      evidence_bundle_id: asString(delivery.evidence_bundle_id),
    },
    artifact_refs: Object.entries(refs).reduce<Record<string, string>>((acc, [key, value]) => {
      if (typeof value === 'string' && value.trim()) acc[key] = value;
      return acc;
    }, {}),
  };
}
function getCompletedAt(raw: unknown): string { const record = asRecord(raw); return (asString(record.updatedAt) || asString(record.updated_at)).trim(); }
function hasMaterializedFindingData(raw: unknown): boolean { const record = asRecord(raw); const executive = asRecord(record.executive_summary); const contract = asRecord(record.data_contract); return getReportFindings(raw).length > 0 || firstFiniteNumber(contract.materialized_risk_count, executive.materialized_findings, executive.total_findings) > 0 || asFiniteNumber(field(record.runtime_verification, 'confirmed')) > 0 || asFiniteNumber(field(record.db_verification, 'confirmed')) > 0; }
function campaignFrom(raw: unknown): JsonRecord {
  const record = asRecord(raw);
  const continuous = asRecord(record.continuous_discovery_campaign || record.continuousDiscoveryCampaign);
  const nestedCampaign = asRecord(continuous.campaign);
  if (Object.keys(nestedCampaign).length > 0) return nestedCampaign;
  const direct = asRecord(record.campaign);
  if (Object.keys(direct).length > 0) return direct;
  return asRecord(continuous.summary);
}
function isContinuousDiscoveryActive(raw: unknown): boolean {
  const record = asRecord(raw);
  const campaign = asRecord(record.continuous_discovery_campaign || record.continuousDiscoveryCampaign);
  const summary = asRecord(campaign.summary || campaign.campaign);
  const currentRun = asRecord(campaign.current_run);
  const state = (asString(summary.campaign_state) || asString(summary.campaign_status) || asString(summary.state) || asString(currentRun.status)).toLowerCase();
  return ['running', 'scanning', 'active', 'in_progress'].includes(state) || Boolean(currentRun.started_at) && !currentRun.finished_at;
}
function normalizeCampaignSnapshot(raw: unknown): JsonRecord {
  const record = asRecord(raw);
  const campaign = campaignFrom(record);
  if (Object.keys(campaign).length === 0) return record;
  const continuous = asRecord(record.continuous_discovery_campaign || record.continuousDiscoveryCampaign);
  const currentRun = asRecord(continuous.current_run);
  const summary = asRecord(continuous.summary);
  const existingScanMeta = asRecord(record.scan_meta);
  const currentCampaignScope = asRecord(
    existingScanMeta.current_campaign_scope || record.current_campaign_scope || {
      campaign_id: asString(campaign.campaign_id || summary.campaign_id || currentRun.campaign_id),
      lineage_campaign_id: asString(campaign.lineage_campaign_id || summary.lineage_campaign_id),
      scope_id: asString(campaign.scope_id || summary.scope_id || currentRun.scope_id),
      environment_ref: asString(campaign.environment_ref || campaign.target_environment || summary.environment_ref || summary.target_environment || currentRun.environment_ref || currentRun.target_environment),
      source_hash: asString(campaign.source_hash || summary.source_hash),
      source_snapshot_hash: asString(campaign.source_snapshot_hash || summary.source_snapshot_hash),
    },
  );
  const currentScopeFindingCount = firstFiniteNumber(
    summary.current_campaign_bundle_finding_count_raw,
    currentRun.current_campaign_bundle_finding_count_raw,
    existingScanMeta.current_report_total_findings,
    existingScanMeta.total_findings,
  );
  const currentScopeDefectCount = firstFiniteNumber(
    summary.current_campaign_customer_ready_defect_count,
    currentRun.current_campaign_customer_ready_defect_count,
    existingScanMeta.current_report_customer_ready_defect_count,
    existingScanMeta.customer_ready_defects,
  );
  return {
    ...record,
    campaign,
    coverage_gaps: Array.isArray(record.coverage_gaps) ? record.coverage_gaps : [],
    current_campaign_scope: currentCampaignScope,
    scan_meta: {
      ...existingScanMeta,
      run_count: asFiniteNumber(campaign.round_count),
      total_ms: asFiniteNumber(currentRun.duration_ms, asFiniteNumber(existingScanMeta.total_ms)),
      current_report_total_findings: currentScopeFindingCount,
      total_findings: firstFiniteNumber(existingScanMeta.total_findings, currentScopeFindingCount),
      current_report_customer_ready_defect_count: currentScopeDefectCount,
      customer_ready_defects: currentScopeDefectCount,
      current_campaign_bundle_finding_count_raw: firstFiniteNumber(
        summary.current_campaign_bundle_finding_count_raw,
        currentRun.current_campaign_bundle_finding_count_raw,
        existingScanMeta.current_campaign_bundle_finding_count_raw,
        currentScopeFindingCount,
      ),
      family_customer_ready_defect_count: firstFiniteNumber(
        existingScanMeta.family_customer_ready_defect_count,
        summary.family_customer_ready_defect_count,
        getReportFindings(record).length,
      ),
      current_campaign_scope: currentCampaignScope,
    },
  };
}
function campaignBlocksRelease(raw: unknown): { blocked: boolean; status: string; reason: string } {
  const campaign = campaignFrom(raw);
  const status = asString(campaign.campaign_status || campaign.campaign_state).toLowerCase();
  const blocked = status === 'blocked' || status === 'coverage_deferred';
  return { blocked, status, reason: asString(campaign.coverage_deferred_reason) || asString(campaign.next_campaign_reason) };
}
function buildProjectSummary(raw: unknown, project: string): ProjectSummary {
  const normalized = normalizeCampaignSnapshot(raw);
  const record = asRecord(normalized);
  const findings = getReportFindings(normalized);
  const scanMeta = asRecord(field(normalized, 'scan_meta'));
  return {
    resolvedProjectId: getResolvedProjectId(normalized),
    projectName: (asString(record.project_name) || asString(record.projectName) || project).trim() || '未选择客户',
    findingsCount: findings.length,
    currentDefectCount: firstFiniteNumber(scanMeta.current_report_customer_ready_defect_count, scanMeta.customer_ready_defects),
    clueCount: getReportClues(normalized).length,
    p0Count: findings.filter((finding) => finding.severity === 'P0').length,
  };
}

function hasValidatedEvidenceQuality(finding: Finding): boolean {
  const quality = finding?.evidence_quality;
  const level = String(quality?.level || '').toLowerCase();
  const score = asFiniteNumber(quality?.score);
  return level === 'validated' && score >= CUSTOMER_READY_MIN_EVIDENCE_SCORE && Boolean(quality?.can_reproduce);
}

function hasPassedBusinessEvidenceStatus(finding: Finding): boolean {
  const status = finding?.evidence_status;
  if (!status) return false;
  const semantic = String(status.semantic_verdict || '').toUpperCase();
  const business = String(status.business_evidence_status || '').toUpperCase();
  const finalReview = String(status.final_review_status || '').toUpperCase();
  if (semantic !== 'SEMANTIC_CONFIRMED') return false;
  if (business !== 'VALIDATED') return false;
  if (!['PENDING_REVIEW', 'VALIDATED_CANDIDATE', 'CUSTOMER_READY'].includes(finalReview)) return false;
  const missing = Array.isArray(status.missing_requirements) ? status.missing_requirements : [];
  return missing.length === 0;
}

function hasExplicitFailureAssertion(finding: Finding): boolean {
  const failedAssertions = finding?.failed_assertions || [];
  const comparison = finding?.expected_actual_comparison;
  const diff = String(comparison?.difference || '').trim();
  if (failedAssertions.length > 0 || diff) return true;
  const expected = String(finding?.expected || comparison?.expected || '').trim();
  const actual = String(finding?.actual || comparison?.actual || '').trim();
  return Boolean(expected && actual && expected !== actual);
}

/** Require explicit execution and full evidence before customer delivery. */
export function isCustomerReadyFinding(finding: Finding): boolean {
  if (!finding || finding.customer_delivery_status !== 'defect' || finding.bug_status !== 'reproduced' || !finding.gate_passed || finding.reproduction?.is_synthetic) return false;
  const record = asRecord(finding);
  const executionStatus = asString(record.execution_status).toLowerCase();
  const confirmationStatus = asString(record.confirmation_status).toLowerCase();
  const evidenceLevel = asString(record.evidence_level).toLowerCase();
  const executionSource = asString(record.execution_source).toLowerCase();
  if (['simulation', 'simulated', 'demo', 'synthetic', 'mock'].some((value) => evidenceLevel.includes(value) || executionSource.includes(value))) return false;
  if (executionStatus && executionStatus !== 'executed') return false;
  if (confirmationStatus && !['confirmed', 'validated_candidate'].includes(confirmationStatus)) return false;
  const consistency = asRecord(record.evidence_consistency);
  if (['rejected', 'missing'].includes(asString(consistency.verdict).toLowerCase())) return false;
  const lane = `${asString(record.value_lane)} ${asString(record._value_lane)} ${asString(record.execution_block)} ${asString(record.block_reason)}`.toLowerCase();
  if (['route_blocked', 'auth_blocked', 'environment_blocked', 'coverage_gap', 'validation_lead', 'not_reproduced'].some((value) => lane.includes(value))) return false;
  return hasValidatedEvidenceQuality(finding) && hasPassedBusinessEvidenceStatus(finding) && hasRealReplayAsset(finding) && hasCustomerFacingHardEvidence(finding);
}

export function hasCustomerFacingHardEvidence(finding: Finding): boolean {
  const raw = finding?.raw_evidence; const reproduction = finding?.reproduction;
  const hasRequest = Boolean(raw?.request_raw?.method && raw?.request_raw?.path) || Boolean(reproduction?.method && reproduction?.path);
  const hasResponse = Boolean(raw?.response_raw?.status_code || raw?.response_raw?.body || reproduction?.har_evidence?.status_code || reproduction?.har_evidence?.response_body);
  const hasAssertion = hasExplicitFailureAssertion(finding);
  const hasTimestamp = Boolean(raw?.timestamp || finding?.timestamp);
  return hasRequest && hasResponse && hasAssertion && hasTimestamp && Boolean(raw?.has_real_evidence || reproduction?.har_evidence);
}

export function hasRealReplayAsset(finding: Finding): boolean { const reproduction = finding?.reproduction; const quality = finding?.evidence_quality; return Boolean(quality?.can_reproduce && !reproduction?.is_synthetic && reproduction?.method && reproduction?.path && (reproduction?.har_evidence?.status_code || reproduction?.har_evidence?.response_body)); }

export function useWorkspaceDirectory() {
  const [workspaces, setWorkspaces] = useState<CustomerWorkspace[]>([]); const [loadError, setLoadError] = useState('');
  const refresh = useCallback(async (force = false) => { try { const items = await getProjects({ force }); setWorkspaces(items); setLoadError(''); return items; } catch (error: unknown) { setWorkspaces([]); setLoadError(error instanceof Error ? error.message : '客户列表加载失败'); return []; } }, []);
  useEffect(() => { void refresh(); }, [refresh]);
  return { workspaces, workspaceOptions: toWorkspaceOptions(workspaces), loadError, refresh };
}

export function useProjectSummary(project: string) {
  const empty = useCallback((): ProjectSummary => ({ resolvedProjectId: '', projectName: project || '未选择客户', findingsCount: 0, currentDefectCount: 0, clueCount: 0, p0Count: 0 }), [project]);
  const [summary, setSummary] = useState<ProjectSummary>(empty); const [loading, setLoading] = useState(true);
  const load = useCallback(() => { if (!project) { setSummary(empty()); setLoading(false); return; } setLoading(true); getFindings(project).then((raw) => setSummary(buildProjectSummary(raw, project))).catch(() => setSummary(empty())).finally(() => setLoading(false)); }, [empty, project]);
  useEffect(() => { load(); }, [load]); useScanCompletedRefresh(project, load);
  return { ...summary, hasResolvedProject: Boolean(summary.resolvedProjectId || project), loading };
}

export function usePipelineData(project: string) {
  const [data, setData] = useState<unknown>(null); const [loading, setLoading] = useState(true); const [error, setError] = useState('');
  const load = useCallback(() => { setLoading(true); setError(''); setData(null); getFindings(project).then((raw) => setData(normalizeCampaignSnapshot(raw))).catch((caught: unknown) => setError(caught instanceof Error ? caught.message : '加载失败')).finally(() => setLoading(false)); }, [project]);
  useEffect(() => { load(); }, [load]); useScanCompletedRefresh(project, load);
  return { data, loading, error, refetch: load };
}

export function useFindingsData(project: string) {
  const [findings, setFindings] = useState<Finding[]>([]); const [clues, setClues] = useState<Finding[]>([]); const [commercialAssets, setCommercialAssets] = useState<CommercialAssets | null>(null); const [scanMeta, setScanMeta] = useState<JsonRecord>({}); const [loading, setLoading] = useState(true); const [error, setError] = useState('');
  const load = useCallback(() => { setLoading(true); setError(''); getFindings(project).then((raw) => { const record = asRecord(raw); setFindings(getReportFindings(raw)); setClues(getReportClues(raw)); setCommercialAssets(getCommercialAssets(raw)); setScanMeta(asRecord(record.scan_meta)); }).catch((caught: unknown) => { setFindings([]); setClues([]); setCommercialAssets(null); setScanMeta({}); setError(caught instanceof Error ? caught.message : '加载失败'); }).finally(() => setLoading(false)); }, [project]);
  useEffect(() => { load(); }, [load]); useScanCompletedRefresh(project, load);
  return { findings, clues, commercialAssets, scanMeta, loading, error, refetch: load };
}

function parseKnowledgeSources(raw: unknown): KnowledgeSource[] {
  const record = asRecord(raw); const asset = asRecord(record.knowledge_asset); const sources = asArray(record.sources || asset.sources || asset.source_inventory);
  return sources.map((value) => { const source = asRecord(value); return { source_id: asString(source.source_id) || asString(source.id), filename: asString(source.filename) || asString(source.original_name) || asString(source.name), source_type: asString(source.source_type) || asString(source.type), status: asString(source.status) || 'active', size_bytes: asFiniteNumber(source.size_bytes), uploaded_at: asString(source.uploaded_at) || asString(source.created_at_utc) || asString(source.created_at) }; }).filter((source) => source.status.trim() !== 'deleted');
}

export function useKnowledgeData(project: string) {
  const [sources, setSources] = useState<KnowledgeSource[]>([]); const [loading, setLoading] = useState(true); const [error, setError] = useState('');
  const load = useCallback(() => { setLoading(true); setSources([]); setError(''); getKnowledgeAsset(project).then((raw) => setSources(parseKnowledgeSources(raw))).catch((caught: unknown) => setError(caught instanceof Error ? caught.message : '资料列表加载失败')).finally(() => setLoading(false)); }, [project]);
  useEffect(() => { load(); }, [load]);
  return { sources, loading, error, refetch: load };
}

function parseReleaseChecks(raw: unknown): { overall: 'pass' | 'fail'; checks: ReleaseCheck[] } {
  const normalized = normalizeCampaignSnapshot(raw);
  const findings = getReportFindings(normalized).filter(isCustomerReadyFinding);
  const p0 = findings.filter((finding) => finding.severity === 'P0').length;
  const security = findings.filter((finding) => finding.defect_family === 'security_boundary' || finding.defect_family === 'privacy_compliance').length;
  const integrity = findings.filter((finding) => finding.defect_family === 'data_integrity').length;
  const dbConfirmed = asFiniteNumber(field(field(normalized, 'db_verification'), 'confirmed'));
  const campaignGate = campaignBlocksRelease(normalized);
  const campaignDetail = campaignGate.reason || (campaignGate.status === 'blocked' ? 'Campaign 缺少进入执行的必要合同' : campaignGate.status === 'coverage_deferred' ? '自动覆盖已递延到后续 Campaign' : 'Campaign 未报告阻断');
  return { overall: p0 > 0 || campaignGate.blocked ? 'fail' : 'pass', checks: [
    { name: 'Campaign 治理状态', status: campaignGate.blocked ? 'fail' : 'pass', detail: campaignGate.blocked ? campaignDetail : 'Campaign 未阻塞或递延覆盖' },
    { name: 'P0 缺陷阻塞', status: p0 === 0 ? 'pass' : 'fail', detail: p0 === 0 ? '无 P0 缺陷' : `${p0} 个 P0 缺陷未修复` },
    { name: '认证授权检测', status: security === 0 ? 'pass' : 'fail', detail: security === 0 ? '未发现认证授权类缺陷' : `${security} 个安全类缺陷待修复` },
    { name: '数据完整性校验', status: integrity === 0 ? 'pass' : 'fail', detail: integrity === 0 ? '未发现数据一致性缺陷' : `${integrity} 个数据完整性缺陷待修复` },
    { name: 'DB 验证', status: dbConfirmed === 0 ? 'pass' : 'fail', detail: dbConfirmed === 0 ? 'DB 一致性检查通过' : `${dbConfirmed} 个 DB 不一致` },
  ]};
}

export function useReleaseData(project: string) {
  const [data, setData] = useState<{ overall: 'pass' | 'fail'; checks: ReleaseCheck[] } | null>(null); const [loading, setLoading] = useState(true);
  const load = useCallback(() => { if (!project) { setData(null); setLoading(false); return; } setLoading(true); getFindings(project).then((raw) => { const status = asString(field(raw, 'status')) || asString(field(field(raw, 'live_map'), 'status')); setData(getResolvedProjectId(raw) && status && status !== 'idle' ? parseReleaseChecks(raw) : null); }).catch(() => setData(null)).finally(() => setLoading(false)); }, [project]);
  useEffect(() => { load(); }, [load]); useScanCompletedRefresh(project, load);
  return { data, loading, refetch: load };
}

export function useLiveStatus(project: string, intervalMs = 30000) {
  const [lastScanMinutes, setLastScanMinutes] = useState<number | null>(null); const [scanActive, setScanActive] = useState(false); const [hasMaterializedMetrics, setHasMaterializedMetrics] = useState(false); const [hasResolvedProject, setHasResolvedProject] = useState(false); const [continuousActive, setContinuousActive] = useState(false);
  const check = useCallback(() => {
    if (!project) { setLastScanMinutes(null); setScanActive(false); setHasMaterializedMetrics(false); setHasResolvedProject(false); setContinuousActive(false); return; }
    getFindings(project).then((raw) => { const resolved = getResolvedProjectId(raw); const completedAt = getCompletedAt(raw); const record = asRecord(raw); setLastScanMinutes(completedAt ? Math.round((Date.now() - new Date(completedAt).getTime()) / 60000) : null); setHasResolvedProject(Boolean(resolved || project)); setScanActive(asString(record.status) === 'running' || asString(field(record.live_map, 'status')) === 'running'); setHasMaterializedMetrics(Boolean(resolved || project) && hasMaterializedFindingData(raw)); setContinuousActive(isContinuousDiscoveryActive(raw)); }).catch(() => { setLastScanMinutes(null); setScanActive(false); setHasMaterializedMetrics(false); setHasResolvedProject(false); setContinuousActive(false); });
  }, [project]);
  useEffect(() => { check(); const timer = setInterval(check, intervalMs); return () => clearInterval(timer); }, [check, intervalMs]); useScanCompletedRefresh(project, check);
  return { lastScanMinutes, scanActive, hasMaterializedMetrics, hasResolvedProject, continuousActive };
}
