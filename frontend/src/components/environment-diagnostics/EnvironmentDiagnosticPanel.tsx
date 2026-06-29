"use client";

import Link from "next/link";
import type { EnvironmentDiagnosticGraph, GateStatus } from "@/features/environment-diagnostics";

function statusClasses(status: GateStatus): string {
  if (status === "passed") return "border-[rgba(89,243,194,0.28)] bg-[rgba(89,243,194,0.10)] text-[var(--fg)]";
  if (status === "warning") return "border-[rgba(255,196,87,0.28)] bg-[rgba(255,196,87,0.10)] text-[var(--fg)]";
  if (status === "blocked") return "border-[rgba(255,92,122,0.28)] bg-[rgba(255,92,122,0.10)] text-[var(--fg)]";
  if (status === "checking") return "border-[rgba(122,167,255,0.28)] bg-[rgba(122,167,255,0.10)] text-[var(--fg)]";
  return "border-[var(--border)] bg-[rgba(255,255,255,0.04)] text-[var(--muted)]";
}

function statusLabel(status: GateStatus): string {
  if (status === "passed") return "已通过";
  if (status === "warning") return "警告";
  if (status === "blocked") return "阻断";
  if (status === "checking") return "检查中";
  return "未知";
}

function gateLabel(label: string, allowed: boolean): string {
  return `${label} · ${allowed ? "允许" : "未允许"}`;
}

