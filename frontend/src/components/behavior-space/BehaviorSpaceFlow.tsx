"use client";

import { useEffect, useMemo, useState } from "react";
import type { CSSProperties } from "react";
import Link from "next/link";
import ELK from "elkjs/lib/elk.bundled.js";
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Panel,
  Position,
  ReactFlow,
  useEdgesState,
  useNodesState,
} from "@xyflow/react";
import type { Edge, Node, NodeProps } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type {
  BehaviorAuditRef,
  BehaviorCoverageStatus,
  BehaviorEvidenceRef,
  BehaviorFinding,
  BehaviorPath,
  BehaviorReplayRef,
  BehaviorSpaceSceneStatus,
  BehaviorSpaceTone,
  BehaviorSpaceVisualization,
  BehaviorSystemNode,
} from "@/behavior-space/types";

type GraphEntityType = "scene" | "system" | "finding" | "evidence" | "replay";
type Selection = { type: "scene" | "system" | "path" | "finding" | "evidence" | "replay"; id: string };
type LinkAction = { label: string; href: string };

interface BehaviorGraphNodeData {
  [key: string]: unknown;
  entityType: GraphEntityType;
  entityId: string;
  badge: string;
  title: string;
  subtitle: string;
  chips: readonly string[];
  tone: BehaviorSpaceTone;
  action?: LinkAction;
}

type GraphNode = Node<BehaviorGraphNodeData>;
type GraphEdgeData = { entityType?: "path"; pathId?: string };
type GraphEdge = Edge<GraphEdgeData>;

const elk = new ELK();
const DEFAULT_SELECTION: Selection = { type: "scene", id: "scene" };

const toneStyles: Record<BehaviorSpaceTone, { border: string; glow: string; text: string }> = {
  positive: {
    border: "rgba(89,243,194,0.4)",
    glow: "rgba(89,243,194,0.14)",
    text: "#9ff8db",
  },
  warning: {
    border: "rgba(245,191,80,0.42)",
    glow: "rgba(245,191,80,0.12)",
    text: "#ffd98a",
  },
  critical: {
    border: "rgba(255,92,122,0.42)",
    glow: "rgba(255,92,122,0.12)",
    text: "#ff9caf",
  },
  neutral: {
    border: "rgba(122,167,255,0.34)",
    glow: "rgba(122,167,255,0.1)",
    text: "#afc8ff",
  },
};

function isSupportedHref(href: string | undefined): href is string {
  if (!href) return false;
  return /^\/projects\/[^/]+(?:\/risks(?:\/[^/?#]+)?(?:\?.*)?(?:#.*)?|\/reports\/executive(?:#.*)?|\/execution(?:#.*)?|\/roi(?:#.*)?|\/behavior-space(?:#.*)?|(?:#.*)?)$/.test(
    href,
  );
}

function toneFromSceneStatus(status: BehaviorSpaceSceneStatus): BehaviorSpaceTone {
  if (status === "ready") return "positive";
  if (status === "warning") return "warning";
  if (status === "blocked") return "critical";
  return "neutral";
}

function toneFromCoverageStatus(status: BehaviorCoverageStatus): BehaviorSpaceTone {
  if (status === "covered") return "positive";
  if (status === "partial") return "warning";
  if (status === "blocked" || status === "uncovered") return "critical";
  return "neutral";
}

function severityTone(severity: string, launchBlocking: boolean): BehaviorSpaceTone {
  const normalized = severity.toLowerCase();
  if (launchBlocking || normalized.includes("critical") || normalized.includes("high") || normalized.includes("严重")) return "critical";
  if (normalized.includes("medium") || normalized.includes("warn") || normalized.includes("中")) return "warning";
  if (normalized.includes("low") || normalized.includes("minor") || normalized.includes("低")) return "positive";
  return "neutral";
}

function summarizeStatus(status: BehaviorSpaceSceneStatus | BehaviorCoverageStatus): string {
  if (status === "ready" || status === "covered") return "已覆盖";
  if (status === "warning" || status === "partial") return "部分覆盖";
  if (status === "blocked") return "存在阻断";
  if (status === "uncovered") return "未覆盖";
  return "状态待定";
}

function chipForCount(label: string, count: number): string {
  return `${label} ${count}`;
}

function nodeDimensions(type: GraphEntityType): { width: number; height: number } {
  if (type === "scene") return { width: 280, height: 164 };
  if (type === "system") return { width: 220, height: 138 };
  if (type === "finding") return { width: 236, height: 148 };
  return { width: 208, height: 122 };
}

function badgeForEntity(type: GraphEntityType): string {
  if (type === "scene") return "Scene";
  if (type === "system") return "System";
  if (type === "finding") return "Risk";
  if (type === "replay") return "Replay";
  return "Evidence";
}

function formatAuditTimestamp(value: string | undefined): string {
  if (!value) return "时间待补充";
  return value.replace("T", " ").replace("Z", "");
}

function formatEvidenceKind(kind: BehaviorEvidenceRef["kind"]): string {
  if (kind === "risk") return "风险证据";
  if (kind === "environment") return "环境证据";
  if (kind === "run") return "执行证据";
  if (kind === "report") return "报告证据";
  if (kind === "api") return "接口证据";
  return "审计证据";
}

function buildReplayHref(replay: BehaviorReplayRef): string | undefined {
  if (!isSupportedHref(replay.href)) return undefined;
  return replay.href.includes("#") ? replay.href : `${replay.href}#replay`;
}

function ActionButtons({ actions }: { actions: readonly LinkAction[] }) {
  if (!actions.length) return null;

  return (
    <div className="mt-4 flex flex-wrap gap-2">
      {actions.map((action) => (
        <Link
          key={`${action.label}:${action.href}`}
          href={action.href}
          className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.44)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(255,255,255,0.18)]"
        >
          {action.label}
        </Link>
      ))}
    </div>
  );
}

