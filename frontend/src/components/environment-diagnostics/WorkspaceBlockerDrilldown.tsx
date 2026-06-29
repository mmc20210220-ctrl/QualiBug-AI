"use client";

import { useState } from "react";
import { EvidenceDrawer, type EvidenceDrawerData } from "@/components/evidence/EvidenceDrawer";
import type { EnvironmentDiagnosticGraph, EnvironmentDiagnosticBlocker, GateStatus } from "@/features/environment-diagnostics";

function statusClasses(status: GateStatus): string {
  if (status === "passed") return "border-[rgba(89,243,194,0.32)] bg-[rgba(89,243,194,0.12)] text-[var(--fg)]";
  if (status === "warning") return "border-[rgba(255,196,87,0.32)] bg-[rgba(255,196,87,0.12)] text-[var(--fg)]";
  if (status === "blocked") return "border-[rgba(255,92,122,0.32)] bg-[rgba(255,92,122,0.12)] text-[var(--fg)]";
  if (status === "checking") return "border-[rgba(122,167,255,0.32)] bg-[rgba(122,167,255,0.12)] text-[var(--fg)]";
  return "border-[var(--border)] bg-[rgba(255,255,255,0.04)] text-[var(--muted)]";
}

function statusLabel(status: GateStatus): string {
  if (status === "passed") return "已通过";
  if (status === "warning") return "警告";
  if (status === "blocked") return "阻断";
  if (status === "checking") return "检查中";
  return "未知";
}

function tone(status: GateStatus): EvidenceDrawerData["tone"] {
  if (status === "blocked") return "critical";
  if (status === "warning") return "warning";
  if (status === "passed") return "success";
  if (status === "checking") return "info";
  return "neutral";
}

function buildDrawer(projectId: string, graph: EnvironmentDiagnosticGraph, blocker: EnvironmentDiagnosticBlocker): EvidenceDrawerData {
  const nodes = graph.nodes.filter((node) => blocker.nodeIds.includes(node.nodeId)).map((node) => `${node.label} · ${node.headline}`);
  const gates = graph.gates.filter((gate) => blocker.gateIds.includes(gate.gateId)).map((gate) => `${gate.label} · ${statusLabel(gate.status)}`);
  return {
    id: blocker.blockerId,
    typeLabel: "Workspace Blocker",
    title: blocker.title,
    summary: blocker.summary,
    reason: blocker.reason,
    remediationAction: blocker.remediationAction,
    tone: tone(blocker.status),
    statusLabel: statusLabel(blocker.status),
    fields: [
      { label: "来源页面", value: "首页工作区" },
      { label: "关联节点数", value: String(nodes.length) },
    ],
    sections: [
      { title: "受影响节点", items: nodes.length ? nodes : ["暂无节点映射。"] },
      { title: "关联门禁", items: gates.length ? gates : ["暂无 gate 映射。"] },
    ],
    links: [
      { label: "环境诊断页", href: `/projects/${encodeURIComponent(projectId)}/environment`, kind: "page" },
      { label: "执行闭环", href: `/projects/${encodeURIComponent(projectId)}/execution`, kind: "page" },
    ],
  };
}

export function WorkspaceBlockerDrilldown({ graph }: { graph: EnvironmentDiagnosticGraph }) {
  const [selected, setSelected] = useState<EvidenceDrawerData | null>(null);

  return (
    <>
      <div id="workspace-blockers" className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(8,13,21,0.48)] p-5">
        <div className="text-xs uppercase tracking-[0.22em] text-[var(--muted)]">阻断项摘要</div>
        <div className="mt-4 grid gap-3">
          {graph.blockers.slice(0, 3).map((blocker) => (
            <button
              key={blocker.blockerId}
              type="button"
              onClick={() => setSelected(buildDrawer(graph.projectId, graph, blocker))}
              className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(255,255,255,0.03)] p-4 text-left transition hover:border-[rgba(255,255,255,0.18)]"
            >
              <div className="flex items-center justify-between gap-2">
                <div className="text-sm font-semibold text-[var(--fg)]">{blocker.title}</div>
                <span className={`rounded-full border px-2 py-1 text-[11px] ${statusClasses(blocker.status)}`}>{statusLabel(blocker.status)}</span>
              </div>
              <div className="mt-2 text-sm text-[var(--muted)]">{blocker.summary}</div>
              <div className="mt-2 text-xs text-[var(--muted)]">动作：{blocker.remediationAction}</div>
              <div className="mt-3 text-xs text-[rgba(122,167,255,0.88)]">点击查看统一 Evidence Drawer</div>
            </button>
          ))}
        </div>
      </div>
      <EvidenceDrawer open={Boolean(selected)} data={selected} onClose={() => setSelected(null)} />
    </>
  );
}
