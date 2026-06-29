import Link from "next/link";
import { buildBehaviorSpaceValueSummary } from "@/behavior-space";
import { maskId } from "@/lib/redact";

export function DecisionSummary({ projectId, snapshot }: { projectId: string; snapshot: unknown }) {
  const summary = buildBehaviorSpaceValueSummary({ projectId, commandCenter: snapshot });

  return (
    <section className="grid gap-3 md:grid-cols-3">
      <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)] p-5">
        <div className="text-xs text-[var(--muted)]">是否可上线</div>
        <div className="mt-2 text-base font-semibold text-[var(--fg)]">{summary.launchRecommendation.value}</div>
        <div className="mt-2 text-sm text-[var(--muted)]">{summary.launchRecommendation.supportingText ?? "—"}</div>
        <div className="mt-4 flex flex-wrap gap-2 text-xs text-[var(--muted)]">
          <span>{summary.coverage.label} {summary.coverage.supportingText ?? summary.coverage.value}</span>
          <span>{summary.efficiencyGain.label} {summary.efficiencyGain.value}</span>
        </div>
      </div>

      <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)] p-5">
        <div className="text-xs text-[var(--muted)]">风险成本</div>
        <div className="mt-2 text-base font-semibold text-[var(--fg)]">{summary.riskCost.value}</div>
        <div className="mt-2 text-sm text-[var(--muted)]">{summary.riskCost.supportingText ?? "—"}</div>
        <div className="mt-4">
          <Link
            href={summary.riskCost.href ?? `/projects/${encodeURIComponent(projectId)}/risks?launch_blocking=true`}
            className="inline-flex items-center rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(255,92,122,0.10)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(255,255,255,0.18)]"
          >
            查看上线阻断证据
          </Link>
        </div>
      </div>

      <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)] p-5">
        <div className="text-xs text-[var(--muted)]">下一步动作</div>
        <ul className="mt-3 grid gap-2 text-sm text-[var(--muted)]">
          {summary.nextActions.actions?.length ? (
            summary.nextActions.actions.slice(0, 4).map((item) => (
              <li key={maskId(item.label, 6, 4)}>
                {item.href ? (
                  <Link href={item.href} className="text-[var(--fg)] hover:underline">
                    {item.label}
                  </Link>
                ) : (
                  item.label
                )}
              </li>
            ))
          ) : (
            <li>—</li>
          )}
        </ul>
      </div>
    </section>
  );
}
