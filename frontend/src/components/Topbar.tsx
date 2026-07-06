import { useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import { emitScanCompleted, useLiveStatus, useProjectSummary, useWorkspaceDirectory } from '../api/data';
import { getFindings, logout, runV12Scan, type V12ScanResult } from '../api/client';
import { formatCustomerName } from '../lib/customer';
import { useProjectNavigation } from '../lib/project-navigation';

const pageLabels: Record<string, string> = {
  '/dashboard': '风险总览', '/findings': '行为验证', '/clues': '待验证线索', '/evidence': '证据链', '/behavior-space': '行为空间', '/materials': '企业资料', '/release': '发布门禁', '/settings': '设置', '/products': '产品矩阵',
};

type TopbarProps = { navOpen?: boolean; onToggleNav?: () => void };
type JsonRecord = Record<string, unknown>;

function wait(ms: number): Promise<void> { return new Promise((resolve) => window.setTimeout(resolve, ms)); }
function asRecord(value: unknown): JsonRecord { return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as JsonRecord : {}; }
function asNumber(value: unknown, fallback = 0): number { const parsed = typeof value === 'number' ? value : Number(value); return Number.isFinite(parsed) ? parsed : fallback; }
function asString(value: unknown): string { return typeof value === 'string' ? value : ''; }

function mergeScanResult(previous: V12ScanResult | null, raw: unknown): V12ScanResult | null {
  if (!previous) return previous;
  const record = asRecord(raw); const executive = asRecord(record.executive_summary); const meta = asRecord(record.scan_meta);
  return {
    ...previous,
    scan_id: asString(meta.scan_id) || previous.scan_id || '',
    total_findings: asNumber(executive.total_findings || executive.total_bugs_found || meta.total_findings || previous.total_findings),
    grade: asString(meta.grade) || asString(executive.system_grade) || previous.grade || '',
    score: asNumber(meta.score || executive.overall_score || previous.score),
    total_ms: asNumber(meta.total_ms || previous.total_ms),
  };
}

export function Topbar({ navOpen = false, onToggleNav }: TopbarProps) {
  const [params] = useSearchParams(); const location = useLocation(); const navigate = useNavigate();
  const project = params.get('project')?.trim() || '';
  const { switchProject } = useProjectNavigation(); const { workspaceOptions } = useWorkspaceDirectory(); const { projectName } = useProjectSummary(project);
  const [showTenantMenu, setShowTenantMenu] = useState(false); const tenantMenuRef = useRef<HTMLDivElement | null>(null);
  const { lastScanMinutes, scanActive, hasMaterializedMetrics, hasResolvedProject, continuousActive } = useLiveStatus(project, 15000);
  const [scanLoad, setScanLoad] = useState(false); const [scanRes, setScanRes] = useState<V12ScanResult | null>(null); const [showResult, setShowResult] = useState(false);
  const currentPage = pageLabels[location.pathname] || '风险总览'; const isProductsPage = location.pathname === '/products';
  const resolvedWorkspaceName = workspaceOptions.find((item) => item.id === project)?.label || '';
  const customerButtonName = project ? resolvedWorkspaceName || formatCustomerName(projectName) || formatCustomerName(project) : workspaceOptions.length === 1 ? workspaceOptions[0].label : '待选择';
  const hasSelectedCustomer = Boolean(project); const minutesDisplay = lastScanMinutes !== null ? (lastScanMinutes < 1 ? '刚刚' : `${lastScanMinutes} 分钟前`) : '--';
  const statusText = isProductsPage ? '版本已同步' : !hasSelectedCustomer ? '待选择客户' : scanActive ? '检测中' : hasMaterializedMetrics ? (continuousActive ? '持续检测中' : `已同步 · ${minutesDisplay}`) : '暂无结果';
  const dotTone = isProductsPage ? 'success' : !hasSelectedCustomer ? 'muted' : scanActive ? 'warning' : hasMaterializedMetrics ? 'success' : 'muted';

  useEffect(() => {
    if (!showTenantMenu) return;
    const closeWhenOutside = (event: MouseEvent) => { if (!tenantMenuRef.current?.contains(event.target as Node)) setShowTenantMenu(false); };
    document.addEventListener('mousedown', closeWhenOutside);
    return () => document.removeEventListener('mousedown', closeWhenOutside);
  }, [showTenantMenu]);

  const runScan = async (): Promise<void> => {
    setScanLoad(true); setScanRes(null); setShowResult(false);
    try {
      const before: JsonRecord = await getFindings(project).catch((): JsonRecord => ({})); const beforeMeta = asRecord(before.scan_meta);
      const beforeRun = asNumber(beforeMeta.run_count); const beforeUpdatedAt = asString(beforeMeta.last_scan_at) || asString(before.updated_at);
      const response = await runV12Scan(project); setScanRes(response); setShowResult(true);
      if (!response.ok) return;
      for (let attempt = 0; attempt < 8; attempt += 1) {
        await wait(attempt === 0 ? 250 : 1000);
        const raw = await getFindings(project).catch(() => null); if (!raw) continue;
        const meta = asRecord(raw.scan_meta); const nextRun = asNumber(meta.run_count); const nextUpdatedAt = asString(meta.last_scan_at) || asString(raw.updated_at);
        setScanRes((previous) => mergeScanResult(previous, raw)); emitScanCompleted(project);
        if ((nextRun && nextRun > beforeRun) || (nextUpdatedAt && nextUpdatedAt !== beforeUpdatedAt)) break;
      }
    } catch (error: unknown) {
      setScanRes({ ok: false, error: error instanceof Error ? error.message : 'scan failed' }); setShowResult(true);
    } finally { setScanLoad(false); }
  };

  return <>
    <header className="topbar">
      <div className="topbar-left">
        <button type="button" className={`nav-toggle${navOpen ? ' active' : ''}`} onClick={onToggleNav} aria-label={navOpen ? '收起导航' : '展开导航'}><span /><span /><span /></button>
        <div className="topbar-title-group"><span className="breadcrumb">QualiBug AI <b>/ {currentPage}</b></span><span className="topbar-subtitle">{isProductsPage ? '产品策略与版本路径' : hasSelectedCustomer ? customerButtonName : '行为风险决策台'}</span></div>
      </div>
      <div className="topbar-right">
        <span className={`system-status ${isProductsPage || (!project || !hasResolvedProject || scanActive || !hasMaterializedMetrics) ? '' : 'online'}${isProductsPage ? ' online' : ''}`}><span className={`system-status-dot tone-${dotTone}`} />{statusText}</span>
        {hasSelectedCustomer && <button type="button" className={`btn btn-primary topbar-run-btn${scanLoad ? ' is-loading' : ''}`} disabled={scanLoad} onClick={() => void runScan()}><span className="topbar-run-btn-icon" aria-hidden="true">{scanLoad ? '···' : '>'}</span>{scanLoad ? '检测中' : '运行检测'}</button>}
        <div className="tenant-switcher" ref={tenantMenuRef}>
          <button type="button" className={`tenant-button${showTenantMenu ? ' open' : ''}`} onClick={() => setShowTenantMenu((value) => !value)} aria-haspopup="menu" aria-expanded={showTenantMenu}><span className="tenant-button-label">客户</span><strong>{customerButtonName}</strong><span className="tenant-button-caret" aria-hidden="true">▾</span></button>
          {showTenantMenu && <div className="tenant-menu" role="menu">{workspaceOptions.length > 0 ? workspaceOptions.map((workspace) => <button key={workspace.id} type="button" role="menuitem" className={`tenant-option${workspace.id === project ? ' is-active' : ''}`} onClick={() => { switchProject(workspace.id); setShowTenantMenu(false); }}><span className="tenant-option-copy"><span className="tenant-option-label">{workspace.label}</span><span className="tenant-option-meta">切换当前客户工作区</span></span>{workspace.id === project && <span className="tenant-option-check">当前</span>}</button>) : <button type="button" className="tenant-option" disabled><span className="tenant-option-copy"><span className="tenant-option-label">暂无客户</span><span className="tenant-option-meta">请先在设置页新建或导入客户</span></span></button>}</div>}
        </div>
        <button type="button" className="btn btn-secondary topbar-logout-btn" onClick={() => { logout(); navigate(`/login?next=${encodeURIComponent(`${location.pathname}${location.search}`)}`, { replace: true }); }}>退出</button>
      </div>
    </header>
    {showResult && scanRes?.ok && <div className="scan-result-card"><div className="scan-result-card-head"><div><div className="scan-result-card-title">检测已完成</div><div className="scan-result-card-subtitle">本次结果已同步到全局面板</div></div><button type="button" className="scan-result-card-close" onClick={() => setShowResult(false)} aria-label="关闭结果卡片">×</button></div><div className="scan-result-grid"><div className="scan-result-metric"><div className="scan-result-label">评级</div><div className="scan-result-value">{scanRes.grade}<span className="scan-result-note">{scanRes.score?.toFixed(0)}/100</span></div></div><div className="scan-result-metric"><div className="scan-result-label">覆盖率</div><div className="scan-result-value">{((scanRes.coverage ?? 0) * 100).toFixed(0)}%</div></div><div className="scan-result-metric is-danger"><div className="scan-result-label">发现问题</div><div className="scan-result-value">{scanRes.total_findings}</div></div><div className="scan-result-metric"><div className="scan-result-label">耗时</div><div className="scan-result-value">{scanRes.total_ms}ms</div></div></div></div>}
  </>;
}
