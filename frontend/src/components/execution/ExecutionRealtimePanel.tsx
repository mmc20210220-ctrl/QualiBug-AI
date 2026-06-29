"use client";

import Link from "next/link";
import { useMemo, useState, type CSSProperties } from "react";
import { Background, Controls, Handle, MarkerType, MiniMap, Position, ReactFlow } from "@xyflow/react";
import type { Edge, Node, NodeProps } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { EvidenceDrawer, type EvidenceDrawerData } from "@/components/evidence/EvidenceDrawer";
import { LongActionCard, type LongActionProgress, type LongActionTerminalState } from "@/components/runtime/LongActionCard";
import { RealtimeConnectionBadge } from "@/components/runtime/RealtimeConnectionBadge";
import { isTerminalRuntimeStatus, runtimeEventToEvidenceDrawerData } from "@/features/execution-runtime/adapter";
import type { RuntimeEvent, RuntimeEventStatus, RuntimeStageEdge, RuntimeStageNode } from "@/features/execution-runtime/model";
import { useRuntimeExecutionTheater } from "@/features/execution-runtime/useRuntimeExecutionTheater";
import { maskId } from "@/lib/redact";

type FocusTarget = { type: "event"; id: string } | { type: "node"; id: string } | { type: "edge"; id: string } | null;

interface TheaterNodeData {
  [key: string]: unknown;
  stage: RuntimeStageNode;
  active: boolean;
}

interface TheaterEdgeData {
  [key: string]: unknown;
  stageEdge: RuntimeStageEdge;
  active: boolean;
}

type TheaterGraphNode = Node<TheaterNodeData>;
type TheaterGraphEdge = Edge<TheaterEdgeData>;

const statusPalette: Record<RuntimeEventStatus, { border: string; glow: string; text: string; chip: string }> = {
  pending: {
    border: "rgba(122,167,255,0.24)",
    glow: "rgba(122,167,255,0.08)",
    text: "#b9d0ff",
    chip: "border-[rgba(122,167,255,0.24)] bg-[rgba(122,167,255,0.10)] text-[rgba(230,239,255,0.92)]",
  },
  running: {
    border: "rgba(122,167,255,0.42)",
    glow: "rgba(122,167,255,0.14)",
    text: "#d6e5ff",
    chip: "border-[rgba(122,167,255,0.28)] bg-[rgba(122,167,255,0.12)] text-[rgba(230,239,255,0.96)]",
  },
  completed: {
    border: "rgba(89,243,194,0.38)",
    glow: "rgba(89,243,194,0.14)",
    text: "#bbffe7",
    chip: "border-[rgba(89,243,194,0.28)] bg-[rgba(89,243,194,0.12)] text-[rgba(220,255,245,0.96)]",
  },
  success: {
    border: "rgba(89,243,194,0.42)",
    glow: "rgba(89,243,194,0.16)",
    text: "#d8ffef",
    chip: "border-[rgba(89,243,194,0.28)] bg-[rgba(89,243,194,0.12)] text-[rgba(220,255,245,0.96)]",
  },
  warning: {
    border: "rgba(245,191,80,0.42)",
    glow: "rgba(245,191,80,0.14)",
    text: "#ffe0a5",
    chip: "border-[rgba(245,191,80,0.26)] bg-[rgba(245,191,80,0.12)] text-[rgba(255,242,216,0.96)]",
  },
  failed: {
    border: "rgba(255,92,122,0.46)",
    glow: "rgba(255,92,122,0.16)",
    text: "#ffbccb",
    chip: "border-[rgba(255,92,122,0.28)] bg-[rgba(255,92,122,0.12)] text-[rgba(255,228,234,0.96)]",
  },
  blocked: {
    border: "rgba(255,92,122,0.52)",
    glow: "rgba(255,92,122,0.18)",
    text: "#ffd1db",
    chip: "border-[rgba(255,92,122,0.30)] bg-[rgba(255,92,122,0.14)] text-[rgba(255,228,234,0.98)]",
  },
};

