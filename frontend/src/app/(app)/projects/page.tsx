import Link from "next/link";
import { requireProjectsIndexAccess } from "@/lib/auth/server";
import { actorHasRole } from "@/lib/auth/authz";
import { readAuthConfig } from "@/lib/auth/config";
import { ApiClientError } from "@/lib/api/client";
import { getRuntimeHealth } from "@/lib/api/runtime-health";
import { RuntimeHealthBadge } from "@/components/runtime/RuntimeHealthBadge";
import { buildSessionProjectOptions, listProjectOptionsFromApi } from "@/lib/project-options";
import { readDataSourceConfig } from "@/lib/runtime-data-source";

export default async function ProjectsPage() {
  const session = await requireProjectsIndexAccess();
  const authConfig = readAuthConfig();
  const dataSource = readDataSourceConfig();
  const health = await getRuntimeHealth();
  const allowAll = actorHasRole(session.actor, "tenant_admin") || session.actor.projectIds.includes("*");

  let projects: { projectId: string; name: string }[] = [];
  let listDetail: string | null = null;
  const listSource: "demo" | "real" = dataSource.resolvedMode;

  if (dataSource.resolvedMode === "demo") {
    projects = buildSessionProjectOptions({ allowAll, projectIds: session.actor.projectIds });
  } else if (health.state !== "online") {
    listDetail = "后端未处于可验证在线状态，项目列表不会从 API 加载。";
  } else {
    try {
      projects = await listProjectOptionsFromApi();
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
          认证：{authConfig.mode} · 数据源：{listSource} ·{" "}
          {listSource === "demo" ? "demo 数据仅用于演示" : "已对接旧 Python Phase104 API，必须真实健康检查通过后才加载"}
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
