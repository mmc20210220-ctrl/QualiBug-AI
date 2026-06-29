"use client";

import { useMemo, useState } from "react";
import { EvidenceDrawer, type EvidenceDrawerData } from "@/components/evidence/EvidenceDrawer";
import { EnvironmentDiagnosticPanel } from "@/components/environment-diagnostics/EnvironmentDiagnosticPanel";
import { EnvironmentTopology2D5 } from "@/components/environment-diagnostics/EnvironmentTopology2D5";
import type {
  EnvironmentDiagnosticBlocker,
  EnvironmentDiagnosticGraph,
  EnvironmentDiagnosticNode,
  GateStatus,
} from "@/features/environment-diagnostics";

function statusLabel(status: GateStatus): string {
  if (status === "passed") return "已通过";
  if (status === "warning") return "警告";
  if (status === "blocked") return "阻断";
  if (status === "checking") return "检查中";
  return "未知";
}

function statusTone(status: GateStatus): EvidenceDrawerData["tone"] {
  if (status === "blocked") return "critical";
  if (status === "warning") return "warning";
  if (status === "passed") return "success";
  if (status === "checking") return "info";
  return "neutral";
}

function buildNodeEvidence(projectId: string, node: EnvironmentDiagnosticNode, graph: EnvironmentDiagnosticGraph): EvidenceDrawerData {
  const relatedBlockers = graph.blockers.filter((blocker) => blocker.nodeIds.includes(node.nodeId)).map((blocker) => blocker.title);
  const relatedGates = graph.gates.filter((gate) => node.gateIds.includes(gate.gateId)).map((gate) => `${gate.label} · ${statusLabel(gate.status)}`);
  return {
    id: node.nodeId,
    typeLabel: "Environment Evidence",
    title: node.label,
    summary: node.headline,
    reason: `${node.detail} 业务解释：${node.businessExplanation}`,
    remediationAction: node.nextAction,
    tone: statusTone(node.status),
    statusLabel: statusLabel(node.status),
    fields: [
      { label: "阶段", value: node.stageId },
      { label: "节点类型", value: node.kind },
      { label: "阻断关联数", value: String(relatedBlockers.length) },
      { label: "门禁关联数", value: String(relatedGates.length) },
    ],
    sections: [
      { title: "关联阻断项", items: relatedBlockers.length ? relatedBlockers : ["当前节点没有直接挂接 blocker。"] },
      { title: "关联门禁", items: relatedGates.length ? relatedGates : ["当前节点没有直接挂接 gate。"] },
    ],
    links: [
      { label: "环境诊断页", href: `/projects/${encodeURIComponent(projectId)}/environment`, kind: "page" },
      { label: "Behavior Space", href: `/projects/${encodeURIComponent(projectId)}/behavior-space`, kind: "page" },
    ],
  };
}

function buildBlockerEvidence(projectId: string, blocker: EnvironmentDiagnosticBlocker, graph: EnvironmentDiagnosticGraph): EvidenceDrawerData {
  const relatedNodes = graph.nodes.filter((node) => blocker.nodeIds.includes(node.nodeId)).map((node) => `${node.label} · ${statusLabel(node.status)}`);
  const relatedGates = graph.gates.filter((gate) => blocker.gateIds.includes(gate.gateId)).map((gate) => `${gate.label} · ${statusLabel(gate.status)}`);
  return {
    id: blocker.blockerId,
    typeLabel: "Blocker Evidence",
    title: blocker.title,
    summary: blocker.summary,
    reason: blocker.reason,
    remediationAction: blocker.remediationAction,
    tone: statusTone(blocker.status),
    statusLabel: statusLabel(blocker.status),
    fields: [
      { label: "关联节点", value: String(relatedNodes.length) },
      { label: "关联门禁", value: String(relatedGates.length) },
    ],
    sections: [
      { title: "受影响节点", items: relatedNodes.length ? relatedNodes : ["未映射到具体节点。"] },
      { title: "受影响门禁", items: relatedGates.length ? relatedGates : ["未映射到具体 gate。"] },
    ],
    links: [
      { label: "环境诊断页", href: `/projects/${encodeURIComponent(projectId)}/environment`, kind: "page" },
      { label: "执行页", href: `/projects/${encodeURIComponent(projectId)}/execution`, kind: "page" },
    ],
  };
}

