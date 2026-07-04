import { NavLink, useSearchParams } from 'react-router-dom';
import { useProjectSummary } from '../api/data';
import { BrandLogo } from './BrandLogo';
import { buildProjectPath } from '../lib/project-navigation';

const navItems = [
  { to: 'dashboard', icon: 'overview', label: '风险总览', section: '风险监控' },
  { to: 'findings', icon: 'bug', label: '行为验证', section: null, badgeKey: 'findings' },
  { to: 'evidence', icon: 'shield', label: '证据链', section: null },
  { to: 'behavior-space', icon: 'runtime', label: '行为空间', section: null },
  { to: 'materials', icon: 'knowledge', label: '企业资料', section: '系统' },
  { to: 'release', icon: 'release', label: '发布门禁', section: null },
  { to: 'products', icon: 'product', label: '产品矩阵', section: null },
  { to: 'settings', icon: 'settings', label: '设置', section: null },
];

type NavItem = (typeof navItems)[number] & { badge?: number | string };

type SidebarProps = {
  mobileOpen?: boolean;
  onClose?: () => void;
};

const icons: Record<string, string> = {
  overview: 'M4 4h6v6H4V4Zm10 0h6v6h-6V4ZM4 14h6v6H4v-6Zm10 0h6v6h-6v-6Z',
  bug: 'M8 2v3m8-3v3M3 8h18M5.5 5.5l1.5 1.5m10 0 1.5-1.5M10 14l-2 3m6-3 2 3M12 12v3',
  shield: 'M12 3 20 6v5c0 5-3.3 8.5-8 10-4.7-1.5-8-5-8-10V6l8-3Z',
  runtime: 'M12 3v9l6 3M5 4h14v16H5z',
  knowledge: 'M4 5.5A2.5 2.5 0 0 1 6.5 3H20v16H6.5A2.5 2.5 0 0 0 4 21.5v-16Z',
  release: 'M6 4h12v16H6z M9 8h6M9 12h6M9 16h3',
  product: 'M4 6h7v5H4V6Zm9 0h7v5h-7V6ZM4 13h7v5H4v-5Zm9 2h7v3h-7v-3Z',
  settings: 'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8Zm8.2 4a7.6 7.6 0 0 0-.13-1.4l2.04-1.58-2-3.46-2.4.97a7.4 7.4 0 0 0-2.42-1.4L14.93 2h-4l-.37 3.13a7.4 7.4 0 0 0-2.42 1.4l-2.4-.97-2 3.46 2.04 1.58A7.6 7.6 0 0 0 5.8 12c0 .48.05.95.13 1.4l-2.04 1.58 2 3.46 2.4-.97a7.4 7.4 0 0 0 2.42 1.4l.37 3.13h4l.37-3.13a7.4 7.4 0 0 0 2.42-1.4l2.4.97 2-3.46-2.04-1.58c.08-.45.13-.92.13-1.4Z',
};

function SvgIcon({ name }: { name: string }) {
  const d = icons[name] || icons.overview;
  return <svg viewBox="0 0 24 24"><path d={d} /></svg>;
}

export function Sidebar({ mobileOpen = false, onClose }: SidebarProps) {
  const [params] = useSearchParams();
  const project = params.get('project')?.trim() || '';
  const { projectName, findingsCount, p0Count } = useProjectSummary(project);
  const findingCount = findingsCount;

  const navEntries = navItems.map((item, index) => ({
    ...item,
    sectionLabel: item.section && item.section !== navItems[index - 1]?.section ? item.section : null,
  }));
  const riskStateLabel = !project
    ? '等待选择客户'
    : p0Count > 0
      ? '存在阻断项'
      : (findingCount || 0) > 0
        ? '可进入闭环'
        : '待首次检测';

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
          <BrandLogo variant="full" size={38} dark subtitle="风险决策台" />
        </div>
        <div className="side-project">
          <span className="side-project-label">当前客户</span>
          <b>{projectName}</b>
          <small>围绕风险总览、证据链和发布门禁，直接支撑上线决策。</small>
          <div className="side-project-metrics">
            <div className="side-project-metric">
              <span>当前状态</span>
              <strong>{riskStateLabel}</strong>
            </div>
            <div className="side-project-metric">
              <span>风险发现</span>
              <strong>{findingCount ?? 0}</strong>
            </div>
            <div className="side-project-metric">
              <span>阻断项</span>
              <strong>{p0Count}</strong>
            </div>
          </div>
        </div>
        {navEntries.map((item: NavItem & { sectionLabel: string | null }) => {
          const sectionLabel = item.sectionLabel ? (
            <div key={`sec-${item.sectionLabel}`} className={`side-section${item.sectionLabel === '系统' ? ' side-section-system' : ''}`}>
              {item.sectionLabel}
            </div>
          ) : null;

          const badge = item.badgeKey === 'findings' && findingCount !== null
            ? (p0Count > 0 ? p0Count : findingCount)
            : item.badge;
          const badgeAlert = item.badgeKey === 'findings' && p0Count > 0;

          return (
            <div key={item.to}>
              {sectionLabel}
              <NavLink
                to={buildProjectPath(`/${item.to}`, project)}
                className={({ isActive }) => `side-link${isActive ? ' active' : ''}`}
                onClick={onClose}
              >
                <SvgIcon name={item.icon} />
                {item.label}
                {badge != null && (
                  <span className={`side-badge${badgeAlert ? ' alert' : ''}`}>{badge}</span>
                )}
              </NavLink>
            </div>
          );
        })}
        <div className="side-bottom">
          <b>QualiBug AI</b> 风险决策台
        </div>
      </aside>
    </>
  );
}
