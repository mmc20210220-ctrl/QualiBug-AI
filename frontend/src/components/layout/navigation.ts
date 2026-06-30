export type NavItem = {
  key: string;
  label: string;
  href: string;
};

export type ProjectNavItem = {
  key: string;
  label: string;
  href: (projectId: string) => string;
  matches: (pathname: string, projectId: string) => boolean;
};

export const globalNavItems: NavItem[] = [
  { key: "projects", label: "项目列表", href: "/projects" },
  { key: "login", label: "登录", href: "/login" },
];

function isProjectSection(pathname: string, projectId: string, suffix: string) {
  const base = getProjectBasePath(projectId);
  const sectionPath = `${base}${suffix}`;
  return pathname === sectionPath || pathname.startsWith(`${sectionPath}/`);
}

export const projectNavItems: ProjectNavItem[] = [
  {
    key: "workspace",
    label: "项目工作区",
    href: (projectId: string) => getProjectBasePath(projectId),
    matches: (pathname: string, projectId: string) => pathname === getProjectBasePath(projectId),
  },
  {
    key: "environment",
    label: "客户环境诊断",
    href: (projectId: string) => `${getProjectBasePath(projectId)}/environment`,
    matches: (pathname: string, projectId: string) => isProjectSection(pathname, projectId, "/environment"),
  },
  {
    key: "behavior-space",
    label: "Behavior Space",
    href: (projectId: string) => `${getProjectBasePath(projectId)}/behavior-space`,
    matches: (pathname: string, projectId: string) => isProjectSection(pathname, projectId, "/behavior-space"),
  },
  {
    key: "capabilities",
    label: "能力中心",
    href: (projectId: string) => `${getProjectBasePath(projectId)}/capabilities`,
    matches: (pathname: string, projectId: string) => isProjectSection(pathname, projectId, "/capabilities"),
  },
  {
    key: "risks",
    label: "风险证据",
    href: (projectId: string) => `${getProjectBasePath(projectId)}/risks`,
    matches: (pathname: string, projectId: string) => isProjectSection(pathname, projectId, "/risks"),
  },
  {
    key: "execution",
    label: "执行",
    href: (projectId: string) => `${getProjectBasePath(projectId)}/execution`,
    matches: (pathname: string, projectId: string) => isProjectSection(pathname, projectId, "/execution"),
  },
  {
    key: "reports",
    label: "报告",
    href: (projectId: string) => `${getProjectBasePath(projectId)}/reports/executive`,
    matches: (pathname: string, projectId: string) => isProjectSection(pathname, projectId, "/reports"),
  },
  {
    key: "roi",
    label: "ROI/价值",
    href: (projectId: string) => `${getProjectBasePath(projectId)}/roi`,
    matches: (pathname: string, projectId: string) => isProjectSection(pathname, projectId, "/roi"),
  },
];

export function getProjectBasePath(projectId: string) {
  return `/projects/${encodeURIComponent(projectId)}`;
}

export function getActiveGlobalNavKey(pathname: string) {
  if (pathname === "/projects") return "projects";
  if (pathname === "/login") return "login";
  return null;
}

export function getActiveProjectNavKey(pathname: string, projectId: string) {
  return projectNavItems.find((item) => item.matches(pathname, projectId))?.key ?? null;
}