function statusLabel(status: RuntimeEventStatus): string {
  if (status === "running") return "执行中";
  if (status === "completed") return "已结束";
  if (status === "success") return "成功";
  if (status === "warning") return "告警";
  if (status === "failed") return "失败";
  if (status === "blocked") return "阻断";
  return "待执行";
}

function terminalOutcome(status: string): LongActionTerminalState["outcome"] {
  return /(fail|error|block|阻断|失败)/i.test(status) ? "failed" : "success";
}

function formatTimestamp(value: string | undefined): string {
  if (!value) return "时间待补充";
  return value.replace("T", " ").replace("Z", "");
}

function metricValue(value: number | null, suffix = ""): string {
  if (value === null) return "—";
  return `${value}${suffix}`;
}

function eventItemTone(event: RuntimeEvent): string {
  if (event.status === "failed" || event.status === "blocked" || event.tone === "critical") {
    return "border-[rgba(255,92,122,0.24)] bg-[rgba(255,92,122,0.10)]";
  }
  if (event.status === "warning") return "border-[rgba(245,191,80,0.22)] bg-[rgba(245,191,80,0.10)]";
  if (event.status === "success" || event.status === "completed") return "border-[rgba(89,243,194,0.22)] bg-[rgba(89,243,194,0.10)]";
  return "border-[var(--border)] bg-[rgba(16,24,38,0.38)]";
}

function ExecutionStageNode({ data, selected }: NodeProps<TheaterGraphNode>) {
  const palette = statusPalette[data.stage.status];
  const style: CSSProperties = {
    borderColor: palette.border,
    boxShadow: data.active || selected ? `0 0 0 1px ${palette.border}, 0 24px 54px rgba(0, 0, 0, 0.38)` : `0 18px 42px rgba(0, 0, 0, 0.24)`,
    background: `linear-gradient(180deg, ${palette.glow} 0%, rgba(14,22,34,0.96) 74%)`,
  };

  return (
    <div className="w-[220px] rounded-[18px] border px-4 py-3 backdrop-blur" style={style}>
      <Handle type="target" position={Position.Left} className="!h-3 !w-3 !border-0 !bg-[var(--accent-2)]" />
      <div className="text-[11px] uppercase tracking-[0.2em]" style={{ color: palette.text }}>
        {data.stage.label}
      </div>
      <div className="mt-2 text-sm font-semibold text-[var(--fg)]">{data.stage.headline}</div>
      <div className="mt-2 text-xs leading-5 text-[var(--muted)]">{data.stage.detail}</div>
      <div className="mt-3 flex flex-wrap gap-2">
        <span className={`rounded-full border px-2 py-1 text-[11px] ${palette.chip}`}>{statusLabel(data.stage.status)}</span>
        {data.stage.badges.slice(0, 2).map((badge) => (
          <span
            key={`${data.stage.nodeId}:${badge}`}
            className="rounded-full border border-[var(--border)] bg-[rgba(255,255,255,0.04)] px-2 py-1 text-[11px] text-[var(--muted)]"
          >
            {badge}
          </span>
        ))}
      </div>
      <Handle type="source" position={Position.Right} className="!h-3 !w-3 !border-0 !bg-[var(--accent)]" />
    </div>
  );
}

const nodeTypes = { executionStage: ExecutionStageNode };

