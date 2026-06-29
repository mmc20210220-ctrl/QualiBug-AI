"use client";

import { useMemo, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { demoProjects } from "@/lib/demo-projects";

export function ProjectSwitcher({
  projectId,
  projects,
}: {
  projectId?: string;
  projects?: { projectId: string; name: string }[];
}) {
  const router = useRouter();
  const params = useParams<{ projectId?: string }>();
  const routeProjectId = typeof params.projectId === "string" ? params.projectId : undefined;
  const options = projects && projects.length ? projects : demoProjects;
  const initial = useMemo(
    () => routeProjectId ?? projectId ?? options[0]?.projectId ?? "",
    [options, projectId, routeProjectId],
  );
  const [value, setValue] = useState<string>(initial);

  return (
    <div className="flex items-center gap-2">
      <div className="hidden text-xs text-[var(--muted)] md:block">当前项目切换</div>
      <select
        aria-label="当前项目切换"
        className="h-9 max-w-[220px] rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.55)] px-3 text-sm text-[var(--fg)] shadow-sm outline-none ring-0"
        value={value}
        onChange={(e) => {
          const nextId = e.target.value;
          setValue(nextId);
          router.push(`/projects/${encodeURIComponent(nextId)}`);
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
