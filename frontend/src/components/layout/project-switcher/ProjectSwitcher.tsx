"use client";

import { useParams, usePathname, useRouter } from "next/navigation";
import { demoProjects } from "@/lib/demo-projects";
import { getProjectBasePath } from "@/components/layout/navigation";

export function ProjectSwitcher({
  projectId,
  projects,
}: {
  projectId?: string;
  projects?: { projectId: string; name: string }[];
}) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useParams<{ projectId?: string }>();
  const routeProjectId = typeof params.projectId === "string" ? params.projectId : undefined;
  const baseOptions = projects && projects.length ? projects : demoProjects;
  const options =
    routeProjectId && !baseOptions.some((project) => project.projectId === routeProjectId)
      ? [{ projectId: routeProjectId, name: `当前项目 ${routeProjectId}` }, ...baseOptions]
      : baseOptions;
  const value = routeProjectId ?? projectId ?? options[0]?.projectId ?? "";

  return (
    <div className="flex items-center gap-2">
      <div className="hidden text-xs text-[var(--muted)] md:block">当前项目切换</div>
      <select
        aria-label="当前项目切换"
        className="h-9 max-w-[220px] rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.55)] px-3 text-sm text-[var(--fg)] shadow-sm outline-none ring-0"
        value={value}
        onChange={(e) => {
          const nextId = e.target.value;
          const currentBase = routeProjectId ? getProjectBasePath(routeProjectId) : projectId ? getProjectBasePath(projectId) : null;
          const nextBase = getProjectBasePath(nextId);
          const nextPath =
            currentBase && pathname.startsWith(currentBase)
              ? `${nextBase}${pathname.slice(currentBase.length)}`
              : nextBase;
          router.push(nextPath || nextBase);
        }}
      >
        {options.map((p) => (
          <option key={p.projectId} value={p.projectId}>
            {p.name}
          </option>
        ))}
      </select>
    </div>
  );
}
