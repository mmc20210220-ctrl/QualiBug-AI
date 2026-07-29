import { NavLink, useSearchParams } from 'react-router-dom';
import { useProjectSummary } from '../api/data';
import { BrandLogo } from './BrandLogo';
import { buildProjectPath } from '../lib/project-navigation';

type NavSection = { label: string; items: NavItem[] };
type NavItem = { to: string; icon: string; label: string; badgeKey?: 'findings' | 'clues' };

const sections: NavSection[] = [
  {
    label: '成果面',
    items: [
      { to: 'dashboard', icon: 'overview', label: '价值总览' },
      { to: 'findings', icon: 'bug', label: '问题清单', badgeKey: 'findings' },
      { to: 'evidence', icon: 'shield', label: '证据中心' },
      { to: 'release', icon: 'release', label: '发布门禁' },
    ],
  },
  {
    label: '企业认知',
    items: [
      { to: 'jobs', icon: 'workflow', label: '系统 Job 与异步任务' },
    ],
  },
  {
    label: '执行面',
    items: [
      { to: 'campaigns', icon: 'campaign', label: '运行中心' },
      { to: 'coverage', icon: 'matrix', label: '覆盖矩阵' },
    ],
  },
  {
    label: '配置',
    items: [
      { to: 'settings', icon: 'settings', label: '项目设置' },
    ],
  },
];

const icons: Record<string, string> = {
  overview: 'M4 4h6v6H4V4Zm10 0h6v6h-6V4ZM4 14h6v6H4v-6Zm10 0h6v6h-6v-6Z',
  bug: 'M8 2v3m8-3v3M3 8h18M5.5 5.5l1.5 1.5m10 0 1.5-1.5M10 14l-2 3m6-3 2 3M12 12v3',
  shield: 'M12 3 20 6v5c0 5-3.3 8.5-8 10-4.7-1.5-8-5-8-10V6l8-3Z',
  campaign: 'M4 5h16v14H4V5Zm3 3h5v3H7V8Zm0 5h10v3H7v-3Zm7-5h3v3h-3V8Z',
  release: 'M6 4h12v16H6z M9 8h6M9 12h6M9 16h3',
  workflow: 'M4 5h5v4H4V5Zm11 0h5v4h-5V5ZM9 7h6M6.5 9v4m0 0h11m0 0V9M10 15h4v4h-4v-4Z',
  settings: 'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8Zm8.2 4a7.6 7.6 0 0 0-.13-1.4l2.04-1.58-2-3.46-2.4.97a7.4 7.4 0 0 0-2.42-1.4L14.93 2h-4l-.37 3.13a7.4 7.4 0 0 0-2.42 1.4l-2.4-.97-2 3.46 2.04 1.58A7.6 7.6 0 0 0 5.8 12c0 .48.05.95.13 1.4l-2.04 1.58 2 3.46 2.4-.97a7.4 7.4 0 0 0 2.42 1.4l.37 3.13h4l.37-3.13a7.4 7.4 0 0 0 2.42-1.4l2.4.97 2-3.46-2.04-1.58c.08-.45.13-.92.13-1.4Z',
  matrix: 'M4 4h16v16H4V4Zm4 0v16M4 9h16M4 14h16M12 4v16',
};

function SvgIcon({ name }: { name: string }) {
  const d = icons[name] || icons.overview;
  return <svg viewBox="0 0 24 24"><path d={d} /></svg>;
}

type SidebarProps = {
  mobileOpen?: boolean;
  onClose?: () => void;
};

export function Sidebar({ mobileOpen = false, onClose }: SidebarProps) {
  const [params] = useSearchParams();
  const project = params.get('project')?.trim() || '';
  const { projectName, currentDefectCount, clueCount, p0Count } = useProjectSummary(project);

  const riskStateLabel = !project
    ? '请选择客户'
    : p0Count > 0
      ? '需先处理阻断'
      : (currentDefectCount || 0) > 0
        ? '可进入整改'
        : (clueCount || 0) > 0
          ? '补证进行中'
          : '等待首次检测';

  return (
    <>
      <div
        className={`sidebar-backdrop${mobileOpen ? ' open' : ''}`}
        onClick={onClose}
        aria-hidden={mobileOpen ? 'false' : 'true'}
      />
      <aside className={`sidebar${mobileOpen ? ' mobile-open' : ''}`}>
        <div className="side-brand">
          <button type="button" className="side-close" onClick={onClose} aria-label="关闭导航">
            ×
          </button>
          <BrandLogo variant="full" detail="compact" tone="dark" size={38} subtitle="客户成果台" />
        </div>

        <div className="side-project">
          <span className="side-project-label">当前客户</span>
          <b>{projectName}</b>
          <div className="side-project-metrics">
            <div className="side-project-metric">
              <span>状态</span>
              <strong>{riskStateLabel}</strong>
            </div>
            <div className="side-project-metric">
              <span>已确认</span>
              <strong>{currentDefectCount ?? 0}</strong>
            </div>
            <div className="side-project-metric">
              <span>待补证</span>
              <strong>{clueCount ?? 0}</strong>
            </div>
          </div>
        </div>

        <nav className="side-nav">
          {sections.map((section) => (
            <div key={section.label} className="side-section-group">
              <div className="side-section">{section.label}</div>
              {section.items.map((item) => {
                const badge = item.badgeKey === 'findings'
                  ? (currentDefectCount || 0)
                  : item.badgeKey === 'clues'
                    ? (clueCount || 0)
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
          <b>QualiBug AI</b>
          AI 驱动 · 真实验证 · 可量化价值
        </div>
      </aside>
    </>
  );
}
