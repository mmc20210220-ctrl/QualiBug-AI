"use client";

import { useMemo, useState } from "react";
import type { EnvironmentDiagnosticGraph, EnvironmentDiagnosticNode, GateStatus } from "@/features/environment-diagnostics";

type StatusFilter = "all" | GateStatus;

type NodeLayout = {
  left: number;
  top: number;
  depth: number;
};

const statusOrder: readonly GateStatus[] = ["blocked", "warning", "checking", "passed", "unknown"];

const statusMeta: Record<GateStatus, { label: string; ring: string; pill: string; glow: string; edge: string }> = {
  passed: {
    label: "已通过",
    ring: "border-[rgba(89,243,194,0.42)] bg-[linear-gradient(180deg,rgba(89,243,194,0.22),rgba(15,27,28,0.9))] text-[var(--fg)]",
    pill: "border-[rgba(89,243,194,0.35)] bg-[rgba(89,243,194,0.12)] text-[rgba(213,255,244,0.96)]",
    glow: "0 0 0 1px rgba(89,243,194,0.08), 0 18px 38px rgba(30,116,93,0.35)",
    edge: "rgba(89,243,194,0.55)",
  },
  warning: {
    label: "警告",
    ring: "border-[rgba(255,196,87,0.42)] bg-[linear-gradient(180deg,rgba(255,196,87,0.2),rgba(34,22,10,0.92))] text-[var(--fg)]",
    pill: "border-[rgba(255,196,87,0.35)] bg-[rgba(255,196,87,0.12)] text-[rgba(255,239,199,0.96)]",
    glow: "0 0 0 1px rgba(255,196,87,0.08), 0 18px 38px rgba(120,78,12,0.33)",
    edge: "rgba(255,196,87,0.55)",
  },
  blocked: {
    label: "阻断",
    ring: "border-[rgba(255,92,122,0.42)] bg-[linear-gradient(180deg,rgba(255,92,122,0.2),rgba(34,12,18,0.92))] text-[var(--fg)]",
    pill: "border-[rgba(255,92,122,0.35)] bg-[rgba(255,92,122,0.12)] text-[rgba(255,220,226,0.96)]",
    glow: "0 0 0 1px rgba(255,92,122,0.08), 0 18px 38px rgba(116,23,44,0.36)",
    edge: "rgba(255,92,122,0.58)",
  },
  checking: {
    label: "检查中",
    ring: "border-[rgba(122,167,255,0.42)] bg-[linear-gradient(180deg,rgba(122,167,255,0.2),rgba(10,18,34,0.92))] text-[var(--fg)]",
    pill: "border-[rgba(122,167,255,0.35)] bg-[rgba(122,167,255,0.12)] text-[rgba(222,233,255,0.96)]",
    glow: "0 0 0 1px rgba(122,167,255,0.08), 0 18px 38px rgba(28,58,118,0.35)",
    edge: "rgba(122,167,255,0.58)",
  },
  unknown: {
    label: "未知",
    ring: "border-[var(--border)] bg-[linear-gradient(180deg,rgba(255,255,255,0.07),rgba(11,15,20,0.92))] text-[var(--fg)]",
    pill: "border-[var(--border)] bg-[rgba(255,255,255,0.05)] text-[var(--muted)]",
    glow: "0 0 0 1px rgba(255,255,255,0.04), 0 18px 38px rgba(0,0,0,0.26)",
    edge: "rgba(164,175,192,0.38)",
  },
};

const laneMeta: Record<string, { label: string; description: string; tone: string }> = {
  access: { label: "Access Plane", description: "识别入口与目标地址", tone: "rgba(122,167,255,0.12)" },
  network: { label: "Network Plane", description: "验证 DNS、TLS 与可达性", tone: "rgba(89,243,194,0.08)" },
  identity: { label: "Identity Plane", description: "确认认证、会话与角色矩阵", tone: "rgba(255,196,87,0.08)" },
  probe: { label: "Probe Plane", description: "执行 API smoke 与数据验证", tone: "rgba(255,92,122,0.08)" },
  safety: { label: "Safety Plane", description: "控制脱敏、回滚与安全边界", tone: "rgba(122,167,255,0.08)" },
  runtime: { label: "Runtime Plane", description: "汇总最终放行结论", tone: "rgba(89,243,194,0.12)" },
};

