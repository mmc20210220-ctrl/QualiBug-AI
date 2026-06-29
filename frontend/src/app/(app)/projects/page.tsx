import Link from "next/link";
import { demoProjects } from "@/lib/demo-projects";
import { requireProjectsIndexAccess } from "@/lib/auth/server";
import { actorHasRole } from "@/lib/auth/authz";
import { maskId } from "@/lib/redact";
import { readAuthConfig } from "@/lib/auth/config";
import { ApiClientError, requestJson } from "@/lib/api/client";
import { getRuntimeHealth } from "@/lib/api/runtime-health";
import { RuntimeHealthBadge } from "@/components/runtime/RuntimeHealthBadge";

export default async function ProjectsPage() {
  const session = await requireProjectsIndexAccess();
  const authConfig = readAuthConfig();
  const health = await getRuntimeHealth();
  const allowAll = actorHasRole(session.actor, "tenant_admin") || session.actor.projectIds.includes("*");

  let projects: { projectId: string; name: string }[] = [];
  let listDetail: string | null = null;
  let listSource: "demo" | "real" = authConfig.mode === "demo" ? "demo" : "real";

  if (authConfig.mode === "demo") {
    projects = allowAll
      ? demoProjects.map((p) => ({ projectId: p.projectId, name: p.name }))
      : session.actor.projectIds.map((projectId) => {
          const hit = demoProjects.find((p) => p.projectId === projectId);
          return hit ? { projectId: hit.projectId, name: hit.name } : { projectId, name: `项目 ${maskId(projectId)}` };
        });
  } else if (health.state !== "online") {
    listDetail = "后端未处于可验证在线状态，项目列表不会从 API 加载。";
  } else {
    try {
      const envelope = await requestJson<{ success?: boolean; data?: unknown; error?: { message?: string } | null }>({
        method: "GET",
        path: "/api/v1/projects",
        timeoutMs: 3000,
        retry: { retries: 1, baseDelayMs: 200, maxDelayMs: 800 },
      });
      const data = envelope?.data;
      if (Array.isArray(data)) {
        projects = data
          .map((row) => {
            if (!row || typeof row !== "object") return null;
            const record = row as Record<string, unknown>;
            const projectId = typeof record.project_id === "string" ? record.project_id : typeof record.projectId === "string" ? record.projectId : null;
            if (!projectId) return null;
            const name =
              typeof record.project_name === "string"
                ? record.project_name
                : typeof record.name === "string"
                  ? record.name
                  : `项目 ${maskId(projectId)}`;
            return { projectId, name };
          })
          .filter((item): item is { projectId: string; name: string } => Boolean(item));
      } else {
        listDetail = "项目列表返回了不可识别的结构。";
      }
    } catch (err) {
      if (err instanceof ApiClientError) {
        if (err.kind === "network" || err.kind === "timeout") listDetail = `离线：${err.message}`;
        else if (err.kind === "http") listDetail = `错误：${err.message}`;
        else listDetail = err.message;
      } else {
        listDetail = "项目列表加载失败。";
      }
    }
  }

  return (
    <div className="grid gap-4">
      <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(16,24,38,0.55)] p-6 shadow-[var(--shadow-1)] backdrop-blur">
        <div className="text-xs text-[var(--muted)]">项目</div>
        <div className="mt-2 flex items-start justify-between gap-4">
          <h1 className="text-xl font-semibold tracking-tight">项目列表</h1>
          <RuntimeHealthBadge health={health} />
        </div>
        <p className="mt-2 text-sm text-[var(--muted)]">
          选择一个项目进入工作区。该列表基于租户/项目/角色权限模型渲染（Task2）。
        </p>
        <div className="mt-3 text-xs text-[var(--muted)]">
          数据源：{listSource} · {authConfig.mode === "demo" ? "demo 仅用于演示" : "real 模式不允许把仅配置视为在线"}
        </div>
      </div>

      {listDetail ? (
        <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)] p-5 text-sm text-[var(--muted)]">
          {listDetail}
        </div>
      ) : null}

      {projects.length ? (
        <div className="grid gap-3 md:grid-cols-3">
          {projects.map((p) => (
            <Link
              key={p.projectId}
              href={`/projects/${encodeURIComponent(p.projectId)}`}
              className="group rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.45)] p-5 shadow-[var(--shadow-1)] backdrop-blur transition hover:border-[rgba(89,243,194,0.35)]"
            >
              <div className="flex items-center justify-between">
                <div className="text-sm font-semibold">{p.name}</div>
                <div className="text-xs text-[var(--muted)] group-hover:text-[var(--fg)]">进入</div>
              </div>
              <div className="mt-2 text-xs text-[var(--muted)]">projectId: {p.projectId}</div>
            </Link>
          ))}
        </div>
      ) : (
        <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)] p-5 text-sm text-[var(--muted)]">
          {listDetail ? "项目列表不可用。" : "（空）"}
        </div>
      )}
    </div>
  );
}
