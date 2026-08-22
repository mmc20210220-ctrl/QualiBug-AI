import { useCallback, useEffect, useState } from 'react';
import { getFindings, getKnowledgeAsset, getProjects, type CustomerWorkspace } from './client';
import type { CommercialAssets, Finding, KnowledgeSource, TestTaskBoard, TestTaskSlice } from '../types';
import { toWorkspaceOptions } from '../lib/customer';
import { asArray, asNum, asRecord, asString } from '../lib/value-guards';

const SCAN_COMPLETED_EVENT = 'qualibug:scan-completed';

type JsonRecord = Record<string, unknown>;
type ScanCompletedDetail = { project: string };
type ProjectSummary = { resolvedProjectId: string; projectName: string; findingsCount: number; currentDefectCount: number; clueCount: number; p0Count: number };

function asBoolean(value: unknown): boolean {
  if (typeof value === 'boolean') return value;
  if (typeof value === 'number') return value !== 0;
  if (typeof value === 'string') {
    const normalized = value.trim().toLowerCase();
    if (['true', '1', 'yes', 'y', 'on'].includes(normalized)) return true;
    if (['false', '0', 'no', 'n', 'off', ''].includes(normalized)) return false;
  }
  return false;
}
function firstFiniteNumber(...values: unknown[]): number { for (const value of values) { if (value === null || value === undefined || value === '') continue; const parsed = Number(value); if (Number.isFinite(parsed)) return parsed; } return 0; }
function field(value: unknown, name: string): unknown { return asRecord(value)[name]; }
function stripFixAdviceForCustomer(value: Finding): Finding {
  const sanitized = { ...(value as unknown as JsonRecord) };
  delete sanitized.recommended_fix;
  const technical = asRecord(sanitized.technical_details);
  if (Object.keys(technical).length > 0) {
    const sanitizedTechnical = { ...technical };
    delete sanitizedTechnical.recommended_fix;
    delete sanitizedTechnical.possible_root_cause;
    sanitized.technical_details = sanitizedTechnical;
  }
  sanitized.product_responsibility_boundary = {
    scope: 'defect_discovery_evidence_regression_release_status',
    no_fix_advice: true,
    customer_meaning: 'QualiBug-AI 只提供缺陷事实、证据链、修复后回归验证和发布状态，不提供修复建议、修复方案或修复代码。',
  };
  return sanitized as unknown as Finding;
}
function findingFrom(value: unknown): Finding | null { const record = asRecord(value); return asString(record.id) || asString(record.title) ? stripFixAdviceForCustomer(record as unknown as Finding) : null; }

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
function classifiedRows(raw: unknown, classification: 'deliverable' | 'candidate' | 'rejected', fallback: string): unknown[] {
  const record = asRecord(raw);
  const projected = asRecord(record.finding_classification);
  return Array.isArray(projected[classification])
    ? asArray(projected[classification])
    : asArray(record[fallback]);
}
function getReportFindings(raw: unknown): Finding[] { return classifiedRows(raw, 'deliverable', 'defects').map(findingFrom).filter((value): value is Finding => value !== null); }
function getReportClues(raw: unknown): Finding[] { return classifiedRows(raw, 'candidate', 'clues').map(findingFrom).filter((value): value is Finding => value !== null); }
function getReportRejected(raw: unknown): Finding[] { return classifiedRows(raw, 'rejected', 'rejected_findings').map(findingFrom).filter((value): value is Finding => value !== null); }
function parseCommercialReleaseGate(raw: unknown): CommercialAssets['release_gate'] | undefined {
  const gate = asRecord(raw);
  if (!Object.keys(gate).length) return undefined;
  const checks = asArray(gate.checks).map((item) => {
    const check = asRecord(item);
    return { name: asString(check.name), status: asString(check.status), detail: asString(check.detail), source: asString(check.source) };
  }).filter((item) => item.name || item.status || item.detail);
  return {
    overall_status: asString(gate.overall_status),
    blocking_check_count: asNum(gate.blocking_check_count),
    pending_check_count: asNum(gate.pending_check_count),
    pass_check_count: asNum(gate.pass_check_count),
    release_recommendation: asString(gate.release_recommendation),
    checks,
    honesty_rule: asString(gate.honesty_rule),
  };
}
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
    finding_count: asNum(assets.finding_count),
    customer_ready_reproduction_count: asNum(assets.customer_ready_reproduction_count),
    release_gate_overall_status: asString(assets.release_gate_overall_status),
    release_recommendation: asString(assets.release_recommendation),
    release_gate_honesty_rule: asString(assets.release_gate_honesty_rule),
    release_gate: parseCommercialReleaseGate(assets.release_gate),
    commercial_handoff: {
      status: asString(handoff.status),
      acceptance_status: asString(handoff.acceptance_status),
      safe_for_customer: asBoolean(handoff.safe_for_customer),
      release_gate_status: asString(handoff.release_gate_status),
    },
    tracker_sync: {
      payload_status: asString(tracker.payload_status),
      payload_gate_status: asString(tracker.payload_gate_status),
      release_gate_overall_status: asString(tracker.release_gate_overall_status),
    },
    delivery_package: {
      status: asString(delivery.status),
      package_id: asString(delivery.package_id),
      package_ref: asString(delivery.package_ref),
      release_verdict: asString(delivery.release_verdict),
      evidence_bundle_id: asString(delivery.evidence_bundle_id),
      release_recommendation: asString(delivery.release_recommendation),
      release_gate_overall_status: asString(delivery.release_gate_overall_status),
      release_gate_blocked: asBoolean(delivery.release_gate_blocked),
      release_gate_block_reason: asString(delivery.release_gate_block_reason),
    },
    artifact_refs: Object.entries(refs).reduce<Record<string, string>>((acc, [key, value]) => {
      if (typeof value === 'string' && value.trim()) acc[key] = value;
      return acc;
    }, {}),
  };
}
function getCompletedAt(raw: unknown): string { const record = asRecord(raw); return (asString(record.updatedAt) || asString(record.updated_at)).trim(); }
function hasMaterializedFindingData(raw: unknown): boolean { const record = asRecord(raw); const executive = asRecord(record.executive_summary); const contract = asRecord(record.data_contract); return getReportFindings(raw).length > 0 || firstFiniteNumber(contract.materialized_risk_count, executive.materialized_findings, executive.total_findings) > 0 || asNum(field(record.runtime_verification, 'confirmed')) > 0 || asNum(field(record.db_verification, 'confirmed')) > 0; }
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
  const formalCounts = asRecord(record.formal_count_projection);
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
    formalCounts.formal_customer_deliverable_count,
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
      run_count: asNum(campaign.round_count),
      total_ms: asNum(currentRun.duration_ms, asNum(existingScanMeta.total_ms)),
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
function buildProjectSummary(raw: unknown, project: string): ProjectSummary {
  const normalized = normalizeCampaignSnapshot(raw);
  const record = asRecord(normalized);
  const findings = getReportFindings(raw);
  const scanMeta = asRecord(field(normalized, 'scan_meta'));
  const formalCounts = asRecord(field(normalized, 'formal_count_projection'));
  return {
    resolvedProjectId: getResolvedProjectId(normalized),
    projectName: (asString(record.project_name) || asString(record.projectName) || project).trim() || '未选择客户',
    findingsCount: findings.length,
    currentDefectCount: firstFiniteNumber(formalCounts.formal_customer_deliverable_count, scanMeta.formal_customer_deliverable_count, findings.length),
    clueCount: getReportClues(raw).length,
    p0Count: findings.filter((finding) => finding.severity === 'P0').length,
  };
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

/** Trust backend delivery gate projection; do not recompute commercial readiness locally. */
export function isCustomerReadyFinding(finding: Finding): boolean {
  if (!finding) return false;
  if (finding.customer_delivery_status !== 'defect') return false;
  if (finding.bug_status !== 'reproduced' || !finding.gate_passed || finding.reproduction?.is_synthetic) return false;
  return true;
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
  const [summary, setSummary] = useState<ProjectSummary>(empty); const [loading, setLoading] = useState(true); const [error, setError] = useState('');
  const load = useCallback(() => { if (!project) { setSummary(empty()); setLoading(false); setError(''); return; } setLoading(true); getFindings(project).then((raw) => { setSummary(buildProjectSummary(raw, project)); setError(''); }).catch((caught: unknown) => { setSummary(empty()); setError(caught instanceof Error ? caught.message : '项目状态读取失败'); }).finally(() => setLoading(false)); }, [empty, project]);
  useEffect(() => { load(); }, [load]); useScanCompletedRefresh(project, load);
  return { ...summary, hasResolvedProject: Boolean(summary.resolvedProjectId || project), loading, error };
}

export function usePipelineData(project: string) {
  const [data, setData] = useState<unknown>(null); const [loading, setLoading] = useState(true); const [error, setError] = useState('');
  const load = useCallback(() => { setLoading(true); setError(''); setData(null); getFindings(project).then((raw) => setData(normalizeCampaignSnapshot(raw))).catch((caught: unknown) => setError(caught instanceof Error ? caught.message : '加载失败')).finally(() => setLoading(false)); }, [project]);
  useEffect(() => { load(); }, [load]); useScanCompletedRefresh(project, load);
  return { data, loading, error, refetch: load };
}

export function useFindingsData(project: string) {
  const [findings, setFindings] = useState<Finding[]>([]); const [clues, setClues] = useState<Finding[]>([]); const [rejected, setRejected] = useState<Finding[]>([]); const [commercialAssets, setCommercialAssets] = useState<CommercialAssets | null>(null); const [scanMeta, setScanMeta] = useState<JsonRecord>({}); const [obligationProjection, setObligationProjection] = useState<JsonRecord>({}); const [loading, setLoading] = useState(true); const [error, setError] = useState('');
  const load = useCallback(() => { setLoading(true); setError(''); getFindings(project).then((raw) => { const record = asRecord(raw); const meta = asRecord(record.scan_meta); setFindings(getReportFindings(raw)); setClues(getReportClues(raw)); setRejected(getReportRejected(raw)); setCommercialAssets(getCommercialAssets(raw)); setScanMeta(meta); setObligationProjection(asRecord(record.obligation_execution_projection || meta.obligation_execution_projection)); }).catch((caught: unknown) => { setFindings([]); setClues([]); setRejected([]); setCommercialAssets(null); setScanMeta({}); setObligationProjection({}); setError(caught instanceof Error ? caught.message : '加载失败'); }).finally(() => setLoading(false)); }, [project]);
  useEffect(() => { load(); }, [load]); useScanCompletedRefresh(project, load);
  return { findings, clues, rejected, commercialAssets, scanMeta, obligationProjection, loading, error, refetch: load };
}

function parseKnowledgeSources(raw: unknown): KnowledgeSource[] {
  const record = asRecord(raw); const asset = asRecord(record.knowledge_asset); const sources = asArray(record.sources || asset.sources || asset.source_inventory);
  return sources.map((value) => {
    const source = asRecord(value);
    const parse = asRecord(source.parse);
    const receipt = asRecord(parse.receipt || parse.parser_receipt || source.parser_receipt);
    const parserStatus = asString(receipt.parser_status || parse.parser_status || parse.parse_status);
    const sourceStatus = asString(source.status) || 'active';
    const visibleStatus = ['failed', 'degraded'].includes(parserStatus) ? parserStatus : sourceStatus;
    return {
      source_id: asString(source.source_id) || asString(source.id),
      filename: asString(source.filename) || asString(source.original_name) || asString(source.name),
      source_type: asString(source.source_type) || asString(source.type),
      status: visibleStatus,
      size_bytes: asNum(source.size_bytes),
      uploaded_at: asString(source.uploaded_at) || asString(source.created_at_utc) || asString(source.created_at),
      parser_status: parserStatus,
      parser_fidelity: asString(receipt.fidelity || parse.fidelity),
      parser_errors: asArray(receipt.errors || parse.errors).map(asRecord),
    };
  }).filter((source) => source.status.trim() !== 'deleted');
}

export function useKnowledgeData(project: string) {
  const [sources, setSources] = useState<KnowledgeSource[]>([]); const [loading, setLoading] = useState(true); const [error, setError] = useState('');
  const load = useCallback(() => { setLoading(true); setSources([]); setError(''); getKnowledgeAsset(project).then((raw) => setSources(parseKnowledgeSources(raw))).catch((caught: unknown) => setError(caught instanceof Error ? caught.message : '资料列表加载失败')).finally(() => setLoading(false)); }, [project]);
  useEffect(() => { load(); }, [load]);
  return { sources, loading, error, refetch: load };
}

export function useTestTaskBoard(project: string) {
  const [board, setBoard] = useState<TestTaskBoard | null>(null);
  const [obligationProjection, setObligationProjection] = useState<JsonRecord>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const load = useCallback(() => {
    if (!project) { setBoard(null); setObligationProjection({}); setLoading(false); return; }
    setLoading(true); setError('');
    getFindings(project).then((raw) => {
      const record = asRecord(raw);
      const meta = asRecord(record.scan_meta);
      setObligationProjection(asRecord(record.obligation_execution_projection || meta.obligation_execution_projection));
      const boardRaw = asRecord(record.test_task_board);
      if (!boardRaw || (Object.keys(boardRaw).length === 0)) { setBoard(null); setLoading(false); return; }
      const ledger = asRecord(boardRaw.ledger);
      const slices = asArray(boardRaw.slices).map((value) => {
        const record = asRecord(value);
        const dims = asArray(record._system_behavior_dimensions).map(asString).filter(Boolean);
        const surfaces = asArray(record._system_behavior_surface_plan).map(asString).filter(Boolean);
        const routes = asArray(record._system_behavior_api_routes).map((r) => {
          const route = asRecord(r);
          return { method: asString(route.method), path: asString(route.path) };
        }).filter((r) => r.method && r.path);
        const assets = asArray(record._system_behavior_required_assets).map(asString).filter(Boolean);
        return {
          slice_id: asString(record.slice_id),
          title: asString(record.title),
          entity: asString(record.entity),
          kind: asString(record.kind),
          status: (asString(record.status) || 'pending') as TestTaskSlice['status'],
          priority: asNum(record.priority),
          endpoints: asArray(record.endpoints).map(asString).filter(Boolean),
          evidence_gaps: asArray(record.evidence_gaps).map(asString).filter(Boolean),
          _system_behavior_dimensions: dims.length > 0 ? dims : undefined,
          _system_behavior_surface_plan: surfaces.length > 0 ? surfaces : undefined,
          _system_behavior_api_routes: routes.length > 0 ? routes : undefined,
          _system_behavior_required_assets: assets.length > 0 ? assets : undefined,
          _selection_family: asString(record._selection_family) || undefined,
          _selection_origin: asString(record._selection_origin) || undefined,
          _coverage_steering_weight: asNum(record._coverage_steering_weight) || undefined,
          _learning_steering_weight: asNum(record._learning_steering_weight) || undefined,
          _historical_boundary_boost: asNum(record._historical_boundary_boost) || undefined,
          _historical_boundary_match: Object.keys(asRecord(record._historical_boundary_match)).length > 0 ? asRecord(record._historical_boundary_match) : undefined,
          source_refs: asArray(record.source_refs).map((r) => asRecord(r) as { source_type: string; locator: string; quote: string }),
          family: asString(record.family) || asString(record._selection_family) || undefined,
          severity: asString(record.severity) || undefined,
          source: asString(record.source) || '',
          target: asString(record.target) || '',
        } as TestTaskSlice;
      });
      const execution = asRecord(boardRaw.execution);
      setBoard({
        ledger: {
          campaign_id: asString(ledger.campaign_id),
          campaign_status: asString(ledger.campaign_status),
          attempted_slice_ids: asArray(ledger.attempted_slice_ids).map(asString),
          confirmed_slice_ids: asArray(ledger.confirmed_slice_ids).map(asString),
          slice_status: asRecord(ledger.slice_status) as Record<string, TestTaskSlice['status'] & string>,
          source_snapshot_hash: asString(ledger.source_snapshot_hash),
        },
        slices,
        execution: { production_data_blocked: asNum(execution.production_data_blocked) },
        evidence_chains_saved: asNum(boardRaw.evidence_chains_saved),
      });
    }).catch((caught: unknown) => {
      setBoard(null); setObligationProjection({}); setError(caught instanceof Error ? caught.message : '加载失败');
    }).finally(() => setLoading(false));
  }, [project]);
  useEffect(() => { load(); }, [load]); useScanCompletedRefresh(project, load);
  return { board, obligationProjection, loading, error, refetch: load };
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