export function EnvironmentDiagnosticPanel({
  graph,
  mode = "detail",
  onSelectNode,
  onSelectBlocker,
}: {
  graph: EnvironmentDiagnosticGraph;
  mode?: "summary" | "detail";
  onSelectNode?: (nodeId: string) => void;
  onSelectBlocker?: (blockerId: string) => void;
}) {
  const blockerLimit = mode === "summary" ? 3 : graph.blockers.length;
  const nodeLimit = mode === "summary" ? 6 : graph.nodes.length;

  return (
    <section className="grid gap-4">
      <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)] p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-xs text-[var(--muted)]">Environment Gate / 客户环境门禁</div>
            <h2 className="mt-2 text-lg font-semibold tracking-tight text-[var(--fg)]">{graph.summary.verdict}</h2>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-[var(--muted)]">{graph.summary.reason}</p>
          </div>
          <div className="flex flex-wrap gap-2 text-xs text-[var(--muted)]">
            <span className={`rounded-full border px-3 py-1 ${statusClasses(graph.summary.status)}`}>{statusLabel(graph.summary.status)}</span>
            <span className="rounded-full border border-[var(--border)] bg-[rgba(255,255,255,0.04)] px-3 py-1">
              评分 {graph.summary.score ?? "—"}
            </span>
            <span className="rounded-full border border-[var(--border)] bg-[rgba(255,255,255,0.04)] px-3 py-1">
              模式 {graph.summary.safeExecutionMode}
            </span>
          </div>
        </div>

        <div className="mt-5 grid gap-3 lg:grid-cols-4">
          {graph.gates.map((gate) => (
            <div key={gate.gateId} className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(10,16,27,0.55)] p-4">
              <div className="flex items-center justify-between gap-2">
                <div className="text-sm font-medium text-[var(--fg)]">{gate.label}</div>
                <span className={`rounded-full border px-2 py-1 text-[11px] ${statusClasses(gate.status)}`}>{statusLabel(gate.status)}</span>
              </div>
              <div className="mt-2 text-xs text-[var(--muted)]">{gateLabel(gate.summary, gate.allowed)}</div>
              <div className="mt-3 text-sm text-[var(--muted)]">{gate.reason}</div>
              <div className="mt-3 text-xs text-[var(--muted)]">下一步：{gate.remediationAction ?? "—"}</div>
            </div>
          ))}
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.25fr)_minmax(0,0.95fr)]">
        <div id="environment-nodes" className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)] p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="text-xs text-[var(--muted)]">环境节点</div>
              <div className="mt-1 text-sm text-[var(--muted)]">所有环境诊断 UI 统一从同一份图模型读取节点、门禁和阻断关系。</div>
            </div>
            {mode === "summary" ? (
              <Link
                href={`/projects/${encodeURIComponent(graph.projectId)}/environment`}
                className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(122,167,255,0.10)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(122,167,255,0.30)]"
              >
                打开环境页
              </Link>
            ) : null}
          </div>

          <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {graph.nodes.slice(0, nodeLimit).map((node) => (
              <article key={node.nodeId} className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(8,13,21,0.58)] p-4">
                <div className="flex items-center justify-between gap-2">
                  <div className="text-sm font-medium text-[var(--fg)]">{node.label}</div>
                  <span className={`rounded-full border px-2 py-1 text-[11px] ${statusClasses(node.status)}`}>{statusLabel(node.status)}</span>
                </div>
                <div className="mt-2 text-sm text-[var(--fg)]">{node.headline}</div>
                <div className="mt-2 text-sm leading-6 text-[var(--muted)]">{node.detail}</div>
                <div className="mt-2 text-xs text-[var(--muted)]">业务解释：{node.businessExplanation}</div>
                <div className="mt-2 text-xs text-[var(--muted)]">下一步：{node.nextAction}</div>
                {onSelectNode ? (
                  <button
                    type="button"
                    onClick={() => onSelectNode(node.nodeId)}
                    className="mt-3 inline-flex min-h-[40px] items-center justify-center rounded-[var(--radius-sm)] border border-[rgba(122,167,255,0.24)] bg-[rgba(122,167,255,0.08)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(122,167,255,0.42)]"
                  >
                    查看 Evidence Drawer
                  </button>
                ) : null}
              </article>
            ))}
          </div>
        </div>

        <div className="grid gap-4">
          <div id="environment-blockers" className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)] p-5">
            <div className="text-xs text-[var(--muted)]">阻断项摘要</div>
            <div className="mt-1 text-sm text-[var(--muted)]">阻断项与 remediation action 同样从 `EnvironmentDiagnosticGraph` 的 blocker 列表读取。</div>
            <div className="mt-4 space-y-3">
              {graph.blockers.slice(0, blockerLimit).map((blocker) => (
                <article key={blocker.blockerId} className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(8,13,21,0.58)] p-4">
                  <div className="flex items-center justify-between gap-2">
                    <div className="text-sm font-medium text-[var(--fg)]">{blocker.title}</div>
                    <span className={`rounded-full border px-2 py-1 text-[11px] ${statusClasses(blocker.status)}`}>{statusLabel(blocker.status)}</span>
                  </div>
                  <div className="mt-2 text-sm text-[var(--muted)]">{blocker.summary}</div>
                  <div className="mt-2 text-xs text-[var(--muted)]">原因：{blocker.reason}</div>
                  <div className="mt-2 text-xs text-[var(--muted)]">动作：{blocker.remediationAction}</div>
                  {onSelectBlocker ? (
                    <button
                      type="button"
                      onClick={() => onSelectBlocker(blocker.blockerId)}
                    className="mt-3 inline-flex min-h-[40px] items-center justify-center rounded-[var(--radius-sm)] border border-[rgba(255,92,122,0.24)] bg-[rgba(255,92,122,0.08)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(255,92,122,0.42)]"
                    >
                      查看 Evidence Drawer
                    </button>
                  ) : null}
                </article>
              ))}
            </div>
          </div>

          <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)] p-5">
            <div className="text-xs text-[var(--muted)]">客户补料与建议动作</div>
            <div className="mt-4 grid gap-3">
              {graph.requiredCustomerInputs.map((item) => (
                <article key={item.inputId} className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(8,13,21,0.58)] p-4">
                  <div className="flex items-center justify-between gap-2">
                    <div className="text-sm font-medium text-[var(--fg)]">{item.title}</div>
                    <span className="rounded-full border border-[var(--border)] bg-[rgba(255,255,255,0.04)] px-2 py-1 text-[11px] text-[var(--muted)]">
                      {item.priority.toUpperCase()} / {item.status}
                    </span>
                  </div>
                  <div className="mt-2 text-sm text-[var(--muted)]">{item.whyNeeded}</div>
                  <div className="mt-2 text-xs text-[var(--muted)]">建议提供：{item.suggestedInput}</div>
                </article>
              ))}
              {graph.requiredCustomerInputs.length === 0 ? (
                <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(8,13,21,0.58)] p-4 text-sm text-[var(--muted)]">
                  当前没有额外客户补料项。
                </div>
              ) : null}
            </div>

            <div className="mt-4 rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(8,13,21,0.58)] p-4">
              <div className="text-sm font-medium text-[var(--fg)]">建议动作队列</div>
              <ul className="mt-3 grid gap-2 text-sm text-[var(--muted)]">
                {graph.suggestedActions.map((action, index) => (
                  <li key={`suggested-action:${index}:${action}`}>{action}</li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
