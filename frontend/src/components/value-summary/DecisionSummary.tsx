import Link from "next/link";
import { redactUnknown, maskId } from "@/lib/redact";

function pickRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

function pickString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function pickNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function pickArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function safeText(value: unknown, fallback: string): string {
  const raw = pickString(value);
  if (!raw) return fallback;
  const redacted = redactUnknown(raw);
  return typeof redacted === "string" ? redacted : fallback;
}

function formatCurrencyRange(input: { min?: number | null; max?: number | null; currency?: string | null }): string {
  const min = input.min ?? null;
  const max = input.max ?? null;
  const ccy = input.currency ?? "CNY";
  if (min === null && max === null) return "—";
  if (min !== null && max !== null) return `${min.toLocaleString()} - ${max.toLocaleString()} ${ccy}`;
  return `${(min ?? max ?? 0).toLocaleString()} ${ccy}`;
}

export function DecisionSummary({ projectId, snapshot }: { projectId: string; snapshot: unknown }) {
  const root = pickRecord(snapshot) ?? {};
  const launch = pickRecord(root.launch_decision) ?? {};
  const metrics = pickRecord(root.value_metrics) ?? {};
  const risk = pickRecord(root.risk_summary) ?? {};

  const launchTitle = safeText(launch.title, "暂无上线建议");
  const launchSummary = safeText(launch.summary, "当前没有足够结果支撑上线建议。");
  const required = pickArray(launch.required_actions).slice(0, 4).map((item) => safeText(item, "—"));

  const blockers = pickNumber(risk.launch_blocking) ?? pickNumber(risk.launch_blocking_risks) ?? null;
  const impactMin = pickNumber(metrics.estimated_business_impact_min);
  const impactMax = pickNumber(metrics.estimated_business_impact_max);
  const currency = pickString(metrics.currency);

  const coverage = pickNumber(metrics.business_flow_coverage_rate);
  const saved = pickNumber(metrics.estimated_hours_saved);

  const p = encodeURIComponent(projectId);
  const blockerHref = `/projects/${p}/risks?launch_blocking=true`;

  return (
    <section className="grid gap-3 md:grid-cols-3">
      <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)] p-5">
        <div className="text-xs text-[var(--muted)]">是否可上线</div>
        <div className="mt-2 text-base font-semibold text-[var(--fg)]">{launchTitle}</div>
        <div className="mt-2 text-sm text-[var(--muted)]">{launchSummary}</div>
        <div className="mt-4 flex flex-wrap gap-2 text-xs text-[var(--muted)]">
          {coverage !== null ? <span>覆盖率 {(coverage * 100).toFixed(0)}%</span> : null}
          {saved !== null ? <span>节省工时 {saved}h</span> : null}
        </div>
      </div>

      <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)] p-5">
        <div className="text-xs text-[var(--muted)]">风险成本</div>
        <div className="mt-2 text-base font-semibold text-[var(--fg)]">
          {formatCurrencyRange({ min: impactMin, max: impactMax, currency })}
        </div>
        <div className="mt-2 text-sm text-[var(--muted)]">
          上线阻断风险 {blockers === null ? "—" : String(blockers)} 个（可下钻查看证据链）
        </div>
        <div className="mt-4">
          <Link
            href={blockerHref}
            className="inline-flex items-center rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(255,92,122,0.10)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(255,255,255,0.18)]"
          >
            查看上线阻断证据
          </Link>
        </div>
      </div>

      <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)] p-5">
        <div className="text-xs text-[var(--muted)]">下一步动作</div>
        <ul className="mt-3 grid gap-2 text-sm text-[var(--muted)]">
          {required.length ? required.map((item) => <li key={maskId(item, 6, 4)}>{item}</li>) : <li>—</li>}
        </ul>
      </div>
    </section>
  );
}

