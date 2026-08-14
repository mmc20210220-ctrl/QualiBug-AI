import { useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import { useLiveStatus, useProjectSummary, useWorkspaceDirectory } from '../api/data';
import { logout } from '../api/client';
import { formatCustomerName } from '../lib/customer';
import { useProjectNavigation } from '../lib/project-navigation';

const pageLabels: Record<string, string> = {
  '/dashboard': '价值总览',
  '/findings': '问题清单',
  '/clues': '内部工作台',
  '/evidence': '证据链',
  '/behavior-space': '行为空间',
  '/materials': '企业资料',
  '/campaigns': '运行中心',
  '/release': '发布门禁',
  '/settings': '系统与环境',
  '/products': '产品矩阵',
  '/coverage': '覆盖矩阵',
  '/test-tasks': '测试任务',
  '/jobs': '后台任务',
};

type TopbarProps = { navOpen?: boolean; onToggleNav?: () => void };

export function Topbar({ navOpen = false, onToggleNav }: TopbarProps) {
  const [params] = useSearchParams(); const location = useLocation(); const navigate = useNavigate();
  const project = params.get('project')?.trim() || '';
  const { switchProject, navigateToProjectPath } = useProjectNavigation(); const { workspaceOptions } = useWorkspaceDirectory(); const { projectName } = useProjectSummary(project);
  const [showTenantMenu, setShowTenantMenu] = useState(false); const tenantMenuRef = useRef<HTMLDivElement | null>(null);
  const { lastScanMinutes, scanActive, hasMaterializedMetrics, hasResolvedProject, continuousActive } = useLiveStatus(project, 15000);
  const currentPage = pageLabels[location.pathname] || '成果总览'; const isProductsPage = location.pathname === '/products';
  const resolvedWorkspaceName = workspaceOptions.find((item) => item.id === project)?.label || '';
  const customerButtonName = project ? resolvedWorkspaceName || formatCustomerName(projectName) || formatCustomerName(project) : workspaceOptions.length === 1 ? workspaceOptions[0].label : '待选择';
  const hasSelectedCustomer = Boolean(project); const minutesDisplay = lastScanMinutes !== null ? (lastScanMinutes < 1 ? '刚刚' : `${lastScanMinutes} 分钟前`) : '--';
  const statusText = isProductsPage ? '版本已同步' : !hasSelectedCustomer ? '待选择客户' : scanActive ? '检测进行中' : hasMaterializedMetrics ? (continuousActive ? '持续守护中' : `结果已同步 · ${minutesDisplay}`) : '暂无结果';
  const dotTone = isProductsPage ? 'success' : !hasSelectedCustomer ? 'muted' : scanActive ? 'warning' : hasMaterializedMetrics ? 'success' : 'muted';

  useEffect(() => {
    if (!showTenantMenu) return;
    const closeWhenOutside = (event: MouseEvent) => { if (!tenantMenuRef.current?.contains(event.target as Node)) setShowTenantMenu(false); };
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === 'Escape') setShowTenantMenu(false); };
    document.addEventListener('mousedown', closeWhenOutside);
    document.addEventListener('keydown', closeOnEscape);
    return () => {
      document.removeEventListener('mousedown', closeWhenOutside);
      document.removeEventListener('keydown', closeOnEscape);
    };
  }, [showTenantMenu]);

  return <>
    <header className="topbar">
      <div className="topbar-left">
        <button type="button" className={`nav-toggle${navOpen ? ' active' : ''}`} onClick={onToggleNav} aria-label={navOpen ? '收起导航' : '展开导航'} aria-expanded={navOpen} aria-controls="primary-sidebar"><span /><span /><span /></button>
        <div className="topbar-title-group"><span className="breadcrumb">QualiBug AI <b>/ {currentPage}</b></span><span className="topbar-subtitle">{isProductsPage ? '产品策略与版本路径' : hasSelectedCustomer ? customerButtonName : '把风险变成可验收结论'}</span></div>
      </div>
      <div className="topbar-right">
        <span className={`system-status ${isProductsPage || (!project || !hasResolvedProject || scanActive || !hasMaterializedMetrics) ? '' : 'online'}${isProductsPage ? ' online' : ''}`}><span className={`system-status-dot tone-${dotTone}`} />{statusText}</span>
        {hasSelectedCustomer && <button type="button" className="btn btn-primary topbar-run-btn" onClick={() => navigateToProjectPath('/campaigns', project)}><span className="topbar-run-btn-icon" aria-hidden="true">&gt;</span>进入运行中心</button>}
        <div className="tenant-switcher" ref={tenantMenuRef}>
          <button type="button" className={`tenant-button${showTenantMenu ? ' open' : ''}`} onClick={() => setShowTenantMenu((value) => !value)} aria-haspopup="menu" aria-expanded={showTenantMenu} aria-controls="tenant-switcher-menu"><span className="tenant-button-label">客户</span><strong>{customerButtonName}</strong><span className="tenant-button-caret" aria-hidden="true">▾</span></button>
          {showTenantMenu && <div id="tenant-switcher-menu" className="tenant-menu" role="menu" aria-label="切换客户工作区">{workspaceOptions.length > 0 ? workspaceOptions.map((workspace) => <button key={workspace.id} type="button" role="menuitem" className={`tenant-option${workspace.id === project ? ' is-active' : ''}`} onClick={() => { switchProject(workspace.id); setShowTenantMenu(false); }}><span className="tenant-option-copy"><span className="tenant-option-label">{workspace.label}</span><span className="tenant-option-meta">切换当前客户工作区</span></span>{workspace.id === project && <span className="tenant-option-check">当前</span>}</button>) : <button type="button" className="tenant-option" disabled><span className="tenant-option-copy"><span className="tenant-option-label">暂无客户</span><span className="tenant-option-meta">请先在设置页新建或导入客户</span></span></button>}</div>}
        </div>
        <button type="button" className="btn btn-secondary topbar-logout-btn" onClick={() => { logout(); navigate(`/login?next=${encodeURIComponent(`${location.pathname}${location.search}`)}`, { replace: true }); }}>退出</button>
      </div>
    </header>
  </>;
}
