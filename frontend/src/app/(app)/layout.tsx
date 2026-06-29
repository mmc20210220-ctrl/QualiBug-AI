import { AppShell } from "@/components/layout/AppShell";
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
    <AppShell projects={projects} userLabel={userLabel} showLogout={config.mode === "oidc"}>
      {children}
    </AppShell>
  );
}
