import Link from "next/link";
import type { EnvironmentDiagnosticGraph, GateStatus } from "@/features/environment-diagnostics";

const stageMeta = [
  {
    key: "environment",
    eyebrow: "Layer 01",
    title: "客户环境层",
    description: "DNS / VPN / Proxy / TLS / Auth / 账号矩阵",
    hrefSuffix: "/environment",
  },
  {
    key: "behavior",
    eyebrow: "Layer 02",
    title: "行为空间层",
    description: "角色 / API / 服务 / 数据对象 / 状态机 / 覆盖路径",
    hrefSuffix: "/behavior-space",
  },
  {
    key: "runtime",
    eyebrow: "Layer 03",
    title: "Runtime 执行层",
    description: "探针、请求、响应、before/after snapshot 和执行剧场",
    hrefSuffix: "/execution",
  },
  {
    key: "evidence",
    eyebrow: "Layer 04",
    title: "证据交付层",
    description: "Finding / 复现包 / Manifest / Gate / Secret Audit",
    hrefSuffix: "/risks",
  },
] as const;

function statusClass(status: GateStatus): string {
  if (status === "passed") return "border-[rgba(89,243,194,0.38)] bg-[rgba(89,243,194,0.12)]";
  if (status === "warning") return "border-[rgba(255,196,87,0.38)] bg-[rgba(255,196,87,0.12)]";
  if (status === "blocked") return "border-[rgba(255,92,122,0.42)] bg-[rgba(255,92,122,0.14)]";
  if (status === "checking") return "border-[rgba(122,167,255,0.38)] bg-[rgba(122,167,255,0.12)]";
  return "border-[var(--border)] bg-[rgba(255,255,255,0.04)]";
}

function statusText(status: GateStatus): string {
  if (status === "passed") return "Ready";
  if (status === "warning") return "Limited";
  if (status === "blocked") return "Blocked";
  if (status === "checking") return "Checking";
  return "Unknown";
}

function stageStatus(graph: EnvironmentDiagnosticGraph, stage: (typeof stageMeta)[number]["key"]): GateStatus {
  if (stage === "environment") return graph.summary.status;
  if (stage === "behavior") return graph.gates.some((gate) => gate.gateId === "readonly_probe_allowed" && gate.allowed) ? "warning" : "blocked";
  if (stage === "runtime") return graph.gates.some((gate) => gate.gateId === "runtime_start_allowed" && gate.allowed) ? "passed" : "blocked";
  if (stage === "evidence") return graph.blockers.some((blocker) => blocker.status === "blocked") ? "warning" : "passed";
  return "unknown";
}

