import Link from "next/link";
import { readAuthConfig } from "@/lib/auth/config";
import { getRuntimeHealth } from "@/lib/api/runtime-health";
import { RuntimeHealthBadge, RuntimeHealthDetail } from "@/components/runtime/RuntimeHealthBadge";

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const next = typeof params.next === "string" ? params.next : undefined;
  const error = typeof params.error === "string" ? params.error : undefined;
  const config = readAuthConfig();
  const health = await getRuntimeHealth();
  const oidcLoginHref = `/auth/login${next ? `?next=${encodeURIComponent(next)}` : ""}`;

  return (
    <div className="grid w-full gap-8 md:grid-cols-2">
      <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(16,24,38,0.75)] p-7 shadow-[var(--shadow-1)] backdrop-blur">
        <div className="text-xs text-[var(--muted)]">QualiBug Console</div>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight">企业登录</h1>
        <p className="mt-2 text-sm text-[var(--muted)]">
          Task1 阶段提供可运行骨架：登录入口、导航框架、核心路由占位与价值呈现层配置可消费。
        </p>

        <div className="mt-6 grid gap-3">
          {config.mode === "oidc" ? (
            <Link
              className="rounded-[var(--radius-sm)] bg-[linear-gradient(135deg,rgba(89,243,194,0.16),rgba(122,167,255,0.10))] px-4 py-3 text-sm font-medium ring-1 ring-[rgba(255,255,255,0.10)] hover:ring-[rgba(255,255,255,0.18)]"
              href={oidcLoginHref}
            >
              使用 OIDC SSO 登录
            </Link>
          ) : (
            <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(14,22,34,0.55)] px-4 py-3 text-sm text-[var(--muted)]">
              当前为 demo 模式（AUTH_MODE=demo），无需登录
            </div>
          )}

          <Link
            className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(14,22,34,0.55)] px-4 py-3 text-sm text-[var(--muted)] hover:text-[var(--fg)]"
            href="/auth/saml"
          >
            使用 SAML 登录（预留）
          </Link>

          {config.mode === "demo" ? (
            <Link
              className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(14,22,34,0.55)] px-4 py-3 text-sm text-[var(--muted)] hover:text-[var(--fg)]"
              href="/projects"
            >
              进入演示模式
            </Link>
          ) : null}
        </div>

        {error ? (
          <div className="mt-4 rounded-[var(--radius-sm)] border border-[rgba(255,86,86,0.35)] bg-[rgba(255,86,86,0.08)] px-4 py-3 text-sm text-[rgba(255,220,220,0.92)]">
            {error}
          </div>
        ) : null}
      </div>

      <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)] p-7">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="text-xs text-[var(--muted)]">可观测状态</div>
            <h2 className="mt-2 text-base font-semibold">后端状态必须真实可验证</h2>
          </div>
          <RuntimeHealthBadge health={health} />
        </div>
        <ul className="mt-3 grid gap-2 text-sm text-[var(--muted)]">
          <li>在线：真实健康检查成功</li>
          <li>未验证：仅配置/未进行探测</li>
          <li>离线：探测失败或不可达</li>
        </ul>
        <RuntimeHealthDetail health={health} />
      </div>
    </div>
  );
}
