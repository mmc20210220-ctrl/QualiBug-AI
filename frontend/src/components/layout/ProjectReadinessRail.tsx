"use client";

import Link from "next/link";
import { buildEnvironmentDiagnosticsMockGraph } from "@/features/environment-diagnostics/mock";
import type { EnvironmentDiagnosticGate, GateStatus } from "@/features/environment-diagnostics/model";

function statusMeta(status: GateStatus): { label: string; className: string; dot: string } {
  if (status === "passed") {
    return {
      label: "已通过",
      className: "border-[rgba(89,243,194,0.30)] bg-[rgba(89,243,194,0.10)] text-[rgba(213,255,244,0.96)]",
      dot: "bg-[rgba(89,243,194,0.95)]",
    };
  }
  if (status === "warning") {
    return {
      label: "警告",
      className: "border-[rgba(255,196,87,0.32)] bg-[rgba(255,196,87,0.10)] text-[rgba(255,239,199,0.96)]",
      dot: "bg-[rgba(255,196,87,0.95)]",
    };
  }
  if (status === "blocked") {
    return {
      label: "阻断",
      className: "border-[rgba(255,92,122,0.34)] bg-[rgba(255,92,122,0.12)] text-[rgba(255,220,226,0.96)]",
      dot: "bg-[rgba(255,92,122,0.95)]",
    };
  }
  if (status === "checking") {
    return {
      label: "检查中",
      className: "border-[rgba(122,167,255,0.32)] bg-[rgba(122,167,255,0.10)] text-[rgba(222,233,255,0.96)]",
      dot: "bg-[rgba(122,167,255,0.95)]",
    };
  }
  return {
    label: "未知",
    className: "border-[var(--border)] bg-[rgba(255,255,255,0.04)] text-[var(--muted)]",
    dot: "bg-[rgba(148,163,184,0.85)]",
  };
}

function GateRow({ gate }: { gate: EnvironmentDiagnosticGate }) {
  const meta = statusMeta(gate.status);
  return (
    <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(8,13,21,0.52)] p-3">
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0 text-sm font-medium text-[var(--fg)]">{gate.label}</div>
        <span className={`shrink-0 rounded-full border px-2 py-1 text-[11px] ${meta.className}`}>{meta.label}</span>
      </div>
      <div className="mt-2 flex items-center gap-2 text-xs text-[var(--muted)]">
        <span className={`h-2 w-2 rounded-full ${meta.dot}`} />
        <span>{gate.allowed ? "允许推进" : "暂不放行"}</span>
      </div>
    </div>
  );
}

export function ProjectReadinessRail({ projectId }: { projectId: string }) {
  const graph = buildEnvironmentDiagnosticsMockGraph(projectId);
  const blockedGates = graph.gates.filter((gate) => !gate.allowed || gate.status === "blocked");
  const p0Blocker = graph.blockers.find((blocker) => blocker.status === "blocked") ?? graph.blockers[0];
  const projectBase = `/projects/${encodeURIComponent(projectId)}`;
  const runtimeGate = graph.gates.find((gate) => gate.gateId === "runtime_start_allowed") ?? graph.gates[0];
  const runtimeMeta = statusMeta(runtimeGate?.status ?? graph.summary.status);

  return (
    <section className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[linear-gradient(180deg,rgba(16,24,38,0.82),rgba(8,13,21,0.96))] p-4 shadow-[var(--shadow-1)] backdrop-blur">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-xs uppercase tracking-[0.22em] text-[var(--muted)]">Environment Gate</div>
          <h2 className="mt-2 text-base font-semibold text-[var(--fg)]">客户环境门禁</h2>
        </div>
        <span className={`rounded-full border px-2.5 py-1 text-[11px] ${runtimeMeta.className}`}>{runtimeMeta.label}</span>
      </div>

      <div className="mt-4 rounded-[var(--radius-sm)] border border-[rgba(255,255,255,0.08)] bg-[rgba(255,255,255,0.035)] p-3">
        <div className="text-xs text-[var(--muted)]">Runtime Start</div>
        <div className="mt-2 text-lg font-semibold text-[var(--fg)]">{runtimeGate?.allowed ? "允许启动" : "上线前先关闭阻断"}</div>
        <p className="mt-2 text-sm leading-6 text-[var(--muted)]">{graph.summary.reason}</p>
      </div>

      <div className="mt-4 grid gap-2">
        {graph.gates.map((gate) => (
          <GateRow key={gate.gateId} gate={gate} />
        ))}
      </div>

      {p0Blocker ? (
        <div className="mt-4 rounded-[var(--radius-sm)] border border-[rgba(255,92,122,0.22)] bg-[rgba(255,92,122,0.08)] p-3">
          <div className="text-xs text-[rgba(255,220,226,0.88)]">优先阻断项</div>
          <div className="mt-2 text-sm font-semibold text-[var(--fg)]">{p0Blocker.title}</div>
          <p className="mt-2 text-xs leading-5 text-[var(--muted)]">{p0Blocker.remediationAction}</p>
        </div>
      ) : null}

      <div className="mt-4 grid gap-2">
        <Link
          href={`${projectBase}/environment`}
          className="rounded-[var(--radius-sm)] border border-[rgba(255,196,87,0.26)] bg-[rgba(255,196,87,0.10)] px-3 py-2 text-center text-sm text-[var(--fg)] hover:border-[rgba(255,196,87,0.44)]"
        >
          打开环境诊断
        </Link>
        <Link
          href={`${projectBase}/execution`}
          className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(14,22,34,0.58)] px-3 py-2 text-center text-sm text-[var(--muted)] hover:text-[var(--fg)]"
        >
          查看执行链路
        </Link>
      </div>

      <div className="mt-4 border-t border-[var(--border)] pt-3 text-xs text-[var(--muted)]">
        阻断门禁 {blockedGates.length} 个 · 客户补料 {graph.requiredCustomerInputs.length} 项 · 不展示真实 host/token/secret
      </div>
    </section>
  );
}
