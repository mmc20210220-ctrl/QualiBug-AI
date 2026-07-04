import { useState, useEffect, useRef } from 'react';
import { useSearchParams, useLocation, useNavigate } from 'react-router-dom';
import { emitScanCompleted, useLiveStatus, useProjectSummary, useWorkspaceDirectory } from '../api/data';
import { getFindings, logout, runV12Scan, type V12ScanResult } from '../api/client';
import { formatCustomerName } from '../lib/customer';
import { useProjectNavigation } from '../lib/project-navigation';

const pageLabels: Record<string, string> = {
  '/dashboard': '风险总览',
  '/findings': '行为验证',
  '/evidence': '证据链',
  '/behavior-space': '行为空间',
  '/materials': '企业资料',
  '/release': '发布门禁',
  '/settings': '设置',
  '/products': '产品矩阵',
};

type TopbarProps = {
  navOpen?: boolean;
  onToggleNav?: () => void;
};

export function Topbar({ navOpen = false, onToggleNav }: TopbarProps) {
  const [params] = useSearchParams();
  const location = useLocation();
  const navigate = useNavigate();
  const project = params.get('project')?.trim() || '';
  const { switchProject } = useProjectNavigation();
  const { workspaceOptions } = useWorkspaceDirectory();
  const { projectName } = useProjectSummary(project);
  const [showTenantMenu, setShowTenantMenu] = useState(false);
  const tenantMenuRef = useRef<HTMLDivElement | null>(null);
  const { lastScanMinutes, scanActive, hasMaterializedMetrics, hasResolvedProject, continuousActive } = useLiveStatus(project, 15000);
  const [scanLoad, setScanLoad] = useState(false);
  const [scanRes, setScanRes] = useState<V12ScanResult | null>(null);
  const [showResult, setShowResult] = useState(false);

  const currentPage = pageLabels[location.pathname] || '风险总览';
  const isProductsPage = location.pathname === '/products';
  const resolvedWorkspaceName = workspaceOptions.find((item) => item.id === project)?.label || '';
  const customerButtonName = project
    ? resolvedWorkspaceName || formatCustomerName(projectName) || formatCustomerName(project)
    : workspaceOptions.length === 1
      ? workspaceOptions[0].label
      : '待选择';
  const hasSelectedCustomer = Boolean(project);
  const minutesDisplay = lastScanMinutes !== null
    ? (lastScanMinutes < 1 ? '刚刚' : `${lastScanMinutes} 分钟前`)
    : '--';
  const statusText = isProductsPage
    ? '版本策略已同步'
    : !hasSelectedCustomer
    ? '未选择客户'
    : scanActive
      ? '检测执行中'
      : hasMaterializedMetrics
        ? (continuousActive ? '持续检测中 · 自动运行' : `状态已同步 · 最近扫描 ${minutesDisplay}`)
        : '已选择客户 · 暂无真实数据';
  const dotTone = isProductsPage ? 'success' : !hasSelectedCustomer ? 'muted' : scanActive ? 'warning' : hasMaterializedMetrics ? 'success' : 'muted';

  useEffect(() => {
    if (!showTenantMenu) return;

    const handlePointerDown = (event: MouseEvent) => {
      if (!tenantMenuRef.current?.contains(event.target as Node)) {
        setShowTenantMenu(false);
      }
    };

    document.addEventListener('mousedown', handlePointerDown);
    return () => document.removeEventListener('mousedown', handlePointerDown);
  }, [showTenantMenu]);

  return (
    <>
      <header className="topbar">
        <div className="topbar-left">
          <button
            type="button"
            className={`nav-toggle${navOpen ? ' active' : ''}`}
            onClick={onToggleNav}
            aria-label={navOpen ? '收起导航' : '展开导航'}
          >
            <span />
            <span />
            <span />
          </button>
          <div className="topbar-title-group">
            <span className="breadcrumb">QualiBug AI <b>/ {currentPage}</b></span>
            <span className="topbar-subtitle">
              {isProductsPage
                ? '企业级产品策略与版本路径'
                : hasSelectedCustomer
                  ? customerButtonName
                  : '企业级行为风险决策界面'}
            </span>
          </div>
        </div>
        <div className="topbar-right">
          <span className={`system-status ${isProductsPage || (!project || !hasResolvedProject || scanActive || !hasMaterializedMetrics) ? '' : 'online'}${isProductsPage ? ' online' : ''}`}>
            <span className={`system-status-dot tone-${dotTone}`} />
            {statusText}
          </span>
          {hasSelectedCustomer && (
            <button
              type="button"
              className={`btn btn-primary topbar-run-btn${scanLoad ? ' is-loading' : ''}`}
              disabled={scanLoad}
              onClick={async () => {
                setScanLoad(true); setScanRes(null); setShowResult(false);
                try {
                  const r = await runV12Scan(project);
                  setScanRes(r);
                  setShowResult(true);
                  if (r.ok) {
                    emitScanCompleted(project);
                    getFindings(project)
                      .then((raw) => {
                        const es = (raw?.executiveSummary || {}) as Record<string, unknown>;
                        setScanRes((prev) => prev
                          ? {
                              ...prev,
                              total_findings: Number(es['totalFindings'] || es['totalBugsFound'] || prev.total_findings || 0),
                              grade: String(es['systemGrade'] || prev.grade || ''),
                              score: Number(es['overallScore'] ?? prev.score ?? 0),
                            }
                          : prev);
                      })
                      .catch(() => {});
                  }
                }
                catch (error: unknown) {
                  setScanRes({
                    ok: false,
                    error: error instanceof Error ? error.message : 'scan failed',
                  });
                  setShowResult(true);
                }
                finally { setScanLoad(false); }
              }}
            >
              <span className="topbar-run-btn-icon" aria-hidden="true">{scanLoad ? '···' : '>'}</span>
              {scanLoad ? '检测中' : '运行检测'}
            </button>
          )}
          {/* Customer selector — always visible */}
          <div className="tenant-switcher" ref={tenantMenuRef}>
              <button
                type="button"
                className={`tenant-button${showTenantMenu ? ' open' : ''}`}
                onClick={() => setShowTenantMenu(!showTenantMenu)}
                aria-haspopup="menu"
                aria-expanded={showTenantMenu}
              >
                <span className="tenant-button-label">客户</span>
                <strong>{customerButtonName}</strong>
                <span className="tenant-button-caret" aria-hidden="true">▾</span>
              </button>
              {showTenantMenu && (
                <div className="tenant-menu" role="menu">
                  {workspaceOptions.length > 0 ? workspaceOptions.map((workspace) => (
                    <button
                      key={workspace.id}
                      type="button"
                      role="menuitem"
                      className={`tenant-option${workspace.id === project ? ' is-active' : ''}`}
                      onClick={() => { switchProject(workspace.id); setShowTenantMenu(false); }}
                    >
                      <span className="tenant-option-copy">
                        <span className="tenant-option-label">{workspace.label}</span>
                        <span className="tenant-option-meta">切换当前客户工作区</span>
                      </span>
                      {workspace.id === project && <span className="tenant-option-check">当前</span>}
                    </button>
                  )) : (
                    <button type="button" className="tenant-option" disabled>
                      <span className="tenant-option-copy">
                        <span className="tenant-option-label">暂无客户</span>
                        <span className="tenant-option-meta">请先在设置页新建或导入客户</span>
                      </span>
                    </button>
                  )}
                </div>
              )}
            </div>
          <button
            type="button"
            className="btn btn-secondary topbar-logout-btn"
            onClick={() => {
              logout();
              navigate(`/login?next=${encodeURIComponent(`${location.pathname}${location.search}`)}`, { replace: true });
            }}
          >
            退出
          </button>
        </div>
      </header>
      {showResult && scanRes?.ok && (
        <div className="scan-result-card">
          <div className="scan-result-card-head">
            <div>
              <div className="scan-result-card-title">检测已完成</div>
              <div className="scan-result-card-subtitle">本次结果已同步到全局面板</div>
            </div>
            <button type="button" className="scan-result-card-close" onClick={() => setShowResult(false)} aria-label="关闭结果卡片">×</button>
          </div>
          <div className="scan-result-grid">
            <div className="scan-result-metric">
              <div className="scan-result-label">评级</div>
              <div className="scan-result-value">
                {scanRes.grade}
                <span className="scan-result-note">
                  {scanRes.score?.toFixed(0)}/100
                </span>
              </div>
            </div>
            <div className="scan-result-metric">
              <div className="scan-result-label">覆盖率</div>
              <div className="scan-result-value">
                {((scanRes.coverage ?? 0) * 100).toFixed(0)}%
              </div>
            </div>
            <div className="scan-result-metric is-danger">
              <div className="scan-result-label">发现问题</div>
              <div className="scan-result-value">{scanRes.total_findings}</div>
            </div>
            <div className="scan-result-metric">
              <div className="scan-result-label">耗时</div>
              <div className="scan-result-value">{scanRes.total_ms}ms</div>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