const nodeLayouts: Record<string, NodeLayout> = {
  entrypoint: { left: 4, top: 14, depth: 0 },
  url: { left: 14, top: 34, depth: 1 },
  dns: { left: 28, top: 13, depth: 0 },
  http: { left: 39, top: 32, depth: 1 },
  auth: { left: 53, top: 14, depth: 0 },
  "account-matrix": { left: 64, top: 34, depth: 1 },
  "api-smoke": { left: 77, top: 14, depth: 0 },
  "test-data": { left: 84, top: 36, depth: 1 },
  snapshot: { left: 92, top: 16, depth: 0 },
  cleanup: { left: 92, top: 56, depth: 2 },
  runtime: { left: 68, top: 62, depth: 2 },
};

const filters: readonly { key: StatusFilter; label: string }[] = [
  { key: "all", label: "全部" },
  { key: "blocked", label: "阻断" },
  { key: "warning", label: "警告" },
  { key: "passed", label: "已通过" },
  { key: "checking", label: "检查中" },
  { key: "unknown", label: "未知" },
];

function stageSummary(nodes: readonly EnvironmentDiagnosticNode[], stageId: string): string {
  const stageNodes = nodes.filter((node) => node.stageId === stageId);
  if (!stageNodes.length) return "当前没有节点";
  const blocked = stageNodes.filter((node) => node.status === "blocked").length;
  const warning = stageNodes.filter((node) => node.status === "warning").length;
  if (blocked > 0) return `${blocked} 个阻断节点`;
  if (warning > 0) return `${warning} 个警告节点`;
  return `${stageNodes.length} 个节点可继续`;
}

function runtimeSummary(graph: EnvironmentDiagnosticGraph) {
  const runtimeGate = graph.gates.find((gate) => gate.gateId === "runtime_start_allowed") ?? graph.gates[0];
  const runtimeNode = graph.nodes.find((node) => node.nodeId === "runtime");
  const allowedCount = graph.gates.filter((gate) => gate.allowed).length;
  const blockerCount = graph.blockers.filter((blocker) => blocker.status === "blocked").length;
  return {
    runtimeGate,
    runtimeNode,
    allowedCount,
    blockerCount,
  };
}

function buildPath(source: NodeLayout, target: NodeLayout) {
  const startX = source.left + 5.2;
  const startY = source.top + 7.5 + source.depth * 1.2;
  const endX = target.left + 5.2;
  const endY = target.top + 7.5 + target.depth * 1.2;
  const curve = Math.max(8, Math.abs(endX - startX) * 0.32);
  return `M ${startX} ${startY} C ${startX + curve} ${startY}, ${endX - curve} ${endY}, ${endX} ${endY}`;
}