export function EnvironmentDiagnosisWorkspace({ graph }: { graph: EnvironmentDiagnosticGraph }) {
  const [selected, setSelected] = useState<EvidenceDrawerData | null>(null);

  const nodeById = useMemo(() => new Map(graph.nodes.map((node) => [node.nodeId, node])), [graph.nodes]);
  const blockerById = useMemo(() => new Map(graph.blockers.map((blocker) => [blocker.blockerId, blocker])), [graph.blockers]);

  const handleSelectNode = (nodeId: string) => {
    const node = nodeById.get(nodeId);
    if (!node) return;
    setSelected(buildNodeEvidence(graph.projectId, node, graph));
  };

  const handleSelectBlocker = (blockerId: string) => {
    const blocker = blockerById.get(blockerId);
    if (!blocker) return;
    setSelected(buildBlockerEvidence(graph.projectId, blocker, graph));
  };

  return (
    <>
      <section id="environment-quick-actions" className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.44)] p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-xs text-[var(--muted)]">首屏下钻</div>
            <div className="mt-2 text-lg font-semibold text-[var(--fg)]">优先打开阻断项和关键环境节点</div>
            <div className="mt-2 text-sm text-[var(--muted)]">把 Evidence Drawer 入口前置到拓扑前面，避免先滚动到很深的位置再找按钮。</div>
          </div>
        </div>
        <div className="mt-4 grid gap-4 xl:grid-cols-2">
          <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(8,13,21,0.58)] p-4">
            <div className="text-xs text-[var(--muted)]">优先阻断项</div>
            <div className="mt-3 flex flex-wrap gap-2">
              {graph.blockers.length ? (
                graph.blockers.slice(0, 4).map((blocker) => (
                  <button
                    key={blocker.blockerId}
                    type="button"
                    onClick={() => handleSelectBlocker(blocker.blockerId)}
                    className="inline-flex min-h-[40px] items-center justify-center rounded-[var(--radius-sm)] border border-[rgba(255,92,122,0.24)] bg-[rgba(255,92,122,0.08)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(255,92,122,0.42)]"
                  >
                    打开 {blocker.title}
                  </button>
                ))
              ) : (
                <div className="text-sm text-[var(--muted)]">当前没有阻断项。</div>
              )}
            </div>
          </div>
          <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(8,13,21,0.58)] p-4">
            <div className="text-xs text-[var(--muted)]">关键环境节点</div>
            <div className="mt-3 flex flex-wrap gap-2">
              {graph.nodes.length ? (
                graph.nodes.slice(0, 4).map((node) => (
                  <button
                    key={node.nodeId}
                    type="button"
                    onClick={() => handleSelectNode(node.nodeId)}
                    className="inline-flex min-h-[40px] items-center justify-center rounded-[var(--radius-sm)] border border-[rgba(122,167,255,0.24)] bg-[rgba(122,167,255,0.08)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(122,167,255,0.42)]"
                  >
                    查看 {node.label}
                  </button>
                ))
              ) : (
                <div className="text-sm text-[var(--muted)]">当前没有环境节点。</div>
              )}
            </div>
          </div>
        </div>
      </section>
      <div id="environment-topology">
        <EnvironmentTopology2D5 graph={graph} onSelectNode={handleSelectNode} />
      </div>
      <div id="environment-detail">
        <EnvironmentDiagnosticPanel graph={graph} mode="detail" onSelectNode={handleSelectNode} onSelectBlocker={handleSelectBlocker} />
      </div>
      <EvidenceDrawer open={Boolean(selected)} data={selected} onClose={() => setSelected(null)} />
    </>
  );
}