function BehaviorNode({ data, selected }: NodeProps<GraphNode>) {
  const palette = toneStyles[data.tone];
  const style: CSSProperties = {
    borderColor: palette.border,
    boxShadow: selected ? `0 0 0 1px ${palette.border}, 0 20px 50px rgba(0, 0, 0, 0.35)` : `0 20px 50px rgba(0, 0, 0, 0.28)`,
    background: `linear-gradient(180deg, ${palette.glow} 0%, rgba(14,22,34,0.96) 72%)`,
  };

  return (
    <div className="rounded-[18px] border px-4 py-3 text-left backdrop-blur" style={style}>
      <Handle type="target" position={Position.Left} className="!h-3 !w-3 !border-0 !bg-[var(--accent-2)]" />
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-[11px] uppercase tracking-[0.22em]" style={{ color: palette.text }}>
            {data.badge}
          </div>
          <div className="mt-2 text-sm font-semibold text-[var(--fg)]">{data.title}</div>
        </div>
      </div>
      <div className="mt-2 text-xs leading-5 text-[var(--muted)]">{data.subtitle}</div>
      <div className="mt-3 flex flex-wrap gap-2">
        {data.chips.map((chip) => (
          <span
            key={`${data.entityId}:${chip}`}
            className="rounded-full border border-[var(--border)] bg-[rgba(255,255,255,0.04)] px-2 py-1 text-[11px] text-[var(--muted)]"
          >
            {chip}
          </span>
        ))}
      </div>
      {data.action ? (
        <div className="mt-3">
          <Link
            href={data.action.href}
            className="nodrag nopan inline-flex rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(255,255,255,0.05)] px-2.5 py-1.5 text-xs text-[var(--fg)] hover:border-[rgba(255,255,255,0.18)]"
          >
            {data.action.label}
          </Link>
        </div>
      ) : null}
      <Handle type="source" position={Position.Right} className="!h-3 !w-3 !border-0 !bg-[var(--accent)]" />
    </div>
  );
}

const nodeTypes = { behavior: BehaviorNode };

async function layoutNodes(nodes: GraphNode[], edges: GraphEdge[]): Promise<GraphNode[]> {
  const graph = {
    id: "behavior-space",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "RIGHT",
      "elk.layered.spacing.nodeNodeBetweenLayers": "110",
      "elk.spacing.nodeNode": "70",
      "elk.edgeRouting": "ORTHOGONAL",
      "elk.padding": "[top=40,left=40,bottom=40,right=40]",
    },
    children: nodes.map((node) => {
      const size = nodeDimensions(node.data.entityType);
      return {
        id: node.id,
        width: size.width,
        height: size.height,
      };
    }),
    edges: edges.map((edge) => ({
      id: edge.id,
      sources: [edge.source],
      targets: [edge.target],
    })),
  };

  const layout = await elk.layout(graph);
  const childMap = new Map((layout.children ?? []).map((child) => [child.id, child]));

  return nodes.map((node) => {
    const child = childMap.get(node.id);
    return {
      ...node,
      position: {
        x: child?.x ?? node.position.x,
        y: child?.y ?? node.position.y,
      },
    };
  });
}

