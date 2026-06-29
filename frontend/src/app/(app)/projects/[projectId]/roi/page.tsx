import Link from "next/link";
import { DecisionSummary } from "@/components/value-summary/DecisionSummary";
import { getCommandCenterSnapshot, toSafeErrorView } from "@/lib/api/command-center";
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

function safeText(value: unknown, fallback = "—"): string {
  if (typeof value === "number") return String(value);
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

export default async function RoiValuePage({ params }: { params: Promise<{ projectId: string }> }) {
  const { projectId } = await params;
  const p = encodeURIComponent(projectId);

  try {
    const snapshotEnvelope = await getCommandCenterSnapshot(projectId);
    const snapshot = pickRecord(snapshotEnvelope.data) ?? {};
    const metrics = pickRecord(snapshot.value_metrics) ?? {};
    const notes = pickArray(metrics.calculation_notes);

    const testPoints = pickNumber(metrics.ai_equivalent_test_points);
    const savedHours = pickNumber(metrics.estimated_hours_saved);
    const trust = pickNumber(metrics.evidence_trust_score);
    const impactMin = pickNumber(metrics.estimated_business_impact_min);
    const impactMax = pickNumber(metrics.estimated_business_impact_max);
    const currency = pickString(metrics.currency);

    return (
      <div className="grid gap-4">
        <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(16,24,38,0.55)] p-6 shadow-[var(--shadow-1)] backdrop-blur">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="text-xs text-[var(--muted)]">ROI</div>
              <h1 className="mt-2 text-xl font-semibold tracking-tight">ROI / 价值量化</h1>
              <p className="mt-2 text-sm text-[var(--muted)]">把后端能力输出转成可决策指标：节省工时、覆盖度、风险成本区间，并可下钻证据。</p>
            </div>
            <Link
              href={`/projects/${p}/reports/executive`}
              className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(14,22,34,0.45)] px-3 py-2 text-sm hover:border-[rgba(255,255,255,0.18)]"
            >
              返回领导层报告
            </Link>
          </div>
        </div>

        <DecisionSummary projectId={projectId} snapshot={snapshotEnvelope.data} />

        <div className="grid gap-3 md:grid-cols-3">
          <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)] p-5">
            <div className="text-xs text-[var(--muted)]">AI 等价测试</div>
            <div className="mt-2 text-2xl font-semibold text-[var(--fg)]">{testPoints === null ? "—" : testPoints}</div>
            <div className="mt-2 text-sm text-[var(--muted)]">等价测试点（脱敏统计口径）</div>
          </div>
          <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)] p-5">
            <div className="text-xs text-[var(--muted)]">节省工时</div>
            <div className="mt-2 text-2xl font-semibold text-[var(--fg)]">{savedHours === null ? "—" : `${savedHours} h`}</div>
            <div className="mt-2 text-sm text-[var(--muted)]">按后端默认口径估算，不包含人力单价</div>
          </div>
          <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)] p-5">
            <div className="text-xs text-[var(--muted)]">风险成本区间</div>
            <div className="mt-2 text-2xl font-semibold text-[var(--fg)]">
              {formatCurrencyRange({ min: impactMin, max: impactMax, currency })}
            </div>
            <div className="mt-2 text-sm text-[var(--muted)]">为风险暴露估算区间，用于上线决策</div>
          </div>
        </div>

        <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)] p-5">
          <div className="text-xs text-[var(--muted)]">证据可信度与说明</div>
          <div className="mt-3 grid gap-2 text-sm text-[var(--muted)]">
            <div>证据可信度：{trust === null ? "—" : trust}</div>
            <div>脱敏状态：{safeText(metrics.redaction_status, "safe")}</div>
          </div>
          <ul className="mt-4 grid gap-2 text-sm text-[var(--muted)]">
            {notes.length ? notes.slice(0, 6).map((item) => <li key={maskId(String(item), 6, 4)}>{safeText(item)}</li>) : <li>—</li>}
          </ul>
          <div className="mt-4 flex flex-wrap gap-2">
            <Link
              href={`/projects/${p}/risks?launch_blocking=true`}
              className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(255,92,122,0.10)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(255,255,255,0.18)]"
            >
              下钻上线阻断证据
            </Link>
            <Link
              href={`/projects/${p}/risks`}
              className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(14,22,34,0.45)] px-3 py-2 text-sm hover:border-[rgba(255,255,255,0.18)]"
            >
              查看全部风险证据
            </Link>
          </div>
        </div>
      </div>
    );
  } catch (err) {
    const safe = toSafeErrorView(err);
    return (
      <div className="grid gap-4">
        <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(16,24,38,0.55)] p-6 shadow-[var(--shadow-1)] backdrop-blur">
          <div className="text-xs text-[var(--muted)]">ROI</div>
          <h1 className="mt-2 text-xl font-semibold tracking-tight">ROI / 价值量化</h1>
        </div>
        <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)] p-5">
          <div className="text-sm font-semibold text-[var(--fg)]">{safe.title}</div>
          <div className="mt-2 text-sm text-[var(--muted)]">{safe.detail}</div>
        </div>
      </div>
    );
  }
}

