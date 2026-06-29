import type { SessionActor, TenantRole } from "@/lib/auth/session";

export function normalizeRole(role: string): TenantRole | null {
  const normalized = role.trim().toLowerCase();
  if (!normalized) return null;
  if (["tenant_admin", "admin", "owner", "platform_admin", "superuser"].includes(normalized)) return "tenant_admin";
  if (["project_admin", "project_owner"].includes(normalized)) return "project_admin";
  if (["project_viewer", "viewer", "read", "readonly", "user"].includes(normalized)) return "project_viewer";
  if (["auditor", "audit", "compliance"].includes(normalized)) return "auditor";
  return null;
}

export function actorHasRole(actor: SessionActor, role: TenantRole): boolean {
  return actor.roles.includes(role);
}

export function canAccessProject(actor: SessionActor, projectId: string): boolean {
  if (actorHasRole(actor, "tenant_admin")) return true;
  if (actor.projectIds.includes("*")) return true;
  return actor.projectIds.includes(projectId);
}

export function canSeeProjectsIndex(actor: SessionActor): boolean {
  if (actorHasRole(actor, "tenant_admin")) return true;
  return actor.projectIds.length > 0;
}

