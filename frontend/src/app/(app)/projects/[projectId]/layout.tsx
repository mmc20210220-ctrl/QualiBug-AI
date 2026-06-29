import { ProjectNav } from "@/components/layout/project-nav/ProjectNav";
import { requireProjectAccess } from "@/lib/auth/server";

export default async function ProjectLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ projectId: string }>;
}) {
  const { projectId } = await params;
  await requireProjectAccess(projectId);

  return (
    <div className="grid gap-4 md:grid-cols-[240px_1fr]">
      <aside className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(16,24,38,0.55)] p-3 shadow-[var(--shadow-1)] backdrop-blur">
        <div className="px-3 pb-2 text-xs text-[var(--muted)]">项目导航</div>
        <ProjectNav projectId={projectId} />
      </aside>
      <div className="min-w-0">{children}</div>
    </div>
  );
}