function buildGraph(theater: ReturnType<typeof useRuntimeExecutionTheater>["theater"], focus: FocusTarget): {
  nodes: TheaterGraphNode[];
  edges: TheaterGraphEdge[];
} {
  const activeEvent = focus?.type === "event" ? theater.events.find((event) => event.eventId === focus.id) : null;
  const activeNodeId = focus?.type === "node" ? focus.id : activeEvent?.nodeId ?? null;
  const activeEdgeId = focus?.type === "edge" ? focus.id : activeEvent?.edgeId ?? null;

  const nodes = theater.nodes.map<TheaterGraphNode>((stage) => ({
    id: stage.nodeId,
    type: "executionStage",
    position: stage.position,
    data: {
      stage,
      active: activeNodeId === stage.nodeId || (activeEvent?.nodeId ?? "") === stage.nodeId,
    },
    draggable: false,
    selectable: true,
  }));

  const edges = theater.edges.map<TheaterGraphEdge>((edge) => {
    const palette = statusPalette[edge.status];
    const active = activeEdgeId === edge.edgeId || (activeEvent?.edgeId ?? "") === edge.edgeId;
    return {
      id: edge.edgeId,
      source: edge.sourceNodeId,
      target: edge.targetNodeId,
      animated: edge.status === "running" || active,
      data: { stageEdge: edge, active },
      label: edge.label,
      labelStyle: { fill: "#a4afc0", fontSize: 11 },
      style: {
        stroke: palette.border,
        strokeWidth: active ? 3 : edge.emphasis ? 2.8 : 2.1,
        strokeDasharray: edge.status === "warning" || edge.status === "blocked" ? "8 6" : undefined,
      },
      markerEnd: {
        type: MarkerType.ArrowClosed,
        width: 18,
        height: 18,
        color: palette.border,
      },
    };
  });

  return { nodes, edges };
}

