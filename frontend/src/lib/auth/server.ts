import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { canAccessProject, canSeeProjectsIndex } from "@/lib/auth/authz";
import { readAuthConfig } from "@/lib/auth/config";
import { SESSION_COOKIE_NAME, type AuthSession } from "@/lib/auth/session";
import { verifySessionCookieValue } from "@/lib/auth/session";

function demoSession(): AuthSession {
  const exp = Math.floor(Date.now() / 1000) + 24 * 60 * 60;
  return {
    exp,
    issuer: "demo",
    actor: { userId: "demo", name: "Demo", roles: ["tenant_admin"], projectIds: ["*"] },
  };
}

export async function getSession(): Promise<AuthSession | null> {
  const config = readAuthConfig();
  if (config.mode === "demo") return demoSession();
  if (!config.sessionSecret) return null;
  const cookieStore = await cookies();
  const cookie = cookieStore.get(SESSION_COOKIE_NAME)?.value;
  return verifySessionCookieValue(cookie, config.sessionSecret);
}

export async function requireSession(): Promise<AuthSession> {
  const config = readAuthConfig();
  if (config.mode === "demo") return demoSession();
  const session = await getSession();
  if (!session) redirect("/login");
  return session;
}

export async function requireProjectsIndexAccess(): Promise<AuthSession> {
  const session = await requireSession();
  if (!canSeeProjectsIndex(session.actor)) redirect("/no-access");
  return session;
}

export async function requireProjectAccess(projectId: string): Promise<AuthSession> {
  const session = await requireSession();
  if (!canAccessProject(session.actor, projectId)) redirect("/no-access");
  return session;
}
