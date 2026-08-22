import { useCallback, useEffect, useMemo, useReducer, useState } from 'react';
import { getFindings, getKnowledgeAsset, getProjects, type CustomerWorkspace } from './client';
import type { CommercialAssets, Finding, KnowledgeSource, TestTaskSlice } from '../types';
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

// ── 共享 command-center 数据源 ──
// 同一项目的全部消费者（Topbar/Sidebar/Dashboard/Findings/Release/Settings…）
// 订阅同一份 command-center 快照：每项目仅一个轮询循环、一次网络往返，
// 所有视图从同一状态派生，消除多面板各自轮询造成的不一致窗口与重复负载。

type PipelineSnapshot = { raw: JsonRecord | null; error: string; loading: boolean };
type PipelineEntry = PipelineSnapshot & {
  fetching: boolean;
  timer: number | null;
  intervalMs: number;
  requests: Map<() => void, number>;
};

const MIN_PIPELINE_INTERVAL_MS = 5_000;
const pipelineEntries = new Map<string, PipelineEntry>();

function effectiveIntervalMs(entry: PipelineEntry): number {
  let min = Number.POSITIVE_INFINITY;
  entry.requests.forEach((ms) => { if (ms < min) min = ms; });
  return Number.isFinite(min) ? Math.max(MIN_PIPELINE_INTERVAL_MS, min) : 30_000;
}

function schedulePipelineLoop(project: string, entry: PipelineEntry): void {
  const nextInterval = effectiveIntervalMs(entry);
  if (!entry.requests.size) {
    if (entry.timer !== null) { window.clearInterval(entry.timer); entry.timer = null; }
    entry.intervalMs = nextInterval;
    return;
  }
  if (entry.timer !== null && entry.intervalMs === nextInterval) return;
  if (entry.timer !== null) window.clearInterval(entry.timer);
  entry.intervalMs = nextInterval;
  entry.timer = window.setInterval(() => { void fetchPipeline(project, entry); }, entry.intervalMs);
}

async function fetchPipeline(project: string, entry: PipelineEntry): Promise<void> {
  if (entry.fetching || !project) return;
  entry.fetching = true;
  try {
    const raw = await getFindings(project);
    // 失败时置空快照：陈旧成功数据不能在故障期间冒充当前结论。
    entry.raw = asRecord(raw);
    entry.error = '';
  } catch (caught: unknown) {
    entry.raw = null;
    entry.error = caught instanceof Error ? caught.message : '项目状态加载失败';
  } finally {
    entry.fetching = false;
    entry.loading = false;
    entry.requests.forEach((_, notify) => notify());
  }
}

function subscribePipeline(project: string, requestedIntervalMs: number, notify: () => void): () => void {
  let entry = pipelineEntries.get(project);
  if (!entry) {
    entry = { raw: null, error: '', loading: true, fetching: false, timer: null, intervalMs: 30_000, requests: new Map() };
    pipelineEntries.set(project, entry);
  }
  entry.requests.set(notify, requestedIntervalMs);
  schedulePipelineLoop(project, entry);
  void fetchPipeline(project, entry);
  return () => {
    entry.requests.delete(notify);
    schedulePipelineLoop(project, entry);
  };
}

export function useScanCompletedRefresh(project: string): void {
  useEffect(() => {
    if (!project || typeof window === 'undefined') return;
    const handler = (event: Event) => {
      const detail = (event as CustomEvent<ScanCompletedDetail>).detail;
      if (detail?.project !== project) return;
      const entry = pipelineEntries.get(project);
      if (entry) void fetchPipeline(project, entry);
    };
    window.addEventListener(SCAN_COMPLETED_EVENT, handler);
    return () => window.removeEventListener(SCAN_COMPLETED_EVENT, handler);
  }, [project]);
}

/** 共享 command-center 快照订阅：外部派生视图（如 onboarding 进度）复用同一份数据。 */
export function usePipelineSnapshot(project: string, intervalMs = 30_000): PipelineSnapshot & { refetch: () => void } {
  const [, forceUpdate] = useReducer((count: number) => count + 1, 0);
  useEffect(() => {
    if (!project) return undefined;
    return subscribePipeline(project, intervalMs, forceUpdate);
  }, [project, intervalMs, forceUpdate]);
  const refetch = useCallback(() => {
    if (!project) return;
    const current = pipelineEntries.get(project);
    if (current) void fetchPipeline(project, current);
  }, [project]);
  const entry = project ? pipelineEntries.get(project) : undefined;
  return {
    raw: project ? entry?.raw ?? null : null,
    error: project ? entry?.error ?? '' : '',
    loading: Boolean(project) && !entry?.raw && (entry?.loading ?? true),
    refetch,
  };
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
/** 后端已物化真实检测数据的判定：供进度线等外部派生复用，不发明完成条件。 */
export function hasMaterializedFindingData(raw: unknown): boolean { const record = asRecord(raw); const executive = asRecord(record.executive_summary); const contract = asRecord(record.data_contract); return getReportFindings(raw).length > 0 || firstFiniteNumber(contract.materialized_risk_count, executive.materialized_findings, executive.total_findings) > 0 || asNum(field(record.runtime_verification, 'confirmed')) > 0 || asNum(field(record.db_verification, 'confirmed')) > 0; }
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
  const { raw, error, loading } = usePipelineSnapshot(project, 15_000);
  useScanCompletedRefresh(project);
  const empty = useCallback((): ProjectSummary => ({ resolvedProjectId: '', projectName: project || '未选择客户', findingsCount: 0, currentDefectCount: 0, clueCount: 0, p0Count: 0 }), [project]);
  const summary = raw ? buildProjectSummary(raw, project) : empty();
  return { ...summary, hasResolvedProject: Boolean(summary.resolvedProjectId || project), loading, error };
}