function ExecutionInspector({
  theater,
  focus,
  onSelectEvent,
}: {
  theater: ReturnType<typeof useRuntimeExecutionTheater>["theater"];
  focus: FocusTarget;
  onSelectEvent: (event: RuntimeEvent) => void;
}) {
  const eventById = useMemo(() => new Map(theater.events.map((event) => [event.eventId, event])), [theater.events]);
  const nodeById = useMemo(() => new Map(theater.nodes.map((node) => [node.nodeId, node])), [theater.nodes]);
  const edgeById = useMemo(() => new Map(theater.edges.map((edge) => [edge.edgeId, edge])), [theater.edges]);

  if (!focus) {
    return (
      <div className="grid gap-4">
        <div>
          <div className="text-xs uppercase tracking-[0.22em] text-[var(--muted)]">Theater</div>
          <h2 className="mt-2 text-lg font-semibold text-[var(--fg)]">执行剧场</h2>
          <p className="mt-2 text-sm leading-6 text-[var(--muted)]">统一展示 lifecycle、probe、请求响应、快照与 finding 落点，支持从关键事件直接下钻到证据。</p>
        </div>
        <div className="grid gap-2">
          {theater.events.slice(0, 4).map((event) => (
            <button
              key={event.eventId}
              type="button"
              onClick={() => onSelectEvent(event)}
              className={`rounded-[var(--radius-sm)] border px-3 py-3 text-left text-sm text-[var(--fg)] hover:border-[rgba(255,255,255,0.18)] ${eventItemTone(event)}`}
            >
              <div>{event.title}</div>
              <div className="mt-1 text-xs text-[var(--muted)]">{formatTimestamp(event.timestamp)}</div>
            </button>
          ))}
        </div>
      </div>
    );
  }

  if (focus.type === "event") {
    const event = eventById.get(focus.id);
    if (!event) return null;

    return (
      <div className="grid gap-4">
        <div>
          <div className="text-xs uppercase tracking-[0.22em] text-[var(--muted)]">Runtime Event</div>
          <h2 className="mt-2 text-lg font-semibold text-[var(--fg)]">{event.title}</h2>
          <div className="mt-2 flex flex-wrap gap-2 text-xs">
            <span className={`rounded-full border px-3 py-1 ${statusPalette[event.status].chip}`}>{statusLabel(event.status)}</span>
            {event.tags.slice(0, 3).map((tag) => (
              <span key={`${event.eventId}:${tag}`} className="rounded-full border border-[var(--border)] px-3 py-1 text-[var(--muted)]">
                {tag}
              </span>
            ))}
          </div>
          <p className="mt-3 text-sm leading-6 text-[var(--muted)]">{event.message}</p>
        </div>

        <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.34)] p-4">
          <div className="text-xs text-[var(--muted)]">事件位置</div>
          <div className="mt-2 text-sm text-[var(--fg)]">
            节点 {event.nodeId ?? "—"} · 边 {event.edgeId ?? "—"}
          </div>
          <div className="mt-1 text-xs text-[var(--muted)]">{formatTimestamp(event.timestamp)}</div>
        </div>

        {event.nextAction ? (
          <div className="rounded-[var(--radius-sm)] border border-[rgba(122,167,255,0.24)] bg-[rgba(122,167,255,0.08)] p-4">
            <div className="text-xs text-[var(--muted)]">建议动作</div>
            <div className="mt-2 text-sm leading-6 text-[var(--fg)]">{event.nextAction}</div>
          </div>
        ) : null}

        <button
          type="button"
          onClick={() => onSelectEvent(event)}
          className="inline-flex min-h-[40px] items-center justify-center rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(255,255,255,0.05)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(255,255,255,0.18)]"
        >
          打开证据下钻
        </button>
      </div>
    );
  }

  if (focus.type === "edge") {
    const edge = edgeById.get(focus.id);
    if (!edge) return null;
    const edgeEvents = edge.eventIds.map((id) => eventById.get(id)).filter((event): event is RuntimeEvent => Boolean(event));

    return (
      <div className="grid gap-4">
        <div>
          <div className="text-xs uppercase tracking-[0.22em] text-[var(--muted)]">Transition</div>
          <h2 className="mt-2 text-lg font-semibold text-[var(--fg)]">{edge.label}</h2>
          <div className="mt-2 text-sm text-[var(--muted)]">
            {edge.sourceNodeId} → {edge.targetNodeId}
          </div>
        </div>
        <div className="grid gap-2">
          {edgeEvents.length ? (
            edgeEvents.map((event) => (
              <button
                key={event.eventId}
                type="button"
                onClick={() => onSelectEvent(event)}
                className={`rounded-[var(--radius-sm)] border px-3 py-3 text-left text-sm text-[var(--fg)] hover:border-[rgba(255,255,255,0.18)] ${eventItemTone(event)}`}
              >
                <div>{event.title}</div>
                <div className="mt-1 text-xs text-[var(--muted)]">{event.message}</div>
              </button>
            ))
          ) : (
            <div className="text-sm text-[var(--muted)]">当前转场暂无直接挂载事件。</div>
          )}
        </div>
      </div>
    );
  }

  const node = nodeById.get(focus.id);
  if (!node) return null;
  const stageEvents = node.eventIds.map((id) => eventById.get(id)).filter((event): event is RuntimeEvent => Boolean(event));

  return (
    <div className="grid gap-4">
      <div>
        <div className="text-xs uppercase tracking-[0.22em] text-[var(--muted)]">Stage Node</div>
        <h2 className="mt-2 text-lg font-semibold text-[var(--fg)]">{node.label}</h2>
        <div className="mt-2 text-sm text-[var(--muted)]">{node.detail}</div>
      </div>
      <div className="flex flex-wrap gap-2">
        <span className={`rounded-full border px-3 py-1 text-xs ${statusPalette[node.status].chip}`}>{statusLabel(node.status)}</span>
        {node.badges.map((badge) => (
          <span key={`${node.nodeId}:${badge}`} className="rounded-full border border-[var(--border)] px-3 py-1 text-xs text-[var(--muted)]">
            {badge}
          </span>
        ))}
      </div>
      <div className="grid gap-2">
        {stageEvents.length ? (
          stageEvents.map((event) => (
            <button
              key={event.eventId}
              type="button"
              onClick={() => onSelectEvent(event)}
              className={`rounded-[var(--radius-sm)] border px-3 py-3 text-left text-sm text-[var(--fg)] hover:border-[rgba(255,255,255,0.18)] ${eventItemTone(event)}`}
            >
              <div>{event.title}</div>
              <div className="mt-1 text-xs text-[var(--muted)]">{formatTimestamp(event.timestamp)}</div>
            </button>
          ))
        ) : (
          <div className="text-sm text-[var(--muted)]">该节点当前暂无已映射事件。</div>
        )}
      </div>
    </div>
  );
}