function buildGraph(visualization: BehaviorSpaceVisualization): { nodes: GraphNode[]; edges: GraphEdge[] } {
  const nodes: GraphNode[] = [];
  const edges: GraphEdge[] = [];

  nodes.push({
    id: DEFAULT_SELECTION.id,
    type: "behavior",
    position: { x: 0, y: 0 },
    data: {
      entityType: "scene",
      entityId: DEFAULT_SELECTION.id,
      badge: badgeForEntity("scene"),
      title: visualization.scene.title,
      subtitle: visualization.scene.summary,
      chips: [
        `上线 ${visualization.valueSummary.launchRecommendation.value}`,
        `覆盖 ${visualization.valueSummary.coverage.value}`,
        `风险 ${visualization.findings.length}`,
      ],
      tone: toneFromSceneStatus(visualization.scene.status),
      action: isSupportedHref(visualization.valueSummary.auditReadiness.href)
        ? { label: "查看报告", href: visualization.valueSummary.auditReadiness.href }
        : undefined,
    },
    style: nodeDimensions("scene"),
  });

  for (const systemNode of visualization.systemNodes) {
    const chips = [
      systemNode.domain ? `域 ${systemNode.domain}` : `类型 ${systemNode.kind}`,
      summarizeStatus(systemNode.status),
      chipForCount("风险", systemNode.riskCount),
    ];
    nodes.push({
      id: systemNode.nodeId,
      type: "behavior",
      position: { x: 0, y: 0 },
      data: {
        entityType: "system",
        entityId: systemNode.nodeId,
        badge: badgeForEntity("system"),
        title: systemNode.label,
        subtitle: systemNode.domain ? `${systemNode.kind} · ${systemNode.domain}` : systemNode.kind,
        chips,
        tone: toneFromSceneStatus(systemNode.status),
        action: systemNode.riskCount > 0
          ? { label: "查看风险", href: `/projects/${encodeURIComponent(visualization.scene.projectId)}/risks` }
          : undefined,
      },
      style: nodeDimensions("system"),
    });
  }

  for (const path of visualization.behaviorPaths) {
    const flowEntryTarget = path.sourceNodeId ?? path.nodeIds[0];
    if (flowEntryTarget) {
      edges.push({
        id: `path-entry:${path.pathId}`,
        source: DEFAULT_SELECTION.id,
        target: flowEntryTarget,
        label: `${path.label} · ${summarizeStatus(path.coverageStatus)}`,
        style: {
          stroke: toneStyles[toneFromCoverageStatus(path.coverageStatus)].border,
          strokeDasharray: "8 6",
          strokeWidth: 1.8,
        },
        labelStyle: { fill: "#a4afc0", fontSize: 11 },
        markerEnd: {
          type: MarkerType.ArrowClosed,
          width: 18,
          height: 18,
          color: toneStyles[toneFromCoverageStatus(path.coverageStatus)].border,
        },
        data: { entityType: "path", pathId: path.pathId },
      });
    }

    for (let index = 0; index < path.nodeIds.length - 1; index += 1) {
      const source = path.nodeIds[index];
      const target = path.nodeIds[index + 1];
      edges.push({
        id: `path:${path.pathId}:${source}:${target}:${index}`,
        source,
        target,
        style: {
          stroke: toneStyles[toneFromCoverageStatus(path.coverageStatus)].border,
          strokeWidth: 2.2,
        },
        markerEnd: {
          type: MarkerType.ArrowClosed,
          width: 18,
          height: 18,
          color: toneStyles[toneFromCoverageStatus(path.coverageStatus)].border,
        },
        data: { entityType: "path", pathId: path.pathId },
      });
    }
  }

  for (const finding of visualization.findings) {
    const anchorPath = visualization.behaviorPaths.find((path) => finding.pathIds.includes(path.pathId));
    const anchorNodeId = anchorPath?.targetNodeId ?? anchorPath?.sourceNodeId ?? DEFAULT_SELECTION.id;
    const replay = finding.replayRefId ? visualization.replayRefs.find((item) => item.replayRefId === finding.replayRefId) : undefined;
    const detailHref = `/projects/${encodeURIComponent(visualization.scene.projectId)}/risks/${encodeURIComponent(finding.findingId)}`;

    nodes.push({
      id: `finding:${finding.findingId}`,
      type: "behavior",
      position: { x: 0, y: 0 },
      data: {
        entityType: "finding",
        entityId: finding.findingId,
        badge: badgeForEntity("finding"),
        title: finding.title,
        subtitle: finding.summary,
        chips: [
          `严重度 ${finding.severity}`,
          finding.launchBlocking ? "上线阻断" : "可后续修复",
          replay ? "支持回放" : "仅证据摘要",
        ],
        tone: severityTone(finding.severity, finding.launchBlocking),
        action: { label: "风险详情", href: detailHref },
      },
      style: nodeDimensions("finding"),
    });

    edges.push({
      id: `finding-link:${finding.findingId}`,
      source: anchorNodeId,
      target: `finding:${finding.findingId}`,
      style: {
        stroke: toneStyles[severityTone(finding.severity, finding.launchBlocking)].border,
        strokeDasharray: "6 5",
        strokeWidth: 1.8,
      },
      markerEnd: {
        type: MarkerType.ArrowClosed,
        width: 18,
        height: 18,
        color: toneStyles[severityTone(finding.severity, finding.launchBlocking)].border,
      },
    });

    if (replay) {
      const replayHref = buildReplayHref(replay);
      nodes.push({
        id: `replay:${replay.replayRefId}`,
        type: "behavior",
        position: { x: 0, y: 0 },
        data: {
          entityType: "replay",
          entityId: replay.replayRefId,
          badge: badgeForEntity("replay"),
          title: replay.label,
          subtitle: replay.steps[0] ?? "复现步骤已绑定到风险详情",
          chips: [chipForCount("步骤", replay.steps.length), chipForCount("证据", replay.evidenceRefIds.length)],
          tone: replay.steps.length > 0 ? "positive" : "neutral",
          action: replayHref ? { label: "打开回放入口", href: replayHref } : undefined,
        },
        style: nodeDimensions("replay"),
      });

      edges.push({
        id: `replay-link:${replay.replayRefId}`,
        source: `finding:${finding.findingId}`,
        target: `replay:${replay.replayRefId}`,
        style: {
          stroke: toneStyles.positive.border,
          strokeWidth: 1.8,
        },
        markerEnd: {
          type: MarkerType.ArrowClosed,
          width: 18,
          height: 18,
          color: toneStyles.positive.border,
        },
      });
    }
  }

  const reportEvidence = visualization.evidenceRefs.find((item) => item.kind === "report");
  const runEvidence = visualization.evidenceRefs.find((item) => item.kind === "run");
  const environmentEvidence = visualization.evidenceRefs.filter((item) => item.kind === "environment");

  const evidenceNodes: Array<{ id: string; title: string; subtitle: string; chips: string[]; tone: BehaviorSpaceTone; href?: string }> = [];
  if (reportEvidence) {
    evidenceNodes.push({
      id: `evidence:${reportEvidence.evidenceRefId}`,
      title: reportEvidence.label,
      subtitle: reportEvidence.summary,
      chips: ["报告入口", "可下钻结论"],
      tone: "positive",
      href: isSupportedHref(reportEvidence.href) ? reportEvidence.href : undefined,
    });
  }
  if (runEvidence) {
    evidenceNodes.push({
      id: `evidence:${runEvidence.evidenceRefId}`,
      title: runEvidence.label,
      subtitle: runEvidence.summary,
      chips: ["执行链路", "探针状态"],
      tone: "warning",
      href: isSupportedHref(runEvidence.href) ? runEvidence.href : undefined,
    });
  }
  if (environmentEvidence.length) {
    evidenceNodes.push({
      id: "evidence:environment:group",
      title: `环境阻断 ${environmentEvidence.length} 项`,
      subtitle: environmentEvidence[0]?.summary ?? "环境阻断已聚合到场景中",
      chips: [chipForCount("阻断", environmentEvidence.length), summarizeStatus(visualization.scene.environmentStatus)],
      tone: visualization.scene.environmentStatus === "blocked" ? "critical" : "warning",
    });
  }

  for (const evidenceNode of evidenceNodes) {
    nodes.push({
      id: evidenceNode.id,
      type: "behavior",
      position: { x: 0, y: 0 },
      data: {
        entityType: "evidence",
        entityId: evidenceNode.id.replace(/^evidence:/, ""),
        badge: badgeForEntity("evidence"),
        title: evidenceNode.title,
        subtitle: evidenceNode.subtitle,
        chips: evidenceNode.chips,
        tone: evidenceNode.tone,
        action: evidenceNode.href ? { label: "打开入口", href: evidenceNode.href } : undefined,
      },
      style: nodeDimensions("evidence"),
    });

    edges.push({
      id: `scene-evidence:${evidenceNode.id}`,
      source: DEFAULT_SELECTION.id,
      target: evidenceNode.id,
      style: {
        stroke: toneStyles[evidenceNode.tone].border,
        strokeWidth: 1.6,
      },
      markerEnd: {
        type: MarkerType.ArrowClosed,
        width: 18,
        height: 18,
        color: toneStyles[evidenceNode.tone].border,
      },
    });
  }

  return { nodes, edges };
}

