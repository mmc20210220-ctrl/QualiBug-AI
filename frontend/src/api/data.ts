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
  p0Count: number;
};

function getResolvedProjectId(raw: any): string {
  return String(raw?.resolvedProjectId || raw?.projectId || '').trim();
}

function getReportFindings(raw: any): Finding[] {
  const risks = raw?.risks;
  return Array.isArray(risks) ? risks as Finding[] : [];
}

function getCompletedAt(raw: any): string {
  return String(raw?.updatedAt || raw?.updated_at || '').trim();
}

function hasMaterializedFindingData(raw: any): boolean {
  const findings = getReportFindings(raw);
  const exec = raw?.executive_summary || {};
  const totalBugs = Number(exec.total_bugs_found || exec.total_findings || 0);
  const runtimeConfirmed = Number(raw?.runtime_verification?.confirmed || 0);
  const dbConfirmed = Number(raw?.db_verification?.confirmed || 0);
  return findings.length > 0 || totalBugs > 0 || runtimeConfirmed > 0 || dbConfirmed > 0;
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
  const exec = raw?.executive_summary || {};
  const canonicalTotal = Number(exec.total_bugs_found || exec.total_findings || findings.length || 0);
  const canonicalP0 = Number(exec.critical_bugs || findings.filter((f: Finding) => f.severity === 'P0').length || 0);
  return {
    resolvedProjectId,
    projectName: String(raw?.project_name || raw?.projectName || project || '').trim() || '未选择客户',
    findingsCount: canonicalTotal,
    p0Count: canonicalP0,
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
  useEffect(() => { refresh(); }, [refresh]);
  return { workspaces, workspaceOptions: toWorkspaceOptions(workspaces), loadError, refresh };
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
      setSummary({ resolvedProjectId: '', projectName: '未选择客户', findingsCount: 0, p0Count: 0 });
      setLoading(false);
      return;
    }
    setLoading(true);
    getFindings(project)
      .then((raw: any) => { setSummary(buildProjectSummary(raw, project)); setLoading(false); })
      .catch(() => { setSummary({ resolvedProjectId: '', projectName: '未选择客户', findingsCount: 0, p0Count: 0 }); setLoading(false); });
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
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const load = useCallback(() => {
    setLoading(true);
    setError('');
    getFindings(project)
      .then((raw: any) => { setFindings(getReportFindings(raw)); setLoading(false); })
      .catch((err: Error) => { setError(err.message); setLoading(false); });
  }, [project]);
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError('');
    setFindings([]);
    getFindings(project)
      .then((raw: any) => { if (!cancelled) { setFindings(getReportFindings(raw)); setLoading(false); } })
      .catch((err: Error) => { if (!cancelled) { setError(err.message); setLoading(false); } });
    return () => { cancelled = true; };
  }, [project]);
  useScanCompletedRefresh(project, load);
  return { findings, loading, error, refetch: load };
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
  const p0 = findings.filter((f) => f.severity === 'P0').length;
  const securityFindings = findings.filter((f) => f.defect_family === 'security_boundary' || f.defect_family === 'privacy_compliance');
  const dataIntegrityFindings = findings.filter((f) => f.defect_family === 'data_integrity');
  const dbConfirmed = Number(raw?.db_verification?.confirmed || 0);

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
