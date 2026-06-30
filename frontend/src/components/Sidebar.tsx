import { NavLink, useSearchParams } from 'react-router-dom';
import { useState, useEffect } from 'react';
import { getFindings } from '../api/client';

const navItems = [
  { to: 'dashboard', icon: 'overview', label: '风险总览', section: '风险监控' },
  { to: 'findings', icon: 'bug', label: '行为裂隙', section: null, badgeKey: 'findings' },
  { to: 'evidence', icon: 'shield', label: '证据链', section: null },
  { to: 'behavior-space', icon: 'runtime', label: '行为空间', section: null },
  { to: 'materials', icon: 'knowledge', label: '企业资料', section: '系统' },
  { to: 'release', icon: 'release', label: '发布门禁', section: null },
  { to: 'settings', icon: 'settings', label: '设置', section: null },
];

const icons: Record<string, string> = {
  overview: 'M4 4h6v6H4V4Zm10 0h6v6h-6V4ZM4 14h6v6H4v-6Zm10 0h6v6h-6v-6Z',
  bug: 'M8 2v3m8-3v3M3 8h18M5.5 5.5l1.5 1.5m10 0 1.5-1.5M10 14l-2 3m6-3 2 3M12 12v3',
  shield: 'M12 3 20 6v5c0 5-3.3 8.5-8 10-4.7-1.5-8-5-8-10V6l8-3Z',
  runtime: 'M12 3v9l6 3M5 4h14v16H5z',
  knowledge: 'M4 5.5A2.5 2.5 0 0 1 6.5 3H20v16H6.5A2.5 2.5 0 0 0 4 21.5v-16Z',
  release: 'M6 4h12v16H6z M9 8h6M9 12h6M9 16h3',
  settings: 'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8Zm8.2 4a7.6 7.6 0 0 0-.13-1.4l2.04-1.58-2-3.46-2.4.97a7.4 7.4 0 0 0-2.42-1.4L14.93 2h-4l-.37 3.13a7.4 7.4 0 0 0-2.42 1.4l-2.4-.97-2 3.46 2.04 1.58A7.6 7.6 0 0 0 5.8 12c0 .48.05.95.13 1.4l-2.04 1.58 2 3.46 2.4-.97a7.4 7.4 0 0 0 2.42 1.4l.37 3.13h4l.37-3.13a7.4 7.4 0 0 0 2.42-1.4l2.4.97 2-3.46-2.04-1.58c.08-.45.13-.92.13-1.4Z',
};

function SvgIcon({ name }: { name: string }) {
  const d = icons[name] || icons.overview;
  return <svg viewBox="0 0 24 24"><path d={d} /></svg>;
}

export function Sidebar() {
  const [params] = useSearchParams();
  const project = params.get('project') || 'real_project_demo';
  const [findingCount, setFindingCount] = useState<number | null>(null);
  const [p0Count, setP0Count] = useState<number>(0);
  const [projectName, setProjectName] = useState<string>(project);

  // Fetch real project data
  useEffect(() => {
    getFindings(project).then(data => {
      const findings = data?.report?.stage2_discovery?.findings;
      const name = data?.report?.project_name;
      if (name) setProjectName(name);
      if (Array.isArray(findings)) {
        setFindingCount(findings.length);
        setP0Count(findings.filter((f: any) => f.severity === 'P0').length);
      } else {
        setFindingCount(0);
      }
    }).catch(() => setFindingCount(0));
  }, [project]);

  let currentSection = '';

  return (
    <aside className="sidebar">
      <div className="side-brand">
        <div className="side-mark">QB</div>
        <div><strong>QualiBug</strong><span>行为风险终端</span></div>
      </div>
      <div className="side-project">
        当前项目<b>{projectName}</b>
      </div>
      {navItems.map((item: any) => {
        const sectionLabel = item.section && item.section !== currentSection ? (
          <div key={`sec-${item.section}`} className="side-section" style={item.section === '系统' ? { marginTop: 6 } : {}}>
            {item.section}
          </div>
        ) : null;
        if (item.section) currentSection = item.section;

        // Badge: show P0 count in red, fallback to total
        const badge = item.badgeKey === 'findings' && findingCount !== null
          ? (p0Count > 0 ? p0Count : findingCount)
          : item.badge;
        const badgeAlert = item.badgeKey === 'findings' && p0Count > 0;

        return (
          <div key={item.to}>
            {sectionLabel}
            <NavLink
              to={`/${item.to}?project=${project}`}
              className={({ isActive }) => `side-link${isActive ? ' active' : ''}`}
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
        <b>QualiBug Enterprise</b>行为风险评级基础设施
      </div>
    </aside>
  );
}
