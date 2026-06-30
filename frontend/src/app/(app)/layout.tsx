import { AppShell } from "@/components/layout/AppShell";
import { actorHasRole } from "@/lib/auth/authz";
import { readAuthConfig } from "@/lib/auth/config";
import { getRuntimeHealth } from "@/lib/api/runtime-health";
import { getSession } from "@/lib/auth/server";
import { maskEmail, maskId } from "@/lib/redact";
import { buildSessionProjectOptions, listProjectOptionsFromApi } from "@/lib/project-options";
import { readDataSourceConfig } from "@/lib/runtime-data-source";

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const config = readAuthConfig();
  const dataSource = readDataSourceConfig();
  const session = await getSession();
  const actor = session?.actor;
  const allowAll = actor ? actorHasRole(actor, "tenant_admin") || actor.projectIds.includes("*") : true;
  let projects = buildSessionProjectOptions({ allowAll, projectIds: actor?.projectIds ?? [] });
  if (dataSource.resolvedMode === "real") {
    const health = await getRuntimeHealth();
    if (health.state === "online") {
      try {
        projects = await listProjectOptionsFromApi();
      } catch {}
    }
  }
  const userLabel =
    config.mode === "demo"
      ? dataSource.resolvedMode === "real"
        ? "Demo Login"
        : "Demo"
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