function renderEvidenceList(
  evidenceRefs: readonly BehaviorEvidenceRef[],
  getHref: (ref: BehaviorEvidenceRef) => string | undefined,
): React.ReactNode {
  if (!evidenceRefs.length) return <div className="text-sm text-[var(--muted)]">暂无已挂载证据入口。</div>;

  return (
    <div className="grid gap-2">
      {evidenceRefs.slice(0, 6).map((ref) => {
        const href = getHref(ref);
        return (
          <div key={ref.evidenceRefId} className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.32)] p-3">
            <div className="text-sm text-[var(--fg)]">{ref.label}</div>
            <div className="mt-1 text-xs text-[var(--muted)]">{ref.summary}</div>
            {href ? (
              <div className="mt-2">
                <Link href={href} className="text-xs text-[var(--fg)] hover:underline">
                  打开证据入口
                </Link>
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

function renderAuditList(auditRefs: readonly BehaviorAuditRef[]) {
  if (!auditRefs.length) return <div className="text-sm text-[var(--muted)]">暂无审计轨迹。</div>;

  return (
    <div className="grid gap-2">
      {auditRefs.slice(0, 6).map((audit) => (
        <div key={audit.auditRefId} className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.32)] p-3">
          <div className="flex items-center justify-between gap-3">
            <div className="text-sm text-[var(--fg)]">{audit.label}</div>
            <div className="text-xs text-[var(--muted)]">{audit.kind}</div>
          </div>
          <div className="mt-1 text-xs text-[var(--muted)]">{audit.summary}</div>
          <div className="mt-2 text-xs text-[var(--muted)]">
            {audit.actor} · {formatAuditTimestamp(audit.timestamp)}
          </div>
          {isSupportedHref(audit.href) ? (
            <div className="mt-2">
              <Link href={audit.href} className="text-xs text-[var(--fg)] hover:underline">
                打开关联页面
              </Link>
            </div>
          ) : null}
        </div>
      ))}
    </div>
  );
}

function renderReplayList(
  replayRefs: readonly BehaviorReplayRef[],
  onSelect: (next: Selection) => void,
  emptyText = "暂无复现包。",
) {
  if (!replayRefs.length) return <div className="text-sm text-[var(--muted)]">{emptyText}</div>;

  return (
    <div className="grid gap-2">
      {replayRefs.slice(0, 6).map((replay) => (
        <button
          key={replay.replayRefId}
          type="button"
          onClick={() => onSelect({ type: "replay", id: replay.replayRefId })}
          className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.32)] px-3 py-3 text-left text-sm text-[var(--fg)] hover:border-[rgba(255,255,255,0.18)]"
        >
          <div>{replay.label}</div>
          <div className="mt-1 text-xs text-[var(--muted)]">
            步骤 {replay.steps.length}
            {replay.updatedAt ? ` · 更新时间 ${formatAuditTimestamp(replay.updatedAt)}` : ""}
          </div>
          {replay.summary ? <div className="mt-2 text-xs text-[var(--muted)]">{replay.summary}</div> : null}
        </button>
      ))}
    </div>
  );
}

function BehaviorSpaceInspector({
  selection,
  visualization,
  onSelect,
}: {
  selection: Selection;
  visualization: BehaviorSpaceVisualization;
  onSelect: (next: Selection) => void;
}) {
  const systemById = useMemo(
    () => new Map(visualization.systemNodes.map((item) => [item.nodeId, item])),
    [visualization.systemNodes],
  );
  const pathById = useMemo(
    () => new Map(visualization.behaviorPaths.map((item) => [item.pathId, item])),
    [visualization.behaviorPaths],
  );
  const findingById = useMemo(
    () => new Map(visualization.findings.map((item) => [item.findingId, item])),
    [visualization.findings],
  );
  const evidenceById = useMemo(
    () => new Map(visualization.evidenceRefs.map((item) => [item.evidenceRefId, item])),
    [visualization.evidenceRefs],
  );
  const replayById = useMemo(
    () => new Map(visualization.replayRefs.map((item) => [item.replayRefId, item])),
    [visualization.replayRefs],
  );

  const getEvidenceHref = (ref: BehaviorEvidenceRef) => (isSupportedHref(ref.href) ? ref.href : undefined);

  if (selection.type === "scene") {
    const actions = [
      visualization.valueSummary.riskCost.href,
      visualization.valueSummary.auditReadiness.href,
      visualization.valueSummary.efficiencyGain.href,
    ]
      .filter(isSupportedHref)
      .map((href) => ({
        href,
        label:
          href.includes("/risks") ? "查看风险证据" : href.includes("/reports") ? "打开领导层报告" : href.includes("/roi") ? "查看 ROI" : "打开入口",
      }));

    return (
      <div className="grid gap-4">
        <div>
          <div className="text-xs uppercase tracking-[0.22em] text-[var(--muted)]">Scene</div>
          <h2 className="mt-2 text-lg font-semibold text-[var(--fg)]">{visualization.scene.title}</h2>
          <p className="mt-2 text-sm leading-6 text-[var(--muted)]">{visualization.scene.summary}</p>
          <ActionButtons actions={actions} />
        </div>

        <div className="grid gap-2 md:grid-cols-2">
          {visualization.valueSummary.fields.map((field) => (
            <div key={field.fieldId} className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.32)] p-3">
              <div className="text-xs text-[var(--muted)]">{field.label}</div>
              <div className="mt-2 text-sm font-semibold text-[var(--fg)]">{field.value}</div>
              <div className="mt-1 text-xs text-[var(--muted)]">{field.supportingText ?? "—"}</div>
            </div>
          ))}
        </div>

        <div>
          <div className="text-xs text-[var(--muted)]">回放入口</div>
          <div className="mt-2">{renderReplayList(visualization.replayRefs, onSelect)}</div>
        </div>

        <div>
          <div className="text-xs text-[var(--muted)]">审计轨迹</div>
          <div className="mt-2">{renderAuditList(visualization.auditRefs)}</div>
        </div>
      </div>
    );
  }

  if (selection.type === "system") {
    const systemNode = systemById.get(selection.id);
    if (!systemNode) return null;
    const relatedPaths = visualization.behaviorPaths.filter((path) => systemNode.flowIds.includes(path.pathId));
    const relatedEvidence = visualization.evidenceRefs.filter((ref) => systemNode.evidenceRefIds.includes(ref.evidenceRefId));

    return (
      <div className="grid gap-4">
        <div>
          <div className="text-xs uppercase tracking-[0.22em] text-[var(--muted)]">System Node</div>
          <h2 className="mt-2 text-lg font-semibold text-[var(--fg)]">{systemNode.label}</h2>
          <div className="mt-2 text-sm text-[var(--muted)]">
            {systemNode.kind}
            {systemNode.domain ? ` · ${systemNode.domain}` : ""}
            {" · "}
            {summarizeStatus(systemNode.status)}
          </div>
          <ActionButtons
            actions={
              systemNode.riskCount > 0
                ? [{ label: "查看风险证据", href: `/projects/${encodeURIComponent(visualization.scene.projectId)}/risks` }]
                : []
            }
          />
        </div>

        <div>
          <div className="text-xs text-[var(--muted)]">关联业务路径</div>
          <div className="mt-2 grid gap-2">
            {relatedPaths.length ? (
              relatedPaths.map((path) => (
                <button
                  key={path.pathId}
                  type="button"
                  onClick={() => onSelect({ type: "path", id: path.pathId })}
                  className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.32)] px-3 py-3 text-left text-sm text-[var(--fg)] hover:border-[rgba(255,255,255,0.18)]"
                >
                  <div>{path.label}</div>
                  <div className="mt-1 text-xs text-[var(--muted)]">
                    {summarizeStatus(path.coverageStatus)} · 风险 {path.riskCount} · 阻断 {path.blockerCount}
                  </div>
                </button>
              ))
            ) : (
              <div className="text-sm text-[var(--muted)]">暂无路径挂载。</div>
            )}
          </div>
        </div>

        <div>
          <div className="text-xs text-[var(--muted)]">证据入口</div>
          <div className="mt-2">{renderEvidenceList(relatedEvidence, getEvidenceHref)}</div>
        </div>
      </div>
    );
  }

  if (selection.type === "path") {
    const path = pathById.get(selection.id);
    if (!path) return null;
    const relatedFindings = path.findingIds.map((id) => findingById.get(id)).filter((item): item is BehaviorFinding => Boolean(item));
    const relatedNodes = path.nodeIds.map((id) => systemById.get(id)).filter((item): item is BehaviorSystemNode => Boolean(item));
    const relatedEvidence = path.evidenceRefIds.map((id) => evidenceById.get(id)).filter((item): item is BehaviorEvidenceRef => Boolean(item));
    const relatedReplays = path.replayRefIds.map((id) => replayById.get(id)).filter((item): item is BehaviorReplayRef => Boolean(item));
    const actions: LinkAction[] = [{ label: "查看关联风险", href: `/projects/${encodeURIComponent(visualization.scene.projectId)}/risks` }];
    const reportHref = visualization.evidenceRefs.find((ref) => ref.kind === "report" && isSupportedHref(ref.href))?.href;
    if (reportHref) actions.push({ label: "打开领导层报告", href: reportHref });

    return (
      <div className="grid gap-4">
        <div>
          <div className="text-xs uppercase tracking-[0.22em] text-[var(--muted)]">Behavior Path</div>
          <h2 className="mt-2 text-lg font-semibold text-[var(--fg)]">{path.label}</h2>
          <div className="mt-2 text-sm text-[var(--muted)]">
            {summarizeStatus(path.coverageStatus)} · 风险 {path.riskCount} · 阻断 {path.blockerCount}
          </div>
          <ActionButtons actions={actions} />
        </div>

        <div>
          <div className="text-xs text-[var(--muted)]">系统建模路径</div>
          <div className="mt-2 flex flex-wrap gap-2">
            {relatedNodes.length ? (
              relatedNodes.map((node) => (
                <button
                  key={node.nodeId}
                  type="button"
                  onClick={() => onSelect({ type: "system", id: node.nodeId })}
                  className="rounded-full border border-[var(--border)] bg-[rgba(16,24,38,0.32)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(255,255,255,0.18)]"
                >
                  {node.label}
                </button>
              ))
            ) : (
              <div className="text-sm text-[var(--muted)]">暂无建模节点。</div>
            )}
          </div>
        </div>

        <div>
          <div className="text-xs text-[var(--muted)]">风险暴露点</div>
          <div className="mt-2 grid gap-2">
            {relatedFindings.length ? (
              relatedFindings.map((finding) => (
                <button
                  key={finding.findingId}
                  type="button"
                  onClick={() => onSelect({ type: "finding", id: finding.findingId })}
                  className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.32)] px-3 py-3 text-left text-sm text-[var(--fg)] hover:border-[rgba(255,255,255,0.18)]"
                >
                  <div>{finding.title}</div>
                  <div className="mt-1 text-xs text-[var(--muted)]">
                    {finding.severity} · {finding.launchBlocking ? "上线阻断" : "可后续处理"}
                  </div>
                </button>
              ))
            ) : (
              <div className="text-sm text-[var(--muted)]">当前路径未挂载风险。</div>
            )}
          </div>
        </div>

        <div>
          <div className="text-xs text-[var(--muted)]">证据摘要</div>
          <div className="mt-2">{renderEvidenceList(relatedEvidence, getEvidenceHref)}</div>
        </div>

        <div>
          <div className="text-xs text-[var(--muted)]">复现包</div>
          <div className="mt-2">{renderReplayList(relatedReplays, onSelect, "当前路径暂无复现包。")}</div>
        </div>
      </div>
    );
  }

  if (selection.type === "finding") {
    const finding = findingById.get(selection.id);
    if (!finding) return null;
    const evidenceRefs = finding.evidenceRefIds.map((id) => evidenceById.get(id)).filter((item): item is BehaviorEvidenceRef => Boolean(item));
    const replay = finding.replayRefId ? replayById.get(finding.replayRefId) : undefined;
    const actions: LinkAction[] = [
      {
        label: "风险详情",
        href: `/projects/${encodeURIComponent(visualization.scene.projectId)}/risks/${encodeURIComponent(finding.findingId)}`,
      },
    ];
    const replayHref = replay ? buildReplayHref(replay) : undefined;
    if (replayHref) actions.push({ label: "打开复现步骤", href: replayHref });
    const reportHref = visualization.evidenceRefs.find((ref) => ref.kind === "report" && isSupportedHref(ref.href))?.href;
    if (reportHref) actions.push({ label: "查看报告结论", href: reportHref });

    return (
      <div className="grid gap-4">
        <div>
          <div className="text-xs uppercase tracking-[0.22em] text-[var(--muted)]">Risk</div>
          <h2 className="mt-2 text-lg font-semibold text-[var(--fg)]">{finding.title}</h2>
          <div className="mt-2 text-sm text-[var(--muted)]">
            {finding.severity} · {finding.launchBlocking ? "上线阻断" : "非阻断"}
          </div>
          <p className="mt-2 text-sm leading-6 text-[var(--muted)]">{finding.summary}</p>
          {finding.businessImpact ? <p className="mt-2 text-sm text-[var(--fg)]">业务影响：{finding.businessImpact}</p> : null}
          <ActionButtons actions={actions} />
        </div>

        <div>
          <div className="text-xs text-[var(--muted)]">证据入口</div>
          <div className="mt-2">{renderEvidenceList(evidenceRefs, getEvidenceHref)}</div>
        </div>

        {replay ? (
          <div>
            <div className="text-xs text-[var(--muted)]">回放摘要</div>
            <div className="mt-2 rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.32)] p-3">
              <div className="text-sm text-[var(--fg)]">{replay.label}</div>
              {replay.summary ? <div className="mt-2 text-xs text-[var(--muted)]">{replay.summary}</div> : null}
              <ul className="mt-2 grid gap-2 text-sm text-[var(--muted)]">
                {replay.steps.slice(0, 6).map((step, index) => (
                  <li key={`${replay.replayRefId}:${index + 1}`}>{index + 1}. {step}</li>
                ))}
              </ul>
            </div>
          </div>
        ) : null}
      </div>
    );
  }

  if (selection.type === "replay") {
    const replay = replayById.get(selection.id);
    if (!replay) return null;
    const href = buildReplayHref(replay);
    const evidenceRefs = replay.evidenceRefIds.map((id) => evidenceById.get(id)).filter((item): item is BehaviorEvidenceRef => Boolean(item));

    return (
      <div className="grid gap-4">
        <div>
          <div className="text-xs uppercase tracking-[0.22em] text-[var(--muted)]">Replay</div>
          <h2 className="mt-2 text-lg font-semibold text-[var(--fg)]">{replay.label}</h2>
          <div className="mt-2 text-sm text-[var(--muted)]">riskId {replay.riskId} · 步骤 {replay.steps.length}</div>
          <ActionButtons actions={href ? [{ label: "打开风险详情中的回放入口", href }] : []} />
        </div>

        <div>
          <div className="text-xs text-[var(--muted)]">复现步骤</div>
        {replay.summary ? <div className="mt-2 text-sm text-[var(--muted)]">{replay.summary}</div> : null}
          <ul className="mt-2 grid gap-2 text-sm text-[var(--muted)]">
            {replay.steps.length ? replay.steps.map((step, index) => <li key={`${replay.replayRefId}:${index + 1}`}>{index + 1}. {step}</li>) : <li>暂无步骤。</li>}
          </ul>
        </div>

        <div>
          <div className="text-xs text-[var(--muted)]">关联证据</div>
          <div className="mt-2">{renderEvidenceList(evidenceRefs, getEvidenceHref)}</div>
        </div>
      </div>
    );
  }

  const evidence = evidenceById.get(selection.id);
  if (!evidence) return null;
  const actions: LinkAction[] = [];
  if (isSupportedHref(evidence.href)) actions.push({ label: "打开已有入口", href: evidence.href });
  if (evidence.findingId) {
    actions.push({
      label: "查看关联风险",
      href: `/projects/${encodeURIComponent(visualization.scene.projectId)}/risks/${encodeURIComponent(evidence.findingId)}`,
    });
  }

  return (
    <div className="grid gap-4">
      <div>
        <div className="text-xs uppercase tracking-[0.22em] text-[var(--muted)]">Evidence</div>
        <h2 className="mt-2 text-lg font-semibold text-[var(--fg)]">{evidence.label}</h2>
        <div className="mt-2 text-sm text-[var(--muted)]">{formatEvidenceKind(evidence.kind)}</div>
        <p className="mt-2 text-sm leading-6 text-[var(--muted)]">{evidence.summary}</p>
        <ActionButtons actions={actions} />
      </div>
    </div>
  );
}

