import Link from "next/link";
import { getBehaviorSpaceVisualization } from "@/behavior-space/api";
import { BehaviorSpaceFlow } from "@/components/behavior-space/BehaviorSpaceFlow";
import { toSafeErrorView } from "@/lib/api/command-center";

function isSupportedHref(href: string | undefined): href is string {
  if (!href) return false;
  return /^\/projects\/[^/]+(?:\/risks(?:\/[^/?#]+)?(?:\?.*)?(?:#.*)?|\/reports\/executive(?:#.*)?|\/execution(?:#.*)?|\/roi(?:#.*)?|(?:#.*)?)$/.test(
    href,
  );
}

export default async function BehaviorSpacePage({
  params,
}: {
  params: Promise<{ projectId: string }>;
}) {
  const { projectId } = await params;
  const p = encodeURIComponent(projectId);

  try {
    const visualization = await getBehaviorSpaceVisualization(projectId);
    const actions = [
      visualization.valueSummary.riskCost.href,
      visualization.valueSummary.auditReadiness.href,
      visualization.valueSummary.efficiencyGain.href,
      visualization.valueSummary.replayReadiness.href,
    ].filter(isSupportedHref);

    return (
      <div className="grid gap-4">
        <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(16,24,38,0.55)] p-6 shadow-[var(--shadow-1)] backdrop-blur">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="text-xs text-[var(--muted)]">Behavior Space</div>
              <h1 className="mt-2 text-xl font-semibold tracking-tight">2D 行为空间主视图</h1>
              <p className="mt-2 max-w-4xl text-sm text-[var(--muted)]">
                用系统建模、业务路径、覆盖状态、风险暴露点和证据入口解释“客户系统已被建模、真实路径已被覆盖、风险可下钻、证据可回放”。
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <Link
                href={`/projects/${p}/risks`}
                className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(255,92,122,0.12)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(255,255,255,0.18)]"
              >
                风险证据
              </Link>
              <Link
                href={`/projects/${p}/reports/executive`}
                className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(89,243,194,0.1)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(255,255,255,0.18)]"
              >
                领导层报告
              </Link>
            </div>
          </div>

          <div className="mt-5 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            {visualization.valueSummary.fields.map((field) => (
              <div key={field.fieldId} className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(11,15,20,0.48)] p-4">
                <div className="text-xs text-[var(--muted)]">{field.label}</div>
                <div className="mt-2 text-base font-semibold text-[var(--fg)]">{field.value}</div>
                <div className="mt-2 text-sm text-[var(--muted)]">{field.supportingText ?? "—"}</div>
                {field.actions?.length ? (
                  <div className="mt-3 flex flex-wrap gap-2">
                    {field.actions
                      .filter((action) => isSupportedHref(action.href))
                      .slice(0, 2)
                      .map((action) => (
                        <Link
                          key={`${field.fieldId}:${action.label}`}
                          href={action.href}
                          className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.44)] px-2.5 py-1.5 text-xs text-[var(--fg)] hover:border-[rgba(255,255,255,0.18)]"
                        >
                          {action.label}
                        </Link>
                      ))}
                  </div>
                ) : null}
              </div>
            ))}
          </div>

          {actions.length ? (
            <div className="mt-4 flex flex-wrap gap-2">
              {actions.map((href) => (
                <Link
                  key={href}
                  href={href}
                  className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.44)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(255,255,255,0.18)]"
                >
                  {href.includes("/risks")
                    ? "查看风险与回放"
                    : href.includes("/reports")
                      ? "打开报告"
                      : href.includes("/roi")
                        ? "查看 ROI"
                        : "打开入口"}
                </Link>
              ))}
            </div>
          ) : null}
        </div>

        <BehaviorSpaceFlow visualization={visualization} />
      </div>
    );
  } catch (err) {
    const safe = toSafeErrorView(err);
    return (
      <div className="grid gap-4">
        <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(16,24,38,0.55)] p-6 shadow-[var(--shadow-1)] backdrop-blur">
          <div className="text-xs text-[var(--muted)]">Behavior Space</div>
          <h1 className="mt-2 text-xl font-semibold tracking-tight">2D 行为空间主视图</h1>
        </div>

        <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)] p-5">
          <div className="text-sm font-semibold text-[var(--fg)]">{safe.title}</div>
          <div className="mt-2 text-sm text-[var(--muted)]">{safe.detail}</div>
          <div className="mt-4">
            <Link
              href={`/projects/${p}/risks`}
              className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(14,22,34,0.45)] px-3 py-2 text-sm hover:border-[rgba(255,255,255,0.18)]"
            >
              返回风险证据
            </Link>
          </div>
        </div>
      </div>
    );
  }
}