export function EnvironmentTopology2D5({
  graph,
  onSelectNode,
}: {
  graph: EnvironmentDiagnosticGraph;
  onSelectNode?: (nodeId: string) => void;
}) {
  const [filter, setFilter] = useState<StatusFilter>("all");

  const counts = useMemo(() => {
    const next = { passed: 0, warning: 0, blocked: 0, checking: 0, unknown: 0 };
    for (const node of graph.nodes) next[node.status] += 1;
    return next;
  }, [graph.nodes]);

  const filteredNodes = useMemo(
    () => graph.nodes.filter((node) => filter === "all" || node.status === filter),
    [filter, graph.nodes],
  );

  const visibleIds = new Set(filteredNodes.map((node) => node.nodeId));
  const summary = runtimeSummary(graph);

  return (
    <section className="grid gap-4 xl:grid-cols-[minmax(0,1.55fr)_minmax(320px,0.9fr)]">
      <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[linear-gradient(180deg,rgba(17,26,41,0.88),rgba(7,11,18,0.96))] p-5 shadow-[var(--shadow-1)]">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-xs uppercase tracking-[0.22em] text-[var(--muted)]">Environment Topology MVP</div>
            <h2 className="mt-2 text-lg font-semibold tracking-tight text-[var(--fg)]">2.5D 环境拓扑</h2>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-[var(--muted)]">
              拓扑直接复用现有 `EnvironmentDiagnosticGraph` 的 10+ 个节点、边和门禁，让团队先看到 Runtime Allowed 之前到底卡在哪一层。
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            {filters.map((item) => {
              const active = filter === item.key;
              const count = item.key === "all" ? graph.nodes.length : counts[item.key];
              return (
                <button
                  key={item.key}
                  type="button"
                  onClick={() => setFilter(item.key)}
                  className={`rounded-full border px-3 py-2 text-xs transition ${
                    active
                      ? "border-[rgba(122,167,255,0.42)] bg-[rgba(122,167,255,0.14)] text-[var(--fg)]"
                      : "border-[var(--border)] bg-[rgba(255,255,255,0.04)] text-[var(--muted)] hover:border-[rgba(255,255,255,0.18)] hover:text-[var(--fg)]"
                  }`}
                >
                  {item.label} {count}
                </button>
              );
            })}
          </div>
        </div>

        <div className="mt-4 grid gap-3 sm:grid-cols-5">
          {statusOrder.map((status) => (
            <div key={status} className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(255,255,255,0.03)] p-3">
              <div className={`inline-flex rounded-full border px-2 py-1 text-[11px] ${statusMeta[status].pill}`}>{statusMeta[status].label}</div>
              <div className="mt-3 text-2xl font-semibold tracking-tight text-[var(--fg)]">{counts[status]}</div>
              <div className="text-xs text-[var(--muted)]">节点</div>
            </div>
          ))}
        </div>

        <div className="mt-5 overflow-x-auto rounded-[var(--radius-md)] border border-[var(--border)] bg-[radial-gradient(circle_at_top,rgba(122,167,255,0.14),rgba(8,13,21,0.96)_58%)] p-4">
          <div className="min-w-[1120px]">
            <div className="grid gap-3 xl:grid-cols-6">
              {Object.entries(laneMeta).map(([stageId, lane]) => (
                <div
                  key={stageId}
                  className="rounded-[var(--radius-sm)] border border-[var(--border)] px-3 py-3"
                  style={{ background: `linear-gradient(180deg, ${lane.tone}, rgba(255,255,255,0.02))` }}
                >
                  <div className="text-[11px] uppercase tracking-[0.22em] text-[var(--muted)]">{lane.label}</div>
                  <div className="mt-2 text-sm font-medium text-[var(--fg)]">{lane.description}</div>
                  <div className="mt-1 text-xs text-[var(--muted)]">{stageSummary(graph.nodes, stageId)}</div>
                </div>
              ))}
            </div>

            <div className="relative mt-4 h-[640px] rounded-[26px] border border-[var(--border)] bg-[linear-gradient(180deg,rgba(255,255,255,0.05),rgba(6,10,18,0.82))] [perspective:1400px]">
              <div className="pointer-events-none absolute inset-0 rounded-[26px] bg-[linear-gradient(180deg,rgba(122,167,255,0.06),transparent_34%,rgba(89,243,194,0.03)_68%,transparent)]" />
              <div className="pointer-events-none absolute left-[3%] right-[3%] top-[10%] h-[78%] rounded-[24px] border border-[rgba(255,255,255,0.05)] bg-[linear-gradient(180deg,rgba(8,13,21,0.18),rgba(8,13,21,0.68))] shadow-[inset_0_1px_0_rgba(255,255,255,0.06)] [transform:rotateX(58deg)]" />
              <div className="pointer-events-none absolute inset-[8%] rounded-[22px] border border-[rgba(255,255,255,0.05)] bg-[linear-gradient(180deg,rgba(255,255,255,0.02),transparent)] [transform:translateZ(8px)]" />

              <svg viewBox="0 0 100 100" className="pointer-events-none absolute inset-0 h-full w-full overflow-visible">
                {graph.edges.map((edge) => {
                  const source = nodeLayouts[edge.sourceNodeId];
                  const target = nodeLayouts[edge.targetNodeId];
                  if (!source || !target) return null;
                  const muted = filter !== "all" && !(visibleIds.has(edge.sourceNodeId) || visibleIds.has(edge.targetNodeId));
                  const meta = statusMeta[edge.status];
                  return (
                    <g key={edge.edgeId} opacity={muted ? 0.14 : 0.82}>
                      <path
                        d={buildPath(source, target)}
                        fill="none"
                        stroke={meta.edge}
                        strokeWidth="0.45"
                        strokeDasharray={edge.status === "checking" ? "1.4 1.1" : undefined}
                      />
                    </g>
                  );
                })}
              </svg>

              {graph.nodes.map((node) => {
                const layout = nodeLayouts[node.nodeId];
                if (!layout) return null;
                const filteredOut = filter !== "all" && !visibleIds.has(node.nodeId);
                const meta = statusMeta[node.status];
                const z = 18 + layout.depth * 12;
                return (
                  <article
                    key={node.nodeId}
                    className={`absolute z-10 flex w-[122px] flex-col rounded-[18px] border p-3 transition ${meta.ring} ${filteredOut ? "opacity-20 saturate-50" : "opacity-100"}`}
                    style={{
                      left: `${layout.left}%`,
                      top: `${layout.top}%`,
                      transform: `translateZ(${z}px) rotateX(2deg)`,
                      boxShadow: meta.glow,
                    }}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <div className="text-[11px] uppercase tracking-[0.18em] text-[var(--muted)]">{node.stageId}</div>
                        <div className="mt-1 text-sm font-semibold text-[var(--fg)]">{node.label}</div>
                      </div>
                      <span className={`rounded-full border px-2 py-1 text-[10px] ${meta.pill}`}>{meta.label}</span>
                    </div>
                    <div className="mt-3 line-clamp-2 text-sm font-medium text-[var(--fg)]">{node.headline}</div>
                    <div className="mt-2 line-clamp-2 text-xs leading-5 text-[var(--muted)]">{node.detail}</div>
                    {onSelectNode ? (
                      <button
                        type="button"
                        onClick={() => onSelectNode(node.nodeId)}
                        className="mt-auto inline-flex min-h-[28px] items-center justify-center rounded-full border border-[rgba(255,255,255,0.16)] bg-[rgba(8,13,21,0.72)] px-2 py-1 text-[10px] text-[var(--fg)] hover:border-[rgba(255,255,255,0.28)]"
                      >
                        证据
                      </button>
                    ) : null}
                  </article>
                );
              })}

              <div className="absolute bottom-4 left-4 flex flex-wrap gap-2 text-[11px] text-[var(--muted)]">
                {statusOrder.map((status) => (
                  <div key={status} className={`rounded-full border px-2 py-1 ${statusMeta[status].pill}`}>
                    {statusMeta[status].label}
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="grid gap-4">
        <div className="rounded-[var(--radius-md)] border border-[rgba(89,243,194,0.18)] bg-[linear-gradient(180deg,rgba(89,243,194,0.14),rgba(8,13,21,0.9))] p-5 shadow-[var(--shadow-1)]">
          <div className="text-xs uppercase tracking-[0.22em] text-[var(--muted)]">Runtime Allowed 汇总</div>
          <div className="mt-2 flex items-center justify-between gap-3">
            <h3 className="text-lg font-semibold tracking-tight text-[var(--fg)]">{summary.runtimeNode?.headline ?? summary.runtimeGate.label}</h3>
            <span className={`rounded-full border px-3 py-1 text-xs ${statusMeta[summary.runtimeGate.status].pill}`}>{statusMeta[summary.runtimeGate.status].label}</span>
          </div>
          <p className="mt-2 text-sm leading-6 text-[var(--muted)]">{summary.runtimeGate.reason}</p>
          <div className="mt-4 grid gap-3 sm:grid-cols-3 xl:grid-cols-1">
            <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(255,255,255,0.04)] p-4">
              <div className="text-xs text-[var(--muted)]">正式 Runtime</div>
              <div className="mt-2 text-2xl font-semibold text-[var(--fg)]">{summary.runtimeGate.allowed ? "允许" : "未允许"}</div>
              <div className="mt-1 text-xs text-[var(--muted)]">最终门禁结论</div>
            </div>
            <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(255,255,255,0.04)] p-4">
              <div className="text-xs text-[var(--muted)]">可执行门禁</div>
              <div className="mt-2 text-2xl font-semibold text-[var(--fg)]">{summary.allowedCount} / {graph.gates.length}</div>
              <div className="mt-1 text-xs text-[var(--muted)]">包含只读、写入和 P0/P1 验证</div>
            </div>
            <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(255,255,255,0.04)] p-4">
              <div className="text-xs text-[var(--muted)]">阻断项</div>
              <div className="mt-2 text-2xl font-semibold text-[var(--fg)]">{summary.blockerCount} / {graph.blockers.length}</div>
              <div className="mt-1 text-xs text-[var(--muted)]">仍需优先收敛的 blocker</div>
            </div>
          </div>
          <div className="mt-4 rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(8,13,21,0.58)] p-4">
            <div className="text-sm font-medium text-[var(--fg)]">建议动作</div>
            <p className="mt-2 text-sm text-[var(--muted)]">{summary.runtimeGate.remediationAction ?? "继续按当前环境策略推进。"} </p>
          </div>
        </div>

        <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.46)] p-5">
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="text-xs text-[var(--muted)]">筛选结果</div>
              <div className="mt-1 text-sm text-[var(--muted)]">当前筛选下命中的节点清单与下一步动作。</div>
            </div>
            <span className="rounded-full border border-[var(--border)] bg-[rgba(255,255,255,0.04)] px-3 py-1 text-xs text-[var(--muted)]">
              {filteredNodes.length} 个节点
            </span>
          </div>
          <div className="mt-4 grid gap-3">
            {filteredNodes.map((node) => (
              <article key={node.nodeId} className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(8,13,21,0.58)] p-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="text-xs uppercase tracking-[0.18em] text-[var(--muted)]">{node.stageId}</div>
                    <div className="mt-1 text-sm font-semibold text-[var(--fg)]">{node.label}</div>
                  </div>
                  <span className={`rounded-full border px-2 py-1 text-[11px] ${statusMeta[node.status].pill}`}>{statusMeta[node.status].label}</span>
                </div>
                <div className="mt-3 text-sm text-[var(--fg)]">{node.headline}</div>
                <div className="mt-2 text-sm leading-6 text-[var(--muted)]">{node.detail}</div>
                <div className="mt-2 text-xs text-[var(--muted)]">下一步：{node.nextAction}</div>
                {onSelectNode ? (
                  <button
                    type="button"
                    onClick={() => onSelectNode(node.nodeId)}
                    className="mt-3 rounded-[var(--radius-sm)] border border-[rgba(122,167,255,0.24)] bg-[rgba(122,167,255,0.08)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(122,167,255,0.42)]"
                  >
                    查看 Evidence Drawer
                  </button>
                ) : null}
              </article>
            ))}
            {filteredNodes.length === 0 ? (
              <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(8,13,21,0.58)] p-4 text-sm text-[var(--muted)]">
                当前筛选没有命中任何节点，请切换到其他状态查看拓扑。
              </div>
            ) : null}
          </div>
        </div>
      </div>
    </section>
  );
}