export function BehaviorSpaceFlow({ visualization }: { visualization: BehaviorSpaceVisualization }) {
  const graph = useMemo(() => buildGraph(visualization), [visualization]);
  const [nodes, setNodes, onNodesChange] = useNodesState<GraphNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<GraphEdge>([]);
  const [selection, setSelection] = useState<Selection>(DEFAULT_SELECTION);

  useEffect(() => {
    let cancelled = false;

    void layoutNodes(graph.nodes, graph.edges).then((layoutedNodes) => {
      if (cancelled) return;
      setNodes(layoutedNodes);
      setEdges(graph.edges);
    });

    return () => {
      cancelled = true;
    };
  }, [graph, setEdges, setNodes]);

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
      <div className="min-h-[760px] overflow-hidden rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(9,14,22,0.86)]">
        <ReactFlow<GraphNode, GraphEdge>
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          fitView
          fitViewOptions={{ padding: 0.16 }}
          minZoom={0.35}
          maxZoom={1.5}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onNodeClick={(_, node) => setSelection({ type: node.data.entityType, id: node.data.entityId })}
          onEdgeClick={(_, edge) => {
            const pathId = typeof edge.data?.pathId === "string" ? edge.data.pathId : null;
            if (pathId) setSelection({ type: "path", id: pathId });
          }}
          defaultEdgeOptions={{ animated: false }}
          proOptions={{ hideAttribution: true }}
        >
          <Background color="rgba(122,167,255,0.12)" gap={24} size={1.2} />
          <MiniMap
            pannable
            zoomable
            nodeColor={(node) => {
              const tone = typeof node.data?.tone === "string" ? node.data.tone : "neutral";
              return toneStyles[tone as BehaviorSpaceTone].border;
            }}
            maskColor="rgba(7,10,18,0.72)"
            bgColor="rgba(11,15,20,0.96)"
          />
          <Controls position="bottom-left" />
          <Panel position="top-left">
            <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(11,15,20,0.88)] px-4 py-3 text-xs text-[var(--muted)] shadow-[var(--shadow-1)]">
              <div className="font-semibold text-[var(--fg)]">2D 主视图</div>
              <div className="mt-1">System = 系统建模，Risk = 风险暴露，Replay = 回放入口，Evidence = 报告/执行/环境证据。</div>
            </div>
          </Panel>
        </ReactFlow>
      </div>

      <aside className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(16,24,38,0.55)] p-5 shadow-[var(--shadow-1)] backdrop-blur">
        <div className="text-xs text-[var(--muted)]">下钻面板</div>
        <div className="mt-4">
          <BehaviorSpaceInspector selection={selection} visualization={visualization} onSelect={setSelection} />
        </div>
      </aside>
    </div>
  );
}