export function ProjectDigitalTwinStage({ projectId, graph }: { projectId: string; graph: EnvironmentDiagnosticGraph }) {
  const p = encodeURIComponent(projectId);
  const runtimeGate = graph.gates.find((gate) => gate.gateId === "runtime_start_allowed") ?? graph.gates[0];
  const blockerCount = graph.blockers.filter((blocker) => blocker.status === "blocked" || blocker.status === "warning").length;
  const coveredGates = graph.gates.filter((gate) => gate.allowed).length;

  return (
    <section className="overflow-hidden rounded-[var(--radius-md)] border border-[var(--border)] bg-[radial-gradient(circle_at_28%_0%,rgba(89,243,194,0.14),transparent_34%),radial-gradient(circle_at_86%_10%,rgba(122,167,255,0.16),transparent_38%),linear-gradient(180deg,rgba(16,24,38,0.86),rgba(6,10,18,0.96))] p-5 shadow-[var(--shadow-1)]">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="text-xs uppercase tracking-[0.24em] text-[var(--muted)]">QualiBug Evidence Digital Twin</div>
          <h2 className="mt-2 text-xl font-semibold tracking-tight text-[var(--fg)]">系统行为数字孪生驾驶舱</h2>
          <p className="mt-2 max-w-4xl text-sm leading-6 text-[var(--muted)]">
            把客户环境、行为空间、Runtime 执行和证据交付压缩到一张可解释主舞台：先看能不能跑，再看卡在哪，再进入真实路径和证据回放。
          </p>
        </div>
        <Link
          href={`/projects/${p}/environment`}
          className={`rounded-full border px-4 py-2 text-sm text-[var(--fg)] transition hover:border-[rgba(255,255,255,0.28)] ${statusClass(runtimeGate.status)}`}
        >
          {runtimeGate.allowed ? "Runtime Ready" : "Runtime Hold"}
        </Link>
      </div>

      <div className="mt-5 grid gap-3 md:grid-cols-3">
        <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(8,13,21,0.55)] p-4">
          <div className="text-xs text-[var(--muted)]">环境就绪评分</div>
          <div className="mt-2 text-3xl font-semibold text-[var(--fg)]">{graph.summary.score ?? "—"}</div>
          <div className="mt-1 text-xs text-[var(--muted)]">先过环境门禁，再谈 bug 发现率</div>
        </div>
        <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(8,13,21,0.55)] p-4">
          <div className="text-xs text-[var(--muted)]">可放行门禁</div>
          <div className="mt-2 text-3xl font-semibold text-[var(--fg)]">{coveredGates}/{graph.gates.length}</div>
          <div className="mt-1 text-xs text-[var(--muted)]">只读、写入、P0/P1 与正式 runtime</div>
        </div>
        <div className="rounded-[var(--radius-sm)] border border-[rgba(255,92,122,0.24)] bg-[rgba(255,92,122,0.08)] p-4">
          <div className="text-xs text-[var(--muted)]">阻断/预警</div>
          <div className="mt-2 text-3xl font-semibold text-[var(--fg)]">{blockerCount}</div>
          <div className="mt-1 text-xs text-[var(--muted)]">必须能点击、能解释、能行动</div>
        </div>
      </div>

      <div className="relative mt-6 overflow-x-auto rounded-[28px] border border-[rgba(255,255,255,0.08)] bg-[linear-gradient(180deg,rgba(255,255,255,0.05),rgba(8,13,21,0.82))] p-4">
        <div className="min-w-[920px] [perspective:1200px]">
          <div className="grid grid-cols-4 gap-4 [transform:rotateX(52deg)_rotateZ(-2deg)] [transform-style:preserve-3d]">
            {stageMeta.map((stage, index) => {
              const status = stageStatus(graph, stage.key);
              return (
                <Link
                  key={stage.key}
                  href={`/projects/${p}${stage.hrefSuffix}`}
                  className={`group min-h-[230px] rounded-[26px] border p-5 shadow-[0_30px_70px_rgba(0,0,0,0.26)] transition hover:-translate-y-2 hover:border-[rgba(255,255,255,0.26)] ${statusClass(status)}`}
                  style={{ transform: `translateZ(${index * 18}px)` }}
                >
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-[11px] uppercase tracking-[0.22em] text-[var(--muted)]">{stage.eyebrow}</span>
                    <span className="rounded-full border border-[rgba(255,255,255,0.14)] bg-[rgba(0,0,0,0.18)] px-2 py-1 text-[11px] text-[var(--fg)]">
                      {statusText(status)}
                    </span>
                  </div>
                  <div className="mt-8 text-lg font-semibold text-[var(--fg)]">{stage.title}</div>
                  <p className="mt-3 text-sm leading-6 text-[var(--muted)]">{stage.description}</p>
                  <div className="mt-6 h-2 rounded-full bg-[rgba(255,255,255,0.08)]">
                    <div
                      className="h-2 rounded-full bg-[linear-gradient(90deg,rgba(89,243,194,0.85),rgba(122,167,255,0.85))]"
                      style={{ width: status === "blocked" ? "38%" : status === "warning" ? "68%" : "92%" }}
                    />
                  </div>
                </Link>
              );
            })}
          </div>
        </div>
        <div className="pointer-events-none absolute inset-x-8 bottom-8 h-px bg-[linear-gradient(90deg,transparent,rgba(89,243,194,0.55),rgba(122,167,255,0.45),transparent)]" />
      </div>
    </section>
  );
}