export function ExecutionRealtimePanel(input: {
  projectId: string;
  initialSnapshotEnvelope?: unknown;
  initialRunEnvelope?: unknown | null;
}) {
  const { theater, connection, startRun } = useRuntimeExecutionTheater({
    projectId: input.projectId,
    initialSnapshotEnvelope: input.initialSnapshotEnvelope,
    initialRunEnvelope: input.initialRunEnvelope,
  });
  const [focus, setFocus] = useState<FocusTarget>(theater.events[0] ? { type: "event", id: theater.events[0].eventId } : null);
  const [drawerData, setDrawerData] = useState<EvidenceDrawerData | null>(null);

  const graph = useMemo(() => buildGraph(theater, focus), [focus, theater]);
  const runProgress: LongActionProgress | undefined = useMemo(() => {
    if (!theater.runId) return undefined;
    if (theater.metrics.progress === null) return { value: null, label: "等待运行进度…" };
    return { value: Math.max(0, Math.min(1, theater.metrics.progress)), label: "实时执行中" };
  }, [theater.metrics.progress, theater.runId]);

  const runTerminal: LongActionTerminalState | null = useMemo(() => {
    if (!theater.runStatus || !isTerminalRuntimeStatus(theater.runStatus)) return null;
    return {
      outcome: terminalOutcome(theater.runStatus),
      detail: `运行已结束：${theater.runStatus}`,
    };
  }, [theater.runStatus]);

  const openEventDrawer = (event: RuntimeEvent) => {
    setFocus({ type: "event", id: event.eventId });
    setDrawerData(runtimeEventToEvidenceDrawerData(event));
  };

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(16,24,38,0.40)] p-4">
        <div>
          <div className="text-xs text-[var(--muted)]">Execution Theater</div>
          <div className="mt-1 text-sm text-[var(--fg)]">统一 RuntimeEvent 驱动画布、证据下钻与底部事件流</div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <RealtimeConnectionBadge state={connection} />
          <span className={`rounded-full border px-3 py-1 text-xs ${statusPalette[theater.nodes[0]?.status ?? "pending"].chip}`}>{theater.runStatus || "idle"}</span>
          <span className="rounded-full border border-[var(--border)] bg-[rgba(255,255,255,0.04)] px-3 py-1 text-xs text-[var(--muted)]">{theater.source}</span>
        </div>
      </div>

      <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)] p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="text-xs text-[var(--muted)]">首屏下钻</div>
            <div className="mt-1 text-sm text-[var(--muted)]">把阻断风险和最新证据入口前置，避免被后续面板或 sibling 卡片遮挡。</div>
          </div>
          <div className="flex flex-wrap gap-2">
            {theater.events[0] ? (
              <button
                type="button"
                onClick={() => openEventDrawer(theater.events[0])}
                className="inline-flex min-h-[40px] items-center justify-center rounded-[var(--radius-sm)] border border-[rgba(122,167,255,0.24)] bg-[rgba(122,167,255,0.08)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(122,167,255,0.42)]"
              >
                打开最新证据下钻
              </button>
            ) : null}
            <Link
              href={`/projects/${encodeURIComponent(input.projectId)}/risks?launch_blocking=true`}
              className="inline-flex min-h-[40px] items-center justify-center rounded-[var(--radius-sm)] border border-[rgba(255,92,122,0.24)] bg-[rgba(255,92,122,0.10)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(255,92,122,0.42)]"
            >
              打开阻断风险
            </Link>
            <Link
              href={`/projects/${encodeURIComponent(input.projectId)}/risks`}
              className="inline-flex min-h-[40px] items-center justify-center rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.40)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(255,255,255,0.18)]"
            >
              查看全部风险证据
            </Link>
          </div>
        </div>
      </div>

      <LongActionCard
        actionKey={`project.${input.projectId}.start_test_run`}
        title="启动测试运行"
        description="通过统一 adapter 管理启动、SSE/轮询状态同步和运行终态归档。"
        primaryLabel="启动测试"
        danger={{
          title: "确认启动测试运行？",
          detail: "该操作会创建新的 runtime，并将后续事件映射到执行剧场中。",
          confirmLabel: "确认启动",
        }}
        trackTerminal
        progress={runProgress}
        terminal={runTerminal}
        run={startRun}
      />

      <div className="grid gap-3 md:grid-cols-4">
        <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)] p-4">
          <div className="text-xs text-[var(--muted)]">runId</div>
          <div className="mt-2 text-sm font-semibold text-[var(--fg)]">{theater.runId ? maskId(theater.runId, 6, 4) : "—"}</div>
        </div>
        <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)] p-4">
          <div className="text-xs text-[var(--muted)]">进度</div>
          <div className="mt-2 text-sm font-semibold text-[var(--fg)]">
            {theater.metrics.progress === null ? "—" : `${Math.round(theater.metrics.progress * 100)}%`}
          </div>
        </div>
        <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)] p-4">
          <div className="text-xs text-[var(--muted)]">Probe</div>
          <div className="mt-2 text-sm font-semibold text-[var(--fg)]">
            {metricValue(theater.metrics.probeCompleted)}/{metricValue(theater.metrics.probeTotal)}
          </div>
          <div className="mt-1 text-xs text-[var(--muted)]">失败 {metricValue(theater.metrics.probeFailed)}</div>
        </div>
        <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)] p-4">
          <div className="text-xs text-[var(--muted)]">Finding</div>
          <div className="mt-2 text-sm font-semibold text-[var(--fg)]">{metricValue(theater.metrics.riskFound)}</div>
          <div className="mt-1 text-xs text-[var(--muted)]">{formatTimestamp(theater.updatedAt)}</div>
        </div>
      </div>

      <div className="relative isolate grid gap-4 xl:items-start xl:grid-cols-[minmax(0,1fr)_360px]">
        <div className="relative z-0 min-h-[560px] overflow-hidden rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(8,12,20,0.88)] xl:sticky xl:top-24">
          <ReactFlow<TheaterGraphNode, TheaterGraphEdge>
            nodes={graph.nodes}
            edges={graph.edges}
            nodeTypes={nodeTypes}
            className="[&_.react-flow__controls]:!z-[1] [&_.react-flow__minimap]:!z-[1] [&_.react-flow__panel]:!z-[1]"
            fitView
            fitViewOptions={{ padding: 0.16 }}
            minZoom={0.35}
            maxZoom={1.6}
            onNodeClick={(_, node) => setFocus({ type: "node", id: node.id })}
            onEdgeClick={(_, edge) => setFocus({ type: "edge", id: edge.id })}
            proOptions={{ hideAttribution: true }}
            defaultEdgeOptions={{ animated: false }}
          >
            <Background color="rgba(122,167,255,0.12)" gap={24} size={1.2} />
            <MiniMap
              pannable
              zoomable
              maskColor="rgba(7,10,18,0.72)"
              bgColor="rgba(11,15,20,0.96)"
              nodeColor={(node) => {
                const stage = node.data?.stage as RuntimeStageNode | undefined;
                return statusPalette[stage?.status ?? "pending"].border;
              }}
            />
            <Controls position="bottom-left" />
          </ReactFlow>
        </div>

        <aside className="relative z-30 min-w-0 overflow-hidden rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(16,24,38,0.78)] p-5 shadow-[var(--shadow-1)] backdrop-blur xl:sticky xl:top-24 xl:max-h-[calc(100vh-7rem)] xl:self-start">
          <div className="text-xs text-[var(--muted)]">下钻面板</div>
          <div className="mt-4 xl:max-h-[calc(100vh-11rem)] xl:overflow-y-auto xl:pr-1">
            <ExecutionInspector theater={theater} focus={focus} onSelectEvent={openEventDrawer} />
          </div>
        </aside>
      </div>

      {theater.summaryCard ? (
        <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.44)] p-5">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="text-xs text-[var(--muted)]">Summary Card</div>
              <div className="mt-2 text-lg font-semibold text-[var(--fg)]">{theater.summaryCard.title}</div>
              <div className="mt-2 text-sm text-[var(--muted)]">{theater.summaryCard.summary}</div>
            </div>
            <span
              className={`rounded-full border px-3 py-1 text-xs ${
                theater.summaryCard.outcome === "failed"
                  ? statusPalette.failed.chip
                  : theater.summaryCard.outcome === "warning"
                    ? statusPalette.warning.chip
                    : statusPalette.success.chip
              }`}
            >
              {theater.summaryCard.outcome}
            </span>
          </div>

          <div className="mt-4 grid gap-3 md:grid-cols-3">
            {theater.summaryCard.metrics.map((metric) => (
              <div key={metric.metricId} className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.34)] p-4">
                <div className="text-xs text-[var(--muted)]">{metric.label}</div>
                <div className="mt-2 text-sm font-semibold text-[var(--fg)]">{metric.value}</div>
              </div>
            ))}
          </div>

          <div className="mt-4 grid gap-4 md:grid-cols-2">
            <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.34)] p-4">
              <div className="text-xs text-[var(--muted)]">关键结论</div>
              <ul className="mt-3 grid gap-2 text-sm text-[var(--muted)]">
                {theater.summaryCard.highlights.map((highlight, index) => (
                  <li key={`highlight:${index}:${highlight}`}>{highlight}</li>
                ))}
              </ul>
            </div>
            <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.34)] p-4">
              <div className="text-xs text-[var(--muted)]">下一步动作</div>
              <ul className="mt-3 grid gap-2 text-sm text-[var(--muted)]">
                {theater.summaryCard.nextActions.map((action, index) => (
                  <li key={`next-action:${index}:${action}`}>{action}</li>
                ))}
              </ul>
              <div className="relative z-10 mt-3 flex flex-wrap gap-2">
                <Link
                  href={`/projects/${encodeURIComponent(input.projectId)}/risks?launch_blocking=true`}
                  className="inline-flex min-h-[40px] items-center justify-center rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(255,92,122,0.10)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(255,255,255,0.18)]"
                >
                  打开阻断风险
                </Link>
                <Link
                  href={`/projects/${encodeURIComponent(input.projectId)}/roi`}
                  className="inline-flex min-h-[40px] items-center justify-center rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.40)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(255,255,255,0.18)]"
                >
                  查看 ROI
                </Link>
              </div>
            </div>
          </div>
        </div>
      ) : null}

      <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.44)] p-5">
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="text-xs text-[var(--muted)]">底部事件流</div>
            <div className="mt-1 text-sm text-[var(--muted)]">failure、blocker、finding 事件会同步高亮画布并支持证据下钻。</div>
          </div>
          <div className="text-xs text-[var(--muted)]">{theater.events.length} events</div>
        </div>
        <div className="mt-4 grid gap-2">
          {theater.events.length ? (
            theater.events.slice(0, 12).map((event) => (
              <button
                key={event.eventId}
                type="button"
                onClick={() => openEventDrawer(event)}
                className={`rounded-[var(--radius-sm)] border px-4 py-3 text-left text-sm text-[var(--fg)] transition hover:border-[rgba(255,255,255,0.18)] ${eventItemTone(event)}`}
              >
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="min-w-0">
                    <div>{event.title}</div>
                    <div className="mt-1 text-xs text-[var(--muted)]">{event.message}</div>
                  </div>
                  <div className="flex flex-wrap items-center gap-2 text-xs">
                    <span className={`rounded-full border px-3 py-1 ${statusPalette[event.status].chip}`}>{statusLabel(event.status)}</span>
                    <span className="text-[var(--muted)]">{formatTimestamp(event.timestamp)}</span>
                  </div>
                </div>
              </button>
            ))
          ) : (
            <div className="text-sm text-[var(--muted)]">暂无事件，等待 runtime 或 demo 数据推送。</div>
          )}
        </div>
      </div>

      <EvidenceDrawer open={Boolean(drawerData)} data={drawerData} onClose={() => setDrawerData(null)} />
    </div>
  );
}