export function usePipelineData(project: string) {
  const { raw, error, loading, refetch } = usePipelineSnapshot(project);
  useScanCompletedRefresh(project);
  return { data: raw ? normalizeCampaignSnapshot(raw) : null, loading, error, refetch };
}

export function useFindingsData(project: string) {
  const { raw, error, loading, refetch } = usePipelineSnapshot(project, 15_000);
  useScanCompletedRefresh(project);
  const record = asRecord(raw);
  const meta = asRecord(record.scan_meta);
  return {
    findings: raw ? getReportFindings(raw) : [],
    clues: raw ? getReportClues(raw) : [],
    rejected: raw ? getReportRejected(raw) : [],
    commercialAssets: raw ? getCommercialAssets(raw) : null,
    scanMeta: meta,
    obligationProjection: asRecord(record.obligation_execution_projection || meta.obligation_execution_projection),
    loading,
    error,
    refetch,
  };
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
  const { raw, error, loading, refetch } = usePipelineSnapshot(project, 15_000);
  useScanCompletedRefresh(project);
  const board = useMemo(() => {
    if (!raw) return null;
    const record = asRecord(raw);
    const boardRaw = asRecord(record.test_task_board);
    if (!boardRaw || (Object.keys(boardRaw).length === 0)) return null;
    const ledger = asRecord(boardRaw.ledger);
    const slices = asArray(boardRaw.slices).map((value) => {
      const sliceRow = asRecord(value);
      const dims = asArray(sliceRow._system_behavior_dimensions).map(asString).filter(Boolean);
      const surfaces = asArray(sliceRow._system_behavior_surface_plan).map(asString).filter(Boolean);
      const routes = asArray(sliceRow._system_behavior_api_routes).map((r) => {
        const route = asRecord(r);
        return { method: asString(route.method), path: asString(route.path) };
      }).filter((r) => r.method && r.path);
      const assets = asArray(sliceRow._system_behavior_required_assets).map(asString).filter(Boolean);
      return {
        slice_id: asString(sliceRow.slice_id),
        title: asString(sliceRow.title),
        entity: asString(sliceRow.entity),
        kind: asString(sliceRow.kind),
        status: (asString(sliceRow.status) || 'pending') as TestTaskSlice['status'],
        priority: asNum(sliceRow.priority),
        endpoints: asArray(sliceRow.endpoints).map(asString).filter(Boolean),
        evidence_gaps: asArray(sliceRow.evidence_gaps).map(asString).filter(Boolean),
        _system_behavior_dimensions: dims.length > 0 ? dims : undefined,
        _system_behavior_surface_plan: surfaces.length > 0 ? surfaces : undefined,
        _system_behavior_api_routes: routes.length > 0 ? routes : undefined,
        _system_behavior_required_assets: assets.length > 0 ? assets : undefined,
        _selection_family: asString(sliceRow._selection_family) || undefined,
        _selection_origin: asString(sliceRow._selection_origin) || undefined,
        _coverage_steering_weight: asNum(sliceRow._coverage_steering_weight) || undefined,
        _learning_steering_weight: asNum(sliceRow._learning_steering_weight) || undefined,
        _historical_boundary_boost: asNum(sliceRow._historical_boundary_boost) || undefined,
        _historical_boundary_match: Object.keys(asRecord(sliceRow._historical_boundary_match)).length > 0 ? asRecord(sliceRow._historical_boundary_match) : undefined,
        source_refs: asArray(sliceRow.source_refs).map((r) => asRecord(r) as { source_type: string; locator: string; quote: string }),
        family: asString(sliceRow.family) || asString(sliceRow._selection_family) || undefined,
        severity: asString(sliceRow.severity) || undefined,
        source: asString(sliceRow.source) || '',
        target: asString(sliceRow.target) || '',
      } as TestTaskSlice;
    });
    const execution = asRecord(boardRaw.execution);
    return {
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
    };
  }, [raw]);
  const obligationProjection = useMemo(() => {
    if (!raw) return {};
    const record = asRecord(raw);
    return asRecord(record.obligation_execution_projection || asRecord(record.scan_meta).obligation_execution_projection);
  }, [raw]);
  return { board, obligationProjection, loading, error, refetch };
}

export function useLiveStatus(project: string, intervalMs = 30000) {
  const { raw, error } = usePipelineSnapshot(project, intervalMs);
  useScanCompletedRefresh(project);
  if (!project || !raw || error) {
    return { lastScanMinutes: null, scanActive: false, hasMaterializedMetrics: false, hasResolvedProject: Boolean(project && raw), continuousActive: false };
  }
  const resolved = getResolvedProjectId(raw);
  const completedAt = getCompletedAt(raw);
  const record = asRecord(raw);
  return {
    lastScanMinutes: completedAt ? Math.round((Date.now() - new Date(completedAt).getTime()) / 60000) : null,
    scanActive: asString(record.status) === 'running' || asString(field(record.live_map, 'status')) === 'running',
    hasMaterializedMetrics: Boolean(resolved || project) && hasMaterializedFindingData(raw),
    hasResolvedProject: Boolean(resolved || project),
    continuousActive: isContinuousDiscoveryActive(raw),
  };
}
