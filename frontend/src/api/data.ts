/* eslint-disable @typescript-eslint/no-explicit-any */
/**
 * QualiBug Data Layer — unified API fetching, pure passthrough, zero computation.
 * Backend returns display-ready JSON, this layer only fetches and passes through.
 */
import { useState, useEffect, useCallback } from 'react';
import { getFindings, getKnowledgeAsset, getProjects, type CustomerWorkspace } from './client';
import type { Finding, KnowledgeSource, ReleaseCheck, ReplayResult } from '../types';
import { toWorkspaceOptions } from '../lib/customer';

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
  clueCount: number;
  p0Count: number;
};

function getResolvedProjectId(raw: any): string {
  return String(raw?.resolvedProjectId || raw?.projectId || '').trim();
}

function getReportFindings(raw: any): Finding[] {
  const defects = raw?.defects;
  if (Array.isArray(defects)) return defects as Finding[];
  return [];
}

function getReportClues(raw: any): Finding[] {
  const clues = raw?.clues;
  return Array.isArray(clues) ? clues as Finding[] : [];
}

function getCompletedAt(raw: any): string {
  return String(raw?.updatedAt || raw?.updated_at || '').trim();
}

function asFiniteNumber(value: any, fallback = 0): number {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function firstFiniteNumber(...values: any[]): number {
  for (const value of values) {
    if (value === null || value === undefined || value === '') continue;
    const n = Number(value);
    if (Number.isFinite(n)) return n;
  }
  return 0;
}

function hasMaterializedFindingData(raw: any): boolean {
  const findings = getReportFindings(raw);
  const exec = raw?.executive_summary || {};
  const contract = raw?.data_contract || {};
  const materializedTotal = firstFiniteNumber(
    contract.materialized_risk_count,
    exec.materialized_findings,
    exec.total_findings,
    findings.length
  );
  const runtimeConfirmed = asFiniteNumber(raw?.runtime_verification?.confirmed);
  const dbConfirmed = asFiniteNumber(raw?.db_verification?.confirmed);
  return findings.length > 0 || materializedTotal > 0 || runtimeConfirmed > 0 || dbConfirmed > 0;
}

function isContinuousDiscoveryActive(raw: any): boolean {
  const campaign = raw?.continuous_discovery_campaign || raw?.continuousDiscoveryCampaign;
  const summary = campaign?.summary || campaign?.campaign || {};
  const state = String(summary?.campaign_state || summary?.state || campaign?.current_run?.status || '').trim().toLowerCase();
  if (['running', 'scanning', 'active', 'in_progress'].includes(state)) return true;
  return Boolean(campaign?.current_run?.started_at) && !campaign?.current_run?.finished_at;
}

function buildProjectSummary(raw: any, project: string): ProjectSummary {
  const resolvedProjectId = getResolvedProjectId(raw);
  const findings = getReportFindings(raw);
  const clues = getReportClues(raw);
  return {
    resolvedProjectId,
    projectName: String(raw?.project_name || raw?.projectName || project || '').trim() || '未选择客户',
    findingsCount: findings.length,
    clueCount: clues.length,
    p0Count: findings.filter((f: Finding) => f.severity === 'P0').length,
  };
}

export function isCustomerReadyFinding(finding: Finding): boolean {
  if (!finding) {
    return false;
  }

  const deliveryStatus = String(finding.customer_delivery_status || finding.delivery_track || '').trim();
  if (deliveryStatus !== 'defect') return false;

  if (finding.bug_status !== 'reproduced' || !finding.gate_passed) {
    return false;
  }

  // ── evidence_consistency.verdict 门控 ──
  // 证据一致性审查未通过的不能进入客户交付
  const evidenceConsistency = (finding as any).evidence_consistency;
  if (evidenceConsistency && typeof evidenceConsistency === 'object') {
    const verdict = String(evidenceConsistency.verdict || '').toLowerCase();
    if (verdict === 'rejected' || verdict === 'missing') {
      return false;
    }
  }

  // ── 阻断类型过滤：这些是内部诊断状态，不是客户可交付缺陷 ──
  const blockedKeywords = ['route_blocked', 'auth_blocked', 'environment_blocked',
    'coverage_gap', 'validation_lead', 'not_reproduced'];
  const valueLane = String((finding as any).value_lane || (finding as any)._value_lane || '').toLowerCase();
  const blockReason = String((finding as any).execution_block || (finding as any).block_reason || '').toLowerCase();
  const combined = `${valueLane} ${blockReason}`;
  if (blockedKeywords.some(kw => combined.includes(kw))) {
    return false;
  }

  if (finding.reproduction?.is_synthetic) {
    return false;
  }

  return hasRealReplayAsset(finding) && hasCustomerFacingHardEvidence(finding);
}

export function hasCustomerFacingHardEvidence(finding: Finding): boolean {
  if (!finding) return false;
  const repro = finding.reproduction || { method: '', path: '', steps: [], curl_command: '', is_synthetic: false };
  const raw = finding.raw_evidence;
  const hasRuntimeEvidence = Boolean(repro.har_evidence?.status_code || repro.har_evidence?.response_body);
  const hasRawEvidence = Boolean(
    raw?.has_real_evidence
    || raw?.response_raw?.status_code
    || raw?.response_raw?.body
    || raw?.db_snapshot?.table
    || raw?.logs?.trace_id
    || raw?.execution_trace?.evidence_hash
  );
  return hasRawEvidence || hasRuntimeEvidence;
}

export function hasRealReplayAsset(finding: Finding): boolean {
  if (!finding) return false;
  const repro = finding.reproduction || { method: '', path: '', steps: [], curl_command: '', is_synthetic: false };
  const eq = finding.evidence_quality || { can_reproduce: false };
  const hasHarReplay = Boolean(repro.har_evidence?.status_code || repro.har_evidence?.response_body);
  return Boolean(
    eq.can_reproduce
    && !repro.is_synthetic
    && repro.method
    && repro.path
    && hasHarReplay
  );
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
  useEffect(() => { refresh(); }, [refresh]);
  return { workspaces, workspaceOptions: toWorkspaceOptions(workspaces), loadError, refresh };
}

export function useProjectSummary(project: string) {
  const [summary, setSummary] = useState<ProjectSummary>({
    resolvedProjectId: '',
    projectName: project || '未选择客户',
    findingsCount: 0,
    clueCount: 0,
    p0Count: 0,
  });
  const [loading, setLoading] = useState(true);
  const load = useCallback(() => {
    if (!project) {
      setSummary({ resolvedProjectId: '', projectName: '未选择客户', findingsCount: 0, clueCount: 0, p0Count: 0 });
      setLoading(false);
      return;
    }
    setLoading(true);
    getFindings(project)
      .then((raw: any) => { setSummary(buildProjectSummary(raw, project)); setLoading(false); })
      .catch(() => { setSummary({ resolvedProjectId: '', projectName: '未选择客户', findingsCount: 0, clueCount: 0, p0Count: 0 }); setLoading(false); });
  }, [project]);
  useEffect(() => { load(); }, [load]);
  useScanCompletedRefresh(project, load);
  return { ...summary, hasResolvedProject: Boolean(summary.resolvedProjectId || project), loading };
}

/**
 * usePipelineData — pure passthrough. No computation, just fetch + return.
 */
export function usePipelineData(project: string) {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const load = useCallback(() => {
    setLoading(true);
    setError('');
    setData(null);
    getFindings(project)
      .then((raw: any) => { setData(raw); setLoading(false); })
      .catch((err: Error) => { setError(err.message); setLoading(false); });
  }, [project]);
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError('');
    setData(null);
    getFindings(project)
      .then((raw: any) => { if (!cancelled) { setData(raw); setLoading(false); } })
      .catch((err: Error) => { if (!cancelled) { setError(err.message); setLoading(false); } });
    return () => { cancelled = true; };
  }, [project]);
  useScanCompletedRefresh(project, load);
  return { data, loading, error, refetch: load };
}

