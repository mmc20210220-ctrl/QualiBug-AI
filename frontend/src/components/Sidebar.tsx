import { NavLink, useLocation, useSearchParams } from 'react-router-dom';
import { useProjectSummary, useWorkspaceDirectory } from '../api/data';
import { BrandLogo } from './BrandLogo';
import { buildProjectPath } from '../lib/project-navigation';

type NavItem = { to: string; icon: string; label: string; badgeKey?: 'findings' };
type NavSection = { label: string; items: NavItem[] };

const sections: NavSection[] = [
  {
    label: 'Agent',
    items: [
      { to: 'dashboard', icon: 'new-task', label: '新任务' },
      { to: 'verify', icon: 'workspace', label: '工作台' },
      { to: 'findings', icon: 'bug', label: 'Findings', badgeKey: 'findings' },
      { to: 'release', icon: 'decision', label: 'Decision' },
    ],
  },
  {
    label: 'Context',
    items: [
      { to: 'analyze', icon: 'knowledge', label: 'Knowledge' },
      { to: 'integration', icon: 'sources', label: 'Sources' },
      { to: 'settings', icon: 'settings', label: 'Settings' },
    ],
  },
];

const COMMAND_CENTER_STATUS_PATHS = new Set([
  '/dashboard',
  '/verify',
  '/findings',
  '/release',
  '/evidence',
  '/campaigns',
  '/coverage',
  '/settings',
]);

const icons: Record<string, string> = {
  'new-task': 'M12 3v18M3 12h18',
  workspace: 'M5 4h14v16H5V4Zm3 4h8M8 12h5m-5 4h8',
  bug: 'M8 2v3m8-3v3M3 8h18M5.5 5.5l1.5 1.5m10 0 1.5-1.5M10 14l-2 3m6-3 2 3M12 12v3',
  decision: 'M12 3 20 6v5c0 5-3.3 8.5-8 10-4.7-1.5-8-5-8-10V6l8-3Zm-3 9 2 2 4-4',
  knowledge: 'M4 5h7v7H4V5Zm9 0h7v7h-7V5ZM4 14h7v5H4v-5Zm10 1 2 2 4-4m-7 6h7',
  sources: 'M5 4h14v4H5V4Zm0 6h14v10H5V10Zm3 3h8M8 16h5',
  settings: 'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8Zm8.2 4a7.6 7.6 0 0 0-.13-1.4l2.04-1.58-2-3.46-2.4.97a7.4 7.4 0 0 0-2.42-1.4L14.93 2h-4l-.37 3.13a7.4 7.4 0 0 0-2.42 1.4l-2.4-.97-2 3.46 2.04 1.58A7.6 7.6 0 0 0 5.8 12c0 .48.05.95.13 1.4l-2.04 1.58 2 3.46 2.4-.97a7.4 7.4 0 0 0 2.42 1.4l.37 3.13h4l.37-3.13a7.4 7.4 0 0 0 2.42-1.4l2.4.97 2 3.46-2.04-1.58c.08-.45.13-.92.13-1.4Z',
};

function SvgIcon({ name }: { name: string }) {
  const d = icons[name] || icons.workspace;
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d={d} /></svg>;
}

type SidebarProps = {
  mobileOpen?: boolean;
  onClose?: () => void;
};

export function Sidebar({ mobileOpen = false, onClose }: SidebarProps) {
  const [params] = useSearchParams();
  const location = useLocation();
  const project = params.get('project')?.trim() || '';
  const statusDataEnabled = COMMAND_CENTER_STATUS_PATHS.has(location.pathname);
  const statusProject = statusDataEnabled ? project : '';
  const { workspaceOptions } = useWorkspaceDirectory();
  const { currentDefectCount, p0Count, error: summaryError } = useProjectSummary(statusProject);
  const workspaceName = workspaceOptions.find((item) => item.id === project)?.label || project || '未选择客户';
  const summaryFaulted = Boolean(statusDataEnabled && project && summaryError);

  return (
    <>
      <div
        className={`sidebar-backdrop${mobileOpen ? ' open' : ''}`}
        onClick={onClose}
        aria-hidden="true"
      />
      <aside id="primary-sidebar" className={`sidebar${mobileOpen ? ' mobile-open' : ''}`} aria-label="主导航">
        <div className="side-brand">
          <button type="button" className="side-close" onClick={onClose} aria-label="关闭导航">×</button>
          <BrandLogo variant="full" detail="compact" tone="dark" size={38} subtitle="AI Quality Engineer" />
        </div>

        <div className="side-project">
          <span className="side-project-label">Workspace</span>
          <b>{workspaceName}</b>
          <p className="side-project-agent-copy">
            {summaryFaulted
              ? '项目状态读取失败'
              : p0Count > 0
                ? `${p0Count} 个阻断问题需要处理`
                : statusDataEnabled && currentDefectCount > 0
                  ? `${currentDefectCount} 个已确认问题`
                  : 'Agent 将按当前上下文工作'}
          </p>
        </div>

        <nav className="side-nav" aria-label="Agent 工作导航">
          {sections.map((section) => (
            <div key={section.label} className="side-section-group">
              <div className="side-section">{section.label}</div>
              {section.items.map((item) => {
                const badge = statusDataEnabled && item.badgeKey === 'findings' && !summaryFaulted
                  ? currentDefectCount || 0
                  : undefined;
                const badgeAlert = item.badgeKey === 'findings' && p0Count > 0;
                return (
                  <NavLink
                    key={item.to}
                    to={buildProjectPath(`/${item.to}`, project)}
                    className={({ isActive }) => `side-link${isActive ? ' active' : ''}`}
                    onClick={onClose}
                  >
                    <SvgIcon name={item.icon} />
                    {item.label}
                    {badge != null && badge > 0 && (
                      <span className={`side-badge${badgeAlert ? ' alert' : ''}`}>{badge}</span>
                    )}
                  </NavLink>
                );
              })}
            </div>
          ))}
        </nav>

        <div className="side-bottom">
          <b>Agent-first · Evidence-based</b>
          用户给出目标，QualiBug 负责理解、真实验证、收集证据并形成决策。
        </div>
      </aside>
    </>
  );
}
