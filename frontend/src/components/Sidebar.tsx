import { NavLink, useSearchParams } from 'react-router-dom';
import { useProjectSummary } from '../api/data';
import { BrandLogo } from './BrandLogo';
import { buildProjectPath } from '../lib/project-navigation';

const navItems = [
  { to: 'dashboard', icon: 'overview', label: '成果总览', section: '客户成果' },
  { to: 'findings', icon: 'bug', label: '缺陷清单', section: null, badgeKey: 'findings' },
  { to: 'evidence', icon: 'shield', label: '证据链', section: null },
  { to: 'release', icon: 'release', label: '发布门禁', section: null },
  { to: 'campaigns', icon: 'campaign', label: '运行中心', section: '执行与治理' },
  { to: 'coverage', icon: 'matrix', label: '覆盖矩阵', section: null },
  { to: 'test-tasks', icon: 'campaign', label: '测试任务', section: null },
  { to: 'behavior-space', icon: 'runtime', label: '行为空间', section: null },
  { to: 'clues', icon: 'runtime', label: '待验证线索', section: null, badgeKey: 'clues' },
  { to: 'settings', icon: 'settings', label: '项目设置', section: '接入配置' },
  { to: 'materials', icon: 'knowledge', label: '企业资料', section: null },
  { to: 'products', icon: 'product', label: '产品矩阵', section: '商业化' },
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
  source: 'M5 4h10l4 4v12H5V4Zm9 1v4h4M8 12h8M8 16h8',
  campaign: 'M4 5h16v14H4V5Zm3 3h5v3H7V8Zm0 5h10v3H7v-3Zm7-5h3v3h-3V8Z',
  release: 'M6 4h12v16H6z M9 8h6M9 12h6M9 16h3',
  product: 'M4 6h7v5H4V6Zm9 0h7v5h-7V6ZM4 13h7v5H4v-5Zm9 2h7v3h-7v-3Z',
  settings: 'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8Zm8.2 4a7.6 7.6 0 0 0-.13-1.4l2.04-1.58-2-3.46-2.4.97a7.4 7.4 0 0 0-2.42-1.4L14.93 2h-4l-.37 3.13a7.4 7.4 0 0 0-2.42 1.4l-2.4-.97-2 3.46 2.04 1.58A7.6 7.6 0 0 0 5.8 12c0 .48.05.95.13 1.4l-2.04 1.58 2 3.46 2.4-.97a7.4 7.4 0 0 0 2.42 1.4l.37 3.13h4l.37-3.13a7.4 7.4 0 0 0 2.42-1.4l2.4.97 2-3.46-2.04-1.58c.08-.45.13-.92.13-1.4Z',
  matrix: 'M4 4h16v16H4V4Zm4 0v16M4 9h16M4 14h16M12 4v16',
};

function SvgIcon({ name }: { name: string }) {
  const d = icons[name] || icons.overview;
  return <svg viewBox="0 0 24 24"><path d={d} /></svg>;
}

export function Sidebar({ mobileOpen = false, onClose }: SidebarProps) {
  const [params] = useSearchParams();
  const project = params.get('project')?.trim() || '';
  const { projectName, findingsCount, currentDefectCount, clueCount, p0Count } = useProjectSummary(project);
  const shelfCount = findingsCount;

  const navEntries = navItems.map((item, index) => ({
    ...item,
    sectionLabel: item.section && item.section !== navItems[index - 1]?.section ? item.section : null,
  }));
  const riskStateLabel = !project
    ? '请选择客户'
    : p0Count > 0
      ? '需先处理阻断'
      : (currentDefectCount || 0) > 0
        ? '可进入整改'
        : (shelfCount || 0) > 0
          ? '有历史结论'
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
          <BrandLogo variant="full" size={38} dark subtitle="客户成果台" />
        </div>
        <div className="side-project">
          <span className="side-project-label">当前客户</span>
          <b>{projectName}</b>
          <small>一眼看清本轮结论、可交付缺陷与证据可信度——给决策者看的成果面。</small>
          <div className="side-project-metrics">
            <div className="side-project-metric">
              <span>状态</span>
              <strong>{riskStateLabel}</strong>
            </div>
            <div className="side-project-metric">
              <span>可交付</span>
              <strong>{currentDefectCount ?? 0}</strong>
            </div>
            <div className="side-project-metric">
              <span>待补证</span>
              <strong>{clueCount ?? 0}</strong>
            </div>
          </div>
        </div>
        {navEntries.map((item: NavItem & { sectionLabel: string | null }) => {
          const sectionLabel = item.sectionLabel ? (
            <div key={`sec-${item.sectionLabel}`} className={`side-section${item.sectionLabel === '系统' ? ' side-section-system' : ''}`}>
              {item.sectionLabel}
            </div>
          ) : null;

          const badge = item.badgeKey === 'findings' && shelfCount !== null
            ? shelfCount
            : item.badgeKey === 'clues'
              ? clueCount
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
          <b>QualiBug AI</b>
          真实执行 · 可验收结论
        </div>
      </aside>
    </>
  );
}
