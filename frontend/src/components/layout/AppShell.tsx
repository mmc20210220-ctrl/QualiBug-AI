"use client";

import { useParams, usePathname } from "next/navigation";
import { GlobalNav } from "@/components/layout/GlobalNav";
import { TopBar } from "@/components/layout/TopBar";
import { ProjectNav } from "@/components/layout/project-nav/ProjectNav";
import { ProjectEventStream } from "@/components/layout/ProjectEventStream";
import { ProjectReadinessRail } from "@/components/layout/ProjectReadinessRail";
import {
  getActiveGlobalNavKey,
  getActiveProjectNavKey,
  getProjectBasePath,
  globalNavItems,
  projectNavItems,
} from "@/components/layout/navigation";

export function AppShell({
  projects,
  userLabel,
  showLogout,
  children,
}: {
  projects: { projectId: string; name: string }[];
  userLabel: string;
  showLogout?: boolean;
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  const params = useParams<{ projectId?: string }>();
  const projectId = typeof params.projectId === "string" ? params.projectId : undefined;
  const isProjectRoute = Boolean(projectId && pathname.startsWith(getProjectBasePath(projectId)));
  const activeGlobalKey = getActiveGlobalNavKey(pathname);
  const activeProjectKey = projectId ? getActiveProjectNavKey(pathname, projectId) : null;
  const activeLabel = isProjectRoute
    ? projectNavItems.find((item) => item.key === activeProjectKey)?.label ?? "项目工作区"
    : globalNavItems.find((item) => item.key === activeGlobalKey)?.label;

  return (
    <div className="min-h-dvh bg-[radial-gradient(1200px_700px_at_20%_-30%,rgba(89,243,194,0.10),transparent_60%),radial-gradient(1000px_600px_at_80%_0%,rgba(122,167,255,0.12),transparent_55%),linear-gradient(180deg,var(--bg),#070a0f)]">
      <TopBar
        projectId={projectId}
        projects={projects}
        userLabel={userLabel}
        showLogout={showLogout}
        activeLabel={activeLabel}
        isProjectRoute={isProjectRoute}
      />

      <div className="mx-auto w-full max-w-[1760px] px-4 pb-10 pt-4">
        <div
          className={[
            "grid gap-4",
            isProjectRoute
              ? "xl:grid-cols-[240px_minmax(0,1fr)_320px]"
              : "md:grid-cols-[240px_minmax(0,1fr)]",
          ].join(" ")}
        >
          <aside className="hidden md:block">
            <div className="sticky top-4 rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(16,24,38,0.55)] p-3 shadow-[var(--shadow-1)] backdrop-blur">
              <div className="px-3 pb-2 text-xs text-[var(--muted)]">{isProjectRoute ? "项目导航" : "全局导航"}</div>
              {projectId && isProjectRoute ? <ProjectNav projectId={projectId} /> : <GlobalNav />}
            </div>
          </aside>

          <main className="min-w-0">{children}</main>

          {isProjectRoute && projectId ? (
            <aside className="hidden xl:block">
              <div className="sticky top-4">
                <ProjectReadinessRail projectId={projectId} />
              </div>
            </aside>
          ) : null}
        </div>

        {isProjectRoute && projectId ? (
          <div className="mt-4 grid gap-4 xl:grid-cols-[240px_minmax(0,1fr)_320px]">
            <div className="hidden xl:block" />
            <div className="xl:col-span-2">
              <ProjectEventStream projectId={projectId} />
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
