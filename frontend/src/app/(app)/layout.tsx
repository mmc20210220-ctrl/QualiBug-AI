import { GlobalNav } from "@/components/layout/GlobalNav";
import { TopBar } from "@/components/layout/TopBar";
import { actorHasRole } from "@/lib/auth/authz";
import { readAuthConfig } from "@/lib/auth/config";
import { getSession } from "@/lib/auth/server";
import { demoProjects } from "@/lib/demo-projects";
import { maskEmail, maskId } from "@/lib/redact";

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const config = readAuthConfig();
  const session = await getSession();
  const actor = session?.actor;
  const allowAll = actor ? actorHasRole(actor, "tenant_admin") || actor.projectIds.includes("*") : true;
  const projects = actor
    ? allowAll
      ? demoProjects.map((p) => ({ projectId: p.projectId, name: p.name }))
      : actor.projectIds.map((projectId) => {
          const hit = demoProjects.find((p) => p.projectId === projectId);
          return hit ? { projectId: hit.projectId, name: hit.name } : { projectId, name: `项目 ${maskId(projectId)}` };
        })
    : demoProjects.map((p) => ({ projectId: p.projectId, name: p.name }));
  const userLabel =
    config.mode === "demo"
      ? "Demo"
      : actor?.email
        ? maskEmail(actor.email)
        : actor?.name
          ? actor.name
          : actor?.userId
            ? maskId(actor.userId)
            : "未登录";

  return (
    <div className="min-h-dvh bg-[radial-gradient(1200px_700px_at_20%_-30%,rgba(89,243,194,0.10),transparent_60%),radial-gradient(1000px_600px_at_80%_0%,rgba(122,167,255,0.12),transparent_55%),linear-gradient(180deg,var(--bg),#070a0f)]">
      <TopBar projects={projects} userLabel={userLabel} showLogout={config.mode === "oidc"} />
      <div className="mx-auto grid w-full max-w-[1400px] grid-cols-1 gap-4 px-4 pb-10 pt-4 md:grid-cols-[240px_1fr]">
        <aside className="hidden md:block">
          <div className="sticky top-4 rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(16,24,38,0.55)] p-3 shadow-[var(--shadow-1)] backdrop-blur">
            <GlobalNav />
          </div>
        </aside>
        <main className="min-w-0">{children}</main>
      </div>
    </div>
  );
}
