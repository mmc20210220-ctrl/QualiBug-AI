"use client";

import Link from "next/link";
import { buildEnvironmentDiagnosticsMockGraph } from "@/features/environment-diagnostics/mock";
import type { GateStatus } from "@/features/environment-diagnostics/model";

const eventTone: Record<GateStatus, string> = {
  passed: "border-[rgba(89,243,194,0.32)] bg-[rgba(89,243,194,0.10)]",
  warning: "border-[rgba(255,196,87,0.32)] bg-[rgba(255,196,87,0.10)]",
  blocked: "border-[rgba(255,92,122,0.32)] bg-[rgba(255,92,122,0.10)]",
  checking: "border-[rgba(122,167,255,0.32)] bg-[rgba(122,167,255,0.10)]",
  unknown: "border-[var(--border)] bg-[rgba(255,255,255,0.04)]",
};

function statusLabel(status: GateStatus): string {
  if (status === "passed") return "passed";
  if (status === "warning") return "warning";
  if (status === "blocked") return "blocked";
  if (status === "checking") return "checking";
  return "unknown";
}

export function ProjectEventStream({ projectId }: { projectId: string }) {
  const graph = buildEnvironmentDiagnosticsMockGraph(projectId);
  const events = [
    {
      id: "event-env-gate",
      time: graph.summary.checkedAt ?? "demo",
      status: graph.summary.status,
      title: "环境门禁已完成预检",
      message: graph.summary.verdict,
      href: `/projects/${encodeURIComponent(projectId)}/environment`,
    },
    ...graph.blockers.slice(0, 3).map((blocker) => ({
      id: `event-${blocker.blockerId}`,
      time: "gate",
      status: blocker.status,
      title: blocker.title,
      message: blocker.remediationAction,
      href: `/projects/${encodeURIComponent(projectId)}/environment#environment-blockers`,
    })),
  ];

  return (
    <section className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[linear-gradient(180deg,rgba(14,22,34,0.62),rgba(8,13,21,0.88))] p-4 shadow-[var(--shadow-1)] backdrop-blur">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="text-xs uppercase tracking-[0.22em] text-[var(--muted)]">Runtime Event Stream</div>
          <h2 className="mt-1 text-base font-semibold text-[var(--fg)]">运行门禁事件流</h2>
        </div>
        <span className="rounded-full border border-[rgba(89,243,194,0.32)] bg-[rgba(89,243,194,0.10)] px-3 py-1 text-xs text-[rgba(213,255,244,0.96)]">
          connected(sse) · demo-safe
        </span>
      </div>

      <div className="mt-4 grid gap-3 lg:grid-cols-4">
        {events.map((event) => (
          <Link
            key={event.id}
            href={event.href}
            className={`group rounded-[var(--radius-sm)] border p-3 transition hover:-translate-y-0.5 hover:border-[rgba(255,255,255,0.22)] ${eventTone[event.status]}`}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="text-[11px] uppercase tracking-[0.18em] text-[var(--muted)]">{statusLabel(event.status)}</span>
              <span className="text-[11px] text-[var(--muted)]">{event.time}</span>
            </div>
            <div className="mt-2 text-sm font-semibold text-[var(--fg)]">{event.title}</div>
            <p className="mt-2 line-clamp-2 text-xs leading-5 text-[var(--muted)]">{event.message}</p>
          </Link>
        ))}
      </div>
    </section>
  );
}
