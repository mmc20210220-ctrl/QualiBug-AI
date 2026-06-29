import Link from "next/link";
import { getBehaviorSpaceVisualization } from "@/behavior-space/api";
import { BehaviorSpaceDeferredVisualizations } from "@/components/behavior-space/BehaviorSpaceDeferredVisualizations";
import { toSafeErrorView } from "@/lib/api/command-center";

function isSupportedHref(href: string | undefined): href is string {
  if (!href) return false;
  return /^\/projects\/[^/]+(?:\/risks(?:\/[^/?#]+)?(?:\?.*)?(?:#.*)?|\/reports\/executive(?:#.*)?|\/execution(?:#.*)?|\/roi(?:#.*)?|\/behavior-space(?:#.*)?|(?:#.*)?)$/.test(
    href,
  );
}

function formatAuditTimestamp(value: string | undefined): string {
  if (!value) return "时间待补充";
  return value.replace("T", " ").replace("Z", "");
}

function buildReplayHref(href: string | undefined): string | undefined {
  if (!isSupportedHref(href)) return undefined;
  return href.includes("#") ? href : `${href}#replay`;
}

function formatAuditKind(kind: string): string {
  if (kind === "approval") return "审批";
  if (kind === "delivery") return "交付";
  if (kind === "export") return "导出";
  if (kind === "execution") return "执行";
  if (kind === "signoff") return "签收";
  if (kind === "snapshot") return "快照";
  if (kind === "report") return "报告";
  return "审计";
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
    const launchRecommendation = visualization.valueSummary.launchRecommendation;
    const riskCost = visualization.valueSummary.riskCost;
    const nextActionsField = visualization.valueSummary.nextActions;
    const nextActionLinks: Array<{ label: string; href: string }> = (nextActionsField.actions ?? []).flatMap((action) =>
      isSupportedHref(action.href) ? [{ label: action.label, href: action.href }] : [],
    );
    const supportingFields = visualization.valueSummary.fields.filter(
      (field) => ![launchRecommendation.fieldId, riskCost.fieldId, nextActionsField.fieldId].includes(field.fieldId),
    );
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
              <h1 className="mt-2 text-xl font-semibold tracking-tight">分层行为空间页</h1>
              <p className="mt-2 max-w-4xl text-sm text-[var(--muted)]">
                用角色层、行为层、系统层、证据层和风险层解释“谁触发了哪条真实路径、路径覆盖到哪里、风险暴露在哪、证据和回放如何下钻”。
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
            <div className="rounded-[var(--radius-sm)] border border-[rgba(89,243,194,0.18)] bg-[rgba(89,243,194,0.08)] p-4">
              <div className="text-xs text-[var(--muted)]">是否可上线</div>
              <div className="mt-2 text-base font-semibold text-[var(--fg)]">{launchRecommendation.value}</div>
              <div className="mt-2 text-sm text-[var(--muted)]">{launchRecommendation.supportingText ?? "—"}</div>
              {isSupportedHref(launchRecommendation.href) ? (
                <div className="mt-3">
                  <Link
                    href={launchRecommendation.href}
                    className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.44)] px-2.5 py-1.5 text-xs text-[var(--fg)] hover:border-[rgba(255,255,255,0.18)]"
                  >
                    查看上线建议依据
                  </Link>
                </div>
              ) : null}
            </div>

            <div className="rounded-[var(--radius-sm)] border border-[rgba(255,92,122,0.18)] bg-[rgba(255,92,122,0.08)] p-4">
              <div className="text-xs text-[var(--muted)]">风险成本</div>
              <div className="mt-2 text-base font-semibold text-[var(--fg)]">{riskCost.value}</div>
              <div className="mt-2 text-sm text-[var(--muted)]">{riskCost.supportingText ?? "—"}</div>
              {isSupportedHref(riskCost.href) ? (
                <div className="mt-3">
                  <Link
                    href={riskCost.href}
                    className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.44)] px-2.5 py-1.5 text-xs text-[var(--fg)] hover:border-[rgba(255,255,255,0.18)]"
                  >
                    查看阻断风险证据
                  </Link>
                </div>
              ) : null}
            </div>

            <div className="rounded-[var(--radius-sm)] border border-[rgba(122,167,255,0.18)] bg-[rgba(122,167,255,0.08)] p-4 md:col-span-2 xl:col-span-2">
              <div className="text-xs text-[var(--muted)]">下一步动作</div>
              <div className="mt-2 text-base font-semibold text-[var(--fg)]">{nextActionsField.value}</div>
              <div className="mt-2 text-sm text-[var(--muted)]">
                {nextActionsField.supportingText ?? "优先推进能直接降低上线阻断和风险成本的动作。"}
              </div>
              {nextActionLinks.length ? (
                <div className="mt-3 flex flex-wrap gap-2">
                  {nextActionLinks.slice(0, 3).map((action) => (
                    <Link
                      key={`next-action:${action.label}`}
                      href={action.href}
                      className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.44)] px-2.5 py-1.5 text-xs text-[var(--fg)] hover:border-[rgba(255,255,255,0.18)]"
                    >
                      {action.label}
                    </Link>
                  ))}
                </div>
              ) : null}
            </div>
          </div>

          <div className="mt-4 rounded-[var(--radius-sm)] border border-[rgba(89,243,194,0.16)] bg-[rgba(89,243,194,0.06)] p-4 text-sm text-[var(--muted)]">
            页面优先回答上线建议、风险成本和下一步动作；原始回包、trace 和内部标识继续留在风险详情与回放入口，不在这里退化成技术数据堆叠。
          </div>

          <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-5">
            {supportingFields.map((field) => {
              const safeActions: Array<{ label: string; href: string }> = (field.actions ?? []).flatMap((action) =>
                isSupportedHref(action.href) ? [{ label: action.label, href: action.href }] : [],
              );
              return (
                <div key={field.fieldId} className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(11,15,20,0.48)] p-4">
                  <div className="text-xs text-[var(--muted)]">{field.label}</div>
                  <div className="mt-2 text-base font-semibold text-[var(--fg)]">{field.value}</div>
                  <div className="mt-2 text-sm text-[var(--muted)]">{field.supportingText ?? "—"}</div>
                  {safeActions.length ? (
                    <div className="mt-3 flex flex-wrap gap-2">
                      {safeActions.slice(0, 2).map((action) => (
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
              );
            })}
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

        <div className="grid gap-3 xl:grid-cols-[minmax(0,1.15fr)_minmax(0,0.85fr)]">
          <div id="behavior-space-replay" className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(11,15,20,0.48)] p-4">
            <div className="flex items-center justify-between gap-3">
              <div>
                <div className="text-xs text-[var(--muted)]">回放与证据绑定</div>
                <div className="mt-2 text-base font-semibold text-[var(--fg)]">风险点与行为路径已挂接 reproduction pack</div>
              </div>
              <div className="text-xs text-[var(--muted)]">共 {visualization.replayRefs.length} 项</div>
            </div>
            <div className="mt-4 grid gap-3 md:grid-cols-2">
              {visualization.replayRefs.length ? (
                visualization.replayRefs.slice(0, 4).map((replay) => {
                  const href = buildReplayHref(replay.href);
                  return (
                    <div key={replay.replayRefId} className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.44)] p-4">
                      <div className="text-sm font-semibold text-[var(--fg)]">{replay.label}</div>
                      <div className="mt-2 text-xs text-[var(--muted)]">
                        {replay.pathId ? "已绑定到业务路径与风险详情" : "已绑定到风险详情"} · 复现步骤 {replay.steps.length} 个
                      </div>
                      <div className="mt-2 text-sm text-[var(--muted)]">{replay.summary ?? replay.steps[0] ?? "复现步骤已绑定到风险详情。"}</div>
                      {href ? (
                        <div className="mt-3">
                          <Link
                            href={href}
                            className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.44)] px-2.5 py-1.5 text-xs text-[var(--fg)] hover:border-[rgba(255,255,255,0.18)]"
                          >
                            打开回放入口
                          </Link>
                        </div>
                      ) : null}
                    </div>
                  );
                })
              ) : (
                <div className="text-sm text-[var(--muted)]">当前暂无已绑定的复现包。</div>
              )}
            </div>
          </div>

          <div id="behavior-space-audit" className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(11,15,20,0.48)] p-4">
            <div className="flex items-center justify-between gap-3">
              <div>
                <div className="text-xs text-[var(--muted)]">审计关联</div>
                <div className="mt-2 text-base font-semibold text-[var(--fg)]">交付、审批、导出与签收准备状态</div>
              </div>
              <div className="text-xs text-[var(--muted)]">共 {visualization.auditRefs.length} 条</div>
            </div>
            <div className="mt-4 grid gap-3">
              {visualization.auditRefs.length ? (
                visualization.auditRefs.slice(0, 6).map((audit) => (
                  <div key={audit.auditRefId} className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.44)] p-4">
                    <div className="flex items-center justify-between gap-3">
                      <div className="text-sm font-semibold text-[var(--fg)]">{audit.label}</div>
                      <div className="text-xs text-[var(--muted)]">{formatAuditKind(audit.kind)}</div>
                    </div>
                    <div className="mt-2 text-sm text-[var(--muted)]">{audit.summary}</div>
                    <div className="mt-2 text-xs text-[var(--muted)]">
                      {audit.actor} · {formatAuditTimestamp(audit.timestamp)}
                    </div>
                    {isSupportedHref(audit.href) ? (
                      <div className="mt-3">
                        <Link
                          href={audit.href}
                          className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.44)] px-2.5 py-1.5 text-xs text-[var(--fg)] hover:border-[rgba(255,255,255,0.18)]"
                        >
                          打开关联位置
                        </Link>
                      </div>
                    ) : null}
                  </div>
                ))
              ) : (
                <div className="text-sm text-[var(--muted)]">当前暂无审计关联。</div>
              )}
            </div>
          </div>
        </div>

        <div className="rounded-[var(--radius-md)] border border-[rgba(122,167,255,0.14)] bg-[rgba(122,167,255,0.05)] p-4 text-sm text-[var(--muted)]">
          分层图谱与 2.5D 演示层继续按需加载：页面先返回上线建议、风险成本、coverage、回放和审计关联，再在浏览器空闲或滚动到对应区域时初始化图布局与演示层。
        </div>

        <BehaviorSpaceDeferredVisualizations visualization={visualization} />
      </div>
    );
  } catch (err) {
    const safe = toSafeErrorView(err);
    return (
      <div className="grid gap-4">
        <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(16,24,38,0.55)] p-6 shadow-[var(--shadow-1)] backdrop-blur">
          <div className="text-xs text-[var(--muted)]">Behavior Space</div>
          <h1 className="mt-2 text-xl font-semibold tracking-tight">分层行为空间页</h1>
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
