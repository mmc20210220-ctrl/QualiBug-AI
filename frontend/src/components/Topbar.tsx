import { useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import { useLiveStatus, useWorkspaceDirectory } from '../api/data';
import { logout } from '../api/client';
import { useProjectNavigation } from '../lib/project-navigation';

const pageLabels: Record<string, { title: string; subtitle: string }> = {
  '/dashboard': { title: 'New Task', subtitle: '告诉 QualiBug 你想验证什么' },
  '/verify': { title: 'Live Workspace', subtitle: '理解、计划、真实执行、证据与判断' },
  '/findings': { title: 'Findings', subtitle: 'AI 发现并有证据支撑的问题' },
  '/release': { title: 'Decision', subtitle: '基于真实证据形成发布建议' },
  '/analyze': { title: 'Knowledge', subtitle: '需求、业务语义、风险与验证目标' },
  '/integration': { title: 'Sources', subtitle: '连接企业资料、代码与被测系统' },
  '/settings': { title: 'Settings', subtitle: '环境、安全边界与系统配置' },
  '/requirements': { title: 'Requirement Intelligence', subtitle: '高级需求审查视图' },
  '/test-intelligence': { title: 'Test Intelligence', subtitle: '高级验证目标视图' },
  '/evidence': { title: 'Evidence', subtitle: '高级证据视图' },
  '/materials': { title: 'Knowledge Sources', subtitle: '企业资料与来源管理' },
  '/campaigns': { title: 'Run Control', subtitle: '真实运行控制与 Preflight' },
  '/coverage': { title: 'Coverage', subtitle: '高级覆盖视图' },
  '/jobs': { title: 'System Jobs', subtitle: '后台运行诊断' },
  '/advanced-dashboard': { title: 'Advanced Dashboard', subtitle: '高级指标与内部诊断' },
};

const STATUS_PATHS = new Set(['/dashboard', '/verify', '/findings', '/release', '/campaigns', '/coverage']);

type TopbarProps = { navOpen?: boolean; onToggleNav?: () => void };

export function Topbar({ navOpen = false, onToggleNav }: TopbarProps) {
  const [params] = useSearchParams();
  const location = useLocation();
  const navigate = useNavigate();
  const project = params.get('project')?.trim() || '';
  const { switchProject, navigateToProjectPath } = useProjectNavigation();
  const { workspaceOptions } = useWorkspaceDirectory();
  const statusEnabled = STATUS_PATHS.has(location.pathname);
  const { scanActive, hasMaterializedMetrics } = useLiveStatus(statusEnabled ? project : '', 15_000);
  const [showTenantMenu, setShowTenantMenu] = useState(false);
  const tenantMenuRef = useRef<HTMLDivElement | null>(null);

  const page = pageLabels[location.pathname]
    || (location.pathname.startsWith('/findings/')
      ? { title: 'Finding', subtitle: '证据调查与修复后验证' }
      : { title: 'QualiBug', subtitle: 'AI Quality Engineer' });
  const workspaceName = workspaceOptions.find((item) => item.id === project)?.label || project || '待选择';
  const statusText = !project
    ? '未选择客户'
    : !statusEnabled
      ? 'Context ready'
      : scanActive
        ? 'Agent working'
        : hasMaterializedMetrics
          ? 'Evidence synced'
          : 'Ready';
  const statusTone = scanActive ? 'warning' : hasMaterializedMetrics ? 'success' : 'muted';

  useEffect(() => {
    if (!showTenantMenu) return;
    const closeWhenOutside = (event: MouseEvent) => {
      if (!tenantMenuRef.current?.contains(event.target as Node)) setShowTenantMenu(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setShowTenantMenu(false);
    };
    document.addEventListener('mousedown', closeWhenOutside);
    document.addEventListener('keydown', closeOnEscape);
    return () => {
      document.removeEventListener('mousedown', closeWhenOutside);
      document.removeEventListener('keydown', closeOnEscape);
    };
  }, [showTenantMenu]);

  return (
    <header className="topbar agent-topbar">
      <div className="topbar-left">
        <button
          type="button"
          className={`nav-toggle${navOpen ? ' active' : ''}`}
          onClick={onToggleNav}
          aria-label={navOpen ? '收起导航' : '展开导航'}
          aria-expanded={navOpen}
          aria-controls="primary-sidebar"
        >
          <span /><span /><span />
        </button>
        <div className="topbar-title-group">
          <span className="breadcrumb"><b>{page.title}</b></span>
          <span className="topbar-subtitle">{page.subtitle}</span>
        </div>
      </div>

      <div className="topbar-right">
        <span className="system-status">
          <span className={`system-status-dot tone-${statusTone}`} />
          {statusText}
        </span>

        {project && location.pathname !== '/dashboard' && (
          <button
            type="button"
            className="btn btn-primary topbar-run-btn"
            onClick={() => navigateToProjectPath('/dashboard', project)}
          >
            <span className="topbar-run-btn-icon" aria-hidden="true">＋</span>
            新任务
          </button>
        )}

        <div className="tenant-switcher" ref={tenantMenuRef}>
          <button
            type="button"
            className={`tenant-button${showTenantMenu ? ' open' : ''}`}
            onClick={() => setShowTenantMenu((value) => !value)}
            aria-haspopup="menu"
            aria-expanded={showTenantMenu}
            aria-controls="tenant-switcher-menu"
          >
            <span className="tenant-button-label">Workspace</span>
            <strong>{workspaceName}</strong>
            <span className="tenant-button-caret" aria-hidden="true">▾</span>
          </button>
          {showTenantMenu && (
            <div id="tenant-switcher-menu" className="tenant-menu" role="menu" aria-label="切换客户工作区">
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
  );
}