/**
 * useFindingsData — pure passthrough. Returns display-ready findings from backend.
 */
export function useFindingsData(project: string) {
  const [findings, setFindings] = useState<Finding[]>([]);
  const [clues, setClues] = useState<Finding[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const load = useCallback(() => {
    setLoading(true);
    setError('');
    getFindings(project)
      .then((raw: any) => {
        setFindings(getReportFindings(raw));
        setClues(getReportClues(raw));
        setLoading(false);
      })
      .catch((err: Error) => {
        setFindings([]);
        setClues([]);
        setError(err.message);
        setLoading(false);
      });
  }, [project]);
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError('');
    setFindings([]);
    setClues([]);
    getFindings(project)
      .then((raw: any) => {
        if (!cancelled) {
          setFindings(getReportFindings(raw));
          setClues(getReportClues(raw));
          setLoading(false);
        }
      })
      .catch((err: Error) => {
        if (!cancelled) {
          setFindings([]);
          setClues([]);
          setError(err.message);
          setLoading(false);
        }
      });
    return () => { cancelled = true; };
  }, [project]);
  useScanCompletedRefresh(project, load);
  return { findings, clues, loading, error, refetch: load };
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

export function useKnowledgeData(project: string) {
  const [sources, setSources] = useState<KnowledgeSource[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const load = useCallback(() => {
    setLoading(true);
    setSources([]);
    setError('');
    getKnowledgeAsset(project)
      .then((raw: any) => { setSources(parseKnowledgeSources(raw)); setLoading(false); })
      .catch((err: Error) => { setError(err.message || '资料列表加载失败'); setLoading(false); });
  }, [project]);
  useEffect(() => { load(); }, [load]);
  return { sources, loading, error, refetch: load };
}

function parseReleaseChecks(raw: any): { overall: 'pass' | 'fail'; checks: ReleaseCheck[] } {
  const findings = getReportFindings(raw);
  const customerReadyFindings = findings.filter(isCustomerReadyFinding);
  const p0 = customerReadyFindings.filter((f) => f.severity === 'P0').length;
  const securityFindings = customerReadyFindings.filter((f) => f.defect_family === 'security_boundary' || f.defect_family === 'privacy_compliance');
  const dataIntegrityFindings = customerReadyFindings.filter((f) => f.defect_family === 'data_integrity');
  const dbConfirmed = asFiniteNumber(raw?.db_verification?.confirmed);

  const checks: ReleaseCheck[] = [
    { name: 'P0 缺陷阻塞', status: p0 === 0 ? 'pass' : 'fail', detail: p0 === 0 ? '无 P0 缺陷' : `${p0} 个 P0 缺陷未修复` },
    { name: '认证授权检测', status: securityFindings.length === 0 ? 'pass' : 'fail', detail: securityFindings.length === 0 ? '未发现认证授权类缺陷' : `${securityFindings.length} 个安全类缺陷待修复` },
    { name: '数据完整性校验', status: dataIntegrityFindings.length === 0 ? 'pass' : 'fail', detail: dataIntegrityFindings.length === 0 ? '未发现数据一致性缺陷' : `${dataIntegrityFindings.length} 个数据完整性缺陷待修复` },
    { name: 'DB 验证', status: dbConfirmed === 0 ? 'pass' : 'fail', detail: dbConfirmed === 0 ? 'DB 一致性检查通过' : `${dbConfirmed} 个 DB 不一致` },
  ];
  return { overall: p0 > 0 ? 'fail' : 'pass', checks };
}

export function useReleaseData(project: string) {
  const [data, setData] = useState<{ overall: 'pass' | 'fail'; checks: ReleaseCheck[] } | null>(null);
  const [loading, setLoading] = useState(true);
  const load = useCallback(() => {
    if (!project) { setData(null); setLoading(false); return; }
    setLoading(true);
    getFindings(project)
      .then((raw: any) => {
        const resolvedProjectId = getResolvedProjectId(raw);
        const status = String(raw?.status || raw?.live_map?.status || '').trim();
        if (!resolvedProjectId || !status || status === 'idle') { setData(null); setLoading(false); return; }
        setData(parseReleaseChecks(raw));
        setLoading(false);
      })
      .catch(() => { setData(null); setLoading(false); });
  }, [project]);
  useEffect(() => { load(); }, [load]);
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
      setLastScanMinutes(null); setScanActive(false); setHasMaterializedMetrics(false);
      setHasResolvedProject(false); setContinuousActive(false);
      return;
    }
    getFindings(project)
      .then((raw: any) => {
        const resolvedProjectId = getResolvedProjectId(raw);
        const completedAt = getCompletedAt(raw);
        if (completedAt) setLastScanMinutes(Math.round((Date.now() - new Date(completedAt).getTime()) / 60000));
        else setLastScanMinutes(null);
        setHasResolvedProject(Boolean(resolvedProjectId || project));
        setScanActive(raw?.status === 'running' || raw?.live_map?.status === 'running');
        setHasMaterializedMetrics(Boolean(resolvedProjectId || project) && hasMaterializedFindingData(raw));
        setContinuousActive(isContinuousDiscoveryActive(raw));
      })
      .catch(() => {
        setLastScanMinutes(null); setScanActive(false); setHasMaterializedMetrics(false);
        setHasResolvedProject(false); setContinuousActive(false);
      });
  }, [project]);
  useEffect(() => {
    check();
    const timer = setInterval(check, intervalMs);
    return () => clearInterval(timer);
  }, [check, intervalMs]);
  useScanCompletedRefresh(project, check);
  return { lastScanMinutes, scanActive, hasMaterializedMetrics, hasResolvedProject, continuousActive };
}
