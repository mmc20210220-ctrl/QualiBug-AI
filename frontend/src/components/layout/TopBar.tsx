import Link from "next/link";
import { ProjectSwitcher } from "@/components/layout/project-switcher/ProjectSwitcher";

export function TopBar({
  projectId,
  projects,
  userLabel,
  showLogout,
}: {
  projectId?: string;
  projects: { projectId: string; name: string }[];
  userLabel: string;
  showLogout?: boolean;
}) {
  return (
    <header className="border-b border-[var(--border)] bg-[rgba(11,15,20,0.75)] backdrop-blur">
      <div className="mx-auto flex w-full max-w-[1400px] items-center gap-4 px-4 py-3">
        <Link href="/projects" className="flex items-baseline gap-2">
          <span className="text-sm font-semibold tracking-tight">QualiBug</span>
          <span className="text-[11px] text-[var(--muted)]">Console</span>
        </Link>

        <div className="flex-1" />

        <div className="hidden items-center gap-3 md:flex">
          <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.45)] px-3 py-2 text-xs text-[var(--muted)]">
            {userLabel}
          </div>
          {showLogout ? (
            <Link
              className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(14,22,34,0.45)] px-3 py-2 text-xs text-[var(--muted)] hover:text-[var(--fg)]"
              href="/auth/logout"
            >
              登出
            </Link>
          ) : null}
        </div>
        <ProjectSwitcher projectId={projectId} projects={projects} />
      </div>
    </header>
  );
}
