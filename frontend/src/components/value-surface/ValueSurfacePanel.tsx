import { assertValueSurfaceRegistry, buildValueSurfaceRegistry, type PageId } from "@/value_surface";

const registry = buildValueSurfaceRegistry();
assertValueSurfaceRegistry(registry);

export function ValueSurfacePanel({ pageId }: { pageId: PageId }) {
  const page = registry.pages.find((p) => p.pageId === pageId);

  if (!page) {
    return (
      <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)] p-5 text-sm text-[var(--muted)]">
        未找到 pageId: {pageId}
      </div>
    );
  }

  return (
    <section className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)] p-5">
      <div className="flex items-center justify-between gap-3">
        <div>
          <div className="text-xs text-[var(--muted)]">价值呈现层（Task0）</div>
          <div className="mt-1 text-sm font-semibold">pageId: {page.pageId}</div>
        </div>
        <div className="text-xs text-[var(--muted)]">
          metrics {page.metrics.length} · acceptance {page.acceptance.length}
        </div>
      </div>

      <div className="mt-4 grid gap-3 md:grid-cols-2">
        <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.40)] p-4">
          <div className="text-xs text-[var(--muted)]">关键指标</div>
          <ul className="mt-2 grid gap-2 text-sm">
            {page.metrics.slice(0, 6).map((m) => (
              <li key={m.metricId} className="flex items-center justify-between gap-3">
                <span className="text-[var(--fg)]">{m.label}</span>
                <span className="text-xs text-[var(--muted)]">{m.field}</span>
              </li>
            ))}
            {page.metrics.length === 0 ? <li className="text-[var(--muted)]">（暂无）</li> : null}
          </ul>
        </div>

        <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.40)] p-4">
          <div className="text-xs text-[var(--muted)]">价值验收点</div>
          <ul className="mt-2 grid gap-2 text-sm text-[var(--muted)]">
            {page.acceptance.slice(0, 5).map((a) => (
              <li key={a.acceptanceId}>
                <span className="text-[var(--fg)]">{a.must ? "Must" : "Should"}</span> · {a.statement}
              </li>
            ))}
            {page.acceptance.length === 0 ? <li>（暂无）</li> : null}
          </ul>
        </div>
      </div>
    </section>
  );
}

