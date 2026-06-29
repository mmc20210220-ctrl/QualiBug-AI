"use client";

import { useEffect, useMemo, useState } from "react";
import type { CSSProperties } from "react";
import Link from "next/link";
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
  BehaviorRoleNode,
  BehaviorSpaceSceneStatus,
  BehaviorSpaceTone,
  BehaviorSpaceVisualization,
  BehaviorSystemNode,
} from "@/behavior-space/types";
import { EvidenceDrawer } from "@/components/evidence/EvidenceDrawer";
import type { EvidenceDrawerData, EvidenceDrawerLink } from "@/components/evidence/EvidenceDrawer";

type GraphLayerId = "role" | "behavior" | "system" | "evidence" | "risk";
type GraphEntityType = "role" | "path" | "system" | "evidence" | "finding";
type Selection =
  | { type: "scene"; id: string }
  | { type: "role"; id: string }
  | { type: "path"; id: string }
  | { type: "system"; id: string }
  | { type: "finding"; id: string }
  | { type: "evidence"; id: string }
  | { type: "replay"; id: string };
type LinkAction = { label: string; href: string };

interface BehaviorGraphNodeData {
  [key: string]: unknown;
  entityType: GraphEntityType;
  entityId: string;
  layerId: GraphLayerId;
  relatedPathIds: readonly string[];
  badge: string;
  title: string;
  subtitle: string;
  chips: readonly string[];
  tone: BehaviorSpaceTone;
  action?: LinkAction;
  riskType?: string;
}

type GraphNode = Node<BehaviorGraphNodeData>;
type GraphEdgeData = { pathId?: string };
type GraphEdge = Edge<GraphEdgeData>;

const DEFAULT_SELECTION: Selection = { type: "scene", id: "scene" };
const DEFAULT_RISK_FILTER = "全部";

const toneStyles: Record<BehaviorSpaceTone, { border: string; glow: string; text: string; solid: string }> = {
  positive: {
    border: "rgba(89,243,194,0.42)",
    glow: "rgba(89,243,194,0.14)",
    text: "#9ff8db",
    solid: "#59f3c2",
  },
  warning: {
    border: "rgba(245,191,80,0.42)",
    glow: "rgba(245,191,80,0.12)",
    text: "#ffd98a",
    solid: "#f5bf50",
  },
  critical: {
    border: "rgba(255,92,122,0.42)",
    glow: "rgba(255,92,122,0.12)",
    text: "#ff9caf",
    solid: "#ff5c7a",
  },
  neutral: {
    border: "rgba(122,167,255,0.34)",
    glow: "rgba(122,167,255,0.1)",
    text: "#afc8ff",
    solid: "#7aa7ff",
  },
};

function isSupportedHref(href: string | undefined): href is string {
  if (!href) return false;
  return /^\/projects\/[^/]+(?:\/risks(?:\/[^/?#]+)?(?:\?.*)?(?:#.*)?|\/reports\/executive(?:#.*)?|\/execution(?:#.*)?|\/roi(?:#.*)?|\/behavior-space(?:#.*)?|\/environment(?:#.*)?|(?:#.*)?)$/.test(
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

function nodeDimensions(layerId: GraphLayerId): { width: number; height: number } {
  if (layerId === "role") return { width: 238, height: 132 };
  if (layerId === "behavior") return { width: 252, height: 148 };
  if (layerId === "system") return { width: 220, height: 134 };
  if (layerId === "evidence") return { width: 224, height: 128 };
  return { width: 236, height: 144 };
}

function badgeForLayer(layerId: GraphLayerId): string {
  if (layerId === "role") return "Role";
  if (layerId === "behavior") return "Behavior";
  if (layerId === "system") return "System";
  if (layerId === "evidence") return "Evidence";
  return "Risk";
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

function formatSystemKind(kind: BehaviorSystemNode["kind"]): string {
  if (kind === "frontend") return "前端入口";
  if (kind === "service") return "业务服务";
  if (kind === "database") return "数据存储";
  if (kind === "queue") return "消息队列";
  if (kind === "external_api") return "外部接口";
  if (kind === "worker") return "异步任务";
  if (kind === "environment") return "环境节点";
  return "系统节点";
}

function buildReplayHref(replay: BehaviorReplayRef): string | undefined {
  if (!isSupportedHref(replay.href)) return undefined;
  return replay.href.includes("#") ? replay.href : `${replay.href}#replay`;
}

function buildFindingDetailHref(projectId: string, findingId: string): string {
  return `/projects/${encodeURIComponent(projectId)}/risks/${encodeURIComponent(findingId)}`;
}

function coverageText(path: BehaviorPath): string {
  return `${path.coveragePercent}%`;
}

function firstPathId(pathIds: readonly string[]): string | null {
  return pathIds[0] ?? null;
}

function sortByLabel<T extends { label: string }>(items: readonly T[]): T[] {
  return [...items].sort((left, right) => left.label.localeCompare(right.label, "zh-CN"));
}

function ActionButtons({ actions }: { actions: readonly LinkAction[] }) {
  if (!actions.length) return null;

  return (
    <div className="mt-4 flex flex-wrap gap-2">
      {actions.map((action) => (
        <Link
          key={`${action.label}:${action.href}`}
          href={action.href}
          className="inline-flex min-h-[40px] items-center justify-center rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.44)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(255,255,255,0.18)]"
        >
          {action.label}
        </Link>
      ))}
    </div>
  );
}

function LayerChip({
  active,
  label,
  value,
  onClick,
}: {
  active: boolean;
  label: string;
  value: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-full border px-3 py-2 text-xs transition ${
        active
          ? "border-[rgba(122,167,255,0.4)] bg-[rgba(122,167,255,0.14)] text-[var(--fg)]"
          : "border-[var(--border)] bg-[rgba(16,24,38,0.35)] text-[var(--muted)] hover:border-[rgba(255,255,255,0.18)]"
      }`}
    >
      {label} · {value}
    </button>
  );
}

function BehaviorNode({ data, selected }: NodeProps<GraphNode>) {
  const palette = toneStyles[data.tone];
  const style: CSSProperties = {
    borderColor: palette.border,
    boxShadow: selected
      ? `0 0 0 1px ${palette.border}, 0 20px 50px rgba(0, 0, 0, 0.35)`
      : `0 18px 42px rgba(0, 0, 0, 0.24)`,
    background: `linear-gradient(180deg, ${palette.glow} 0%, rgba(14,22,34,0.96) 74%)`,
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

function layerX(layerId: GraphLayerId): number {
  if (layerId === "role") return 80;
  if (layerId === "behavior") return 390;
  if (layerId === "system") return 720;
  if (layerId === "evidence") return 1060;
  return 1400;
}

function positionNodes(nodes: GraphNode[]): GraphNode[] {
  const layerOrder: GraphLayerId[] = ["role", "behavior", "system", "evidence", "risk"];
  return layerOrder.flatMap((layerId) => {
    const layerNodes = nodes.filter((node) => node.data.layerId === layerId);
    return layerNodes.map((node, index) => ({
      ...node,
      position: {
        x: layerX(layerId),
        y: 40 + index * (layerId === "evidence" ? 154 : 176),
      },
    }));
  });
}

function buildBaseGraph(visualization: BehaviorSpaceVisualization): { nodes: GraphNode[]; edges: GraphEdge[] } {
  const nodes: GraphNode[] = [];
  const edges: GraphEdge[] = [];

  for (const roleNode of sortByLabel(visualization.roleNodes)) {
    nodes.push({
      id: `role:${roleNode.roleId}`,
      type: "behavior",
      position: { x: 0, y: 0 },
      data: {
        entityType: "role",
        entityId: roleNode.roleId,
        layerId: "role",
        relatedPathIds: roleNode.pathIds,
        badge: badgeForLayer("role"),
        title: roleNode.label,
        subtitle: roleNode.summary,
        chips: [summarizeStatus(roleNode.status), chipForCount("路径", roleNode.pathIds.length), chipForCount("风险", roleNode.riskCount)],
        tone: toneFromSceneStatus(roleNode.status),
      },
      style: nodeDimensions("role"),
    });

    for (const pathId of roleNode.pathIds) {
      edges.push({
        id: `role-path:${roleNode.roleId}:${pathId}`,
        source: `role:${roleNode.roleId}`,
        target: `path:${pathId}`,
        data: { pathId },
      });
    }
  }

  for (const path of visualization.behaviorPaths) {
    nodes.push({
      id: `path:${path.pathId}`,
      type: "behavior",
      position: { x: 0, y: 0 },
      data: {
        entityType: "path",
        entityId: path.pathId,
        layerId: "behavior",
        relatedPathIds: [path.pathId],
        badge: badgeForLayer("behavior"),
        title: path.label,
        subtitle: path.summary ?? `coverage ${coverageText(path)} · ${summarizeStatus(path.coverageStatus)}`,
        chips: [
          `coverage ${coverageText(path)}`,
          chipForCount("风险", path.riskCount),
          path.blockerCount > 0 ? chipForCount("阻断", path.blockerCount) : "已挂接证据",
        ],
        tone: toneFromCoverageStatus(path.coverageStatus),
        action: { label: "高亮该路径", href: `#behavior-space-2d` },
      },
      style: nodeDimensions("behavior"),
    });

    if (path.nodeIds[0]) {
      edges.push({
        id: `path-system:${path.pathId}:${path.nodeIds[0]}`,
        source: `path:${path.pathId}`,
        target: `system:${path.nodeIds[0]}`,
        data: { pathId: path.pathId },
      });
    }

    for (let index = 0; index < path.nodeIds.length - 1; index += 1) {
      const source = path.nodeIds[index];
      const target = path.nodeIds[index + 1];
      edges.push({
        id: `system-system:${path.pathId}:${source}:${target}:${index}`,
        source: `system:${source}`,
        target: `system:${target}`,
        data: { pathId: path.pathId },
      });
    }
  }

  for (const systemNode of sortByLabel(visualization.systemNodes)) {
    nodes.push({
      id: `system:${systemNode.nodeId}`,
      type: "behavior",
      position: { x: 0, y: 0 },
      data: {
        entityType: "system",
        entityId: systemNode.nodeId,
        layerId: "system",
        relatedPathIds: systemNode.flowIds,
        badge: badgeForLayer("system"),
        title: systemNode.label,
        subtitle: systemNode.domain ? `${formatSystemKind(systemNode.kind)} · ${systemNode.domain}` : formatSystemKind(systemNode.kind),
        chips: [summarizeStatus(systemNode.status), chipForCount("路径", systemNode.flowIds.length), chipForCount("风险", systemNode.riskCount)],
        tone: toneFromSceneStatus(systemNode.status),
      },
      style: nodeDimensions("system"),
    });
  }

  for (const evidence of sortByLabel(visualization.evidenceRefs)) {
    const relatedPathIds = evidence.pathId
      ? [evidence.pathId]
      : evidence.findingId
        ? visualization.findings.find((item) => item.findingId === evidence.findingId)?.pathIds ?? []
        : [];
    const tone =
      evidence.kind === "environment"
        ? "warning"
        : evidence.kind === "report"
          ? "positive"
          : evidence.kind === "run"
            ? "neutral"
            : "warning";

    nodes.push({
      id: `evidence:${evidence.evidenceRefId}`,
      type: "behavior",
      position: { x: 0, y: 0 },
      data: {
        entityType: "evidence",
        entityId: evidence.evidenceRefId,
        layerId: "evidence",
        relatedPathIds,
        badge: badgeForLayer("evidence"),
        title: evidence.label,
        subtitle: evidence.summary,
        chips: [formatEvidenceKind(evidence.kind), evidence.findingId ? "已绑定风险" : "路径级证据"],
        tone,
        action: isSupportedHref(evidence.href) ? { label: "打开入口", href: evidence.href } : undefined,
      },
      style: nodeDimensions("evidence"),
    });

    const anchorPathId = relatedPathIds[0];
    if (anchorPathId) {
      edges.push({
        id: `path-evidence:${anchorPathId}:${evidence.evidenceRefId}`,
        source: `path:${anchorPathId}`,
        target: `evidence:${evidence.evidenceRefId}`,
        data: { pathId: anchorPathId },
      });
    }
  }

  for (const finding of visualization.findings) {
    const replay = finding.replayRefId ? visualization.replayRefs.find((item) => item.replayRefId === finding.replayRefId) : undefined;
    const detailHref = `/projects/${encodeURIComponent(visualization.scene.projectId)}/risks/${encodeURIComponent(finding.findingId)}`;
    nodes.push({
      id: `finding:${finding.findingId}`,
      type: "behavior",
      position: { x: 0, y: 0 },
      data: {
        entityType: "finding",
        entityId: finding.findingId,
        layerId: "risk",
        relatedPathIds: finding.pathIds,
        badge: badgeForLayer("risk"),
        title: finding.title,
        subtitle: `${finding.riskType} · ${finding.summary}`,
        chips: [
          `严重度 ${finding.severity}`,
          finding.launchBlocking ? "上线阻断" : "可排期修复",
          replay ? "支持 Evidence Replay" : "仅证据 Drawer",
        ],
        tone: severityTone(finding.severity, finding.launchBlocking),
        action: { label: "风险详情", href: detailHref },
        riskType: finding.riskType,
      },
      style: nodeDimensions("risk"),
    });

    const evidenceLinkTargets = visualization.evidenceRefs.filter((item) => finding.evidenceRefIds.includes(item.evidenceRefId));
    if (evidenceLinkTargets.length) {
      for (const evidence of evidenceLinkTargets) {
        edges.push({
          id: `evidence-finding:${evidence.evidenceRefId}:${finding.findingId}`,
          source: `evidence:${evidence.evidenceRefId}`,
          target: `finding:${finding.findingId}`,
          data: { pathId: evidence.pathId ?? finding.pathIds[0] },
        });
      }
    } else if (finding.pathIds[0]) {
      edges.push({
        id: `path-finding:${finding.pathIds[0]}:${finding.findingId}`,
        source: `path:${finding.pathIds[0]}`,
        target: `finding:${finding.findingId}`,
        data: { pathId: finding.pathIds[0] },
      });
    }
  }

  return { nodes: positionNodes(nodes), edges };
}

function buildFindingDrawerData(
  visualization: BehaviorSpaceVisualization,
  finding: BehaviorFinding,
  evidenceRefs: readonly BehaviorEvidenceRef[],
  replay: BehaviorReplayRef | undefined,
): EvidenceDrawerData {
  const links: EvidenceDrawerLink[] = [];
  const detailHref = `/projects/${encodeURIComponent(visualization.scene.projectId)}/risks/${encodeURIComponent(finding.findingId)}`;
  links.push({ label: "风险详情", href: detailHref, kind: "page" });
  const replayHref = replay ? buildReplayHref(replay) : undefined;
  if (replayHref) links.push({ label: "Evidence Replay", href: replayHref, kind: "page" });
  for (const evidence of evidenceRefs.slice(0, 2)) {
    if (isSupportedHref(evidence.href)) links.push({ label: evidence.label, href: evidence.href, kind: "page" });
  }

  return {
    id: finding.findingId,
    typeLabel: "Behavior Space / Bug 节点",
    title: finding.title,
    summary: finding.summary,
    reason: `该风险挂载在 ${finding.pathIds.length} 条真实路径上，类型为 ${finding.riskType}，可继续下钻到风险详情或 Evidence Replay。`,
    remediationAction: finding.businessImpact ? `优先处理 ${finding.riskType}，并验证“${finding.businessImpact}”是否被 replay 与证据摘要完整覆盖。` : undefined,
    tone: finding.launchBlocking ? "critical" : "warning",
    statusLabel: `${finding.riskType} · ${finding.severity}`,
    fields: [
      { label: "风险类型", value: finding.riskType },
      { label: "严重度", value: finding.severity },
      { label: "上线阻断", value: finding.launchBlocking ? "是" : "否" },
      { label: "关联证据", value: `${evidenceRefs.length}` },
    ],
    sections: [
      {
        title: "关联路径",
        items: finding.pathIds.map((pathId) => visualization.behaviorPaths.find((item) => item.pathId === pathId)?.label ?? pathId),
      },
      {
        title: "证据摘要",
        items: evidenceRefs.slice(0, 6).map((item) => `${item.label} · ${item.summary}`),
      },
      {
        title: "Replay 摘要",
        items: replay?.steps.slice(0, 6) ?? ["当前未挂接复现步骤。"],
      },
    ],
    links,
  };
}

function buildEvidenceDrawerData(
  visualization: BehaviorSpaceVisualization,
  evidence: BehaviorEvidenceRef,
  finding: BehaviorFinding | undefined,
  replay: BehaviorReplayRef | undefined,
): EvidenceDrawerData {
  const links: EvidenceDrawerLink[] = [];
  if (isSupportedHref(evidence.href)) links.push({ label: "已有证据入口", href: evidence.href, kind: "page" });
  if (finding) {
    links.push({
      label: "关联风险详情",
      href: `/projects/${encodeURIComponent(visualization.scene.projectId)}/risks/${encodeURIComponent(finding.findingId)}`,
      kind: "page",
    });
  }
  const replayHref = replay ? buildReplayHref(replay) : undefined;
  if (replayHref) links.push({ label: "Evidence Replay", href: replayHref, kind: "page" });

  return {
    id: evidence.evidenceRefId,
    typeLabel: "Behavior Space / 证据节点",
    title: evidence.label,
    summary: evidence.summary,
    reason: finding
      ? `该证据已绑定到风险“${finding.title}”，可作为该风险的下钻入口。`
      : evidence.pathId
        ? "该证据已挂接到真实业务路径，可用于解释 coverage 与阻断来源。"
        : "该证据为页面级解释入口，可继续跳转到对应页面。",
    remediationAction: evidence.kind === "environment" ? "优先修复环境阻断后重新执行相关路径。" : undefined,
    tone: evidence.kind === "environment" ? "warning" : finding?.launchBlocking ? "critical" : "info",
    statusLabel: formatEvidenceKind(evidence.kind),
    fields: [
      { label: "证据类型", value: formatEvidenceKind(evidence.kind) },
      { label: "关联风险", value: finding?.title ?? "未直接绑定" },
      { label: "关联路径", value: evidence.pathId ? visualization.behaviorPaths.find((item) => item.pathId === evidence.pathId)?.label ?? evidence.pathId : "未直接绑定" },
      { label: "Replay", value: replay ? "已挂接" : "未挂接" },
    ],
    sections: [
      {
        title: "可继续下钻",
        items: replay ? replay.steps.slice(0, 6) : ["打开关联页面查看完整摘要或风险详情。"],
      },
    ],
    links,
  };
}

function renderEvidenceList(
  evidenceRefs: readonly BehaviorEvidenceRef[],
  getHref: (ref: BehaviorEvidenceRef) => string | undefined,
  onOpenDrawer: (ref: BehaviorEvidenceRef) => void,
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
            <div className="mt-3 flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => onOpenDrawer(ref)}
                className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.44)] px-2.5 py-1.5 text-xs text-[var(--fg)] hover:border-[rgba(255,255,255,0.18)]"
              >
                打开 Drawer
              </button>
              {href ? (
                <Link href={href} className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.44)] px-2.5 py-1.5 text-xs text-[var(--fg)] hover:border-[rgba(255,255,255,0.18)]">
                  打开证据入口
                </Link>
              ) : null}
            </div>
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

function RiskQuickActionCard({
  finding,
  projectId,
  active,
  onFocus,
  onOpenDrawer,
}: {
  finding: BehaviorFinding;
  projectId: string;
  active: boolean;
  onFocus: (finding: BehaviorFinding) => void;
  onOpenDrawer: (finding: BehaviorFinding) => void;
}) {
  const detailHref = buildFindingDetailHref(projectId, finding.findingId);

  return (
    <div
      className={`rounded-[var(--radius-sm)] border p-4 transition ${
        active
          ? "border-[rgba(255,92,122,0.34)] bg-[rgba(255,92,122,0.09)]"
          : "border-[var(--border)] bg-[rgba(16,24,38,0.32)]"
      }`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-sm font-semibold text-[var(--fg)]">{finding.title}</div>
          <div className="mt-1 text-xs text-[var(--muted)]">
            {finding.riskType} · {finding.severity} · {finding.launchBlocking ? "上线阻断" : "非阻断"}
          </div>
        </div>
        <div className="rounded-full border border-[rgba(255,255,255,0.1)] bg-[rgba(255,255,255,0.04)] px-2 py-1 text-[11px] text-[var(--muted)]">
          路径 {finding.pathIds.length}
        </div>
      </div>
      <div className="mt-2 text-sm text-[var(--muted)]">{finding.summary}</div>
      <div className="mt-3 flex flex-wrap gap-2">
        <Link
          href={detailHref}
          className="inline-flex min-h-[40px] items-center justify-center rounded-[var(--radius-sm)] border border-[rgba(255,92,122,0.28)] bg-[rgba(255,92,122,0.12)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(255,255,255,0.18)]"
        >
          打开风险详情
        </Link>
        <button
          type="button"
          onClick={() => onOpenDrawer(finding)}
          className="inline-flex min-h-[40px] items-center justify-center rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.44)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(255,255,255,0.18)]"
        >
          打开证据 Drawer
        </button>
        <button
          type="button"
          onClick={() => onFocus(finding)}
          className="inline-flex min-h-[40px] items-center justify-center rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.44)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(255,255,255,0.18)]"
        >
          在图谱中定位
        </button>
      </div>
    </div>
  );
}

function BehaviorSpaceInspector({
  selection,
  visualization,
  onSelect,
  focusedPathId,
  onOpenFindingDrawer,
  onOpenEvidenceDrawer,
}: {
  selection: Selection;
  visualization: BehaviorSpaceVisualization;
  onSelect: (next: Selection) => void;
  focusedPathId: string | null;
  onOpenFindingDrawer: (finding: BehaviorFinding) => void;
  onOpenEvidenceDrawer: (evidence: BehaviorEvidenceRef) => void;
}) {
  const roleById = useMemo(() => new Map(visualization.roleNodes.map((item) => [item.roleId, item])), [visualization.roleNodes]);
  const systemById = useMemo(() => new Map(visualization.systemNodes.map((item) => [item.nodeId, item])), [visualization.systemNodes]);
  const pathById = useMemo(() => new Map(visualization.behaviorPaths.map((item) => [item.pathId, item])), [visualization.behaviorPaths]);
  const findingById = useMemo(() => new Map(visualization.findings.map((item) => [item.findingId, item])), [visualization.findings]);
  const evidenceById = useMemo(() => new Map(visualization.evidenceRefs.map((item) => [item.evidenceRefId, item])), [visualization.evidenceRefs]);
  const replayById = useMemo(() => new Map(visualization.replayRefs.map((item) => [item.replayRefId, item])), [visualization.replayRefs]);
  const getEvidenceHref = (ref: BehaviorEvidenceRef) => (isSupportedHref(ref.href) ? ref.href : undefined);

  if (selection.type === "scene") {
    const actions = [
      visualization.valueSummary.coverage.href,
      visualization.valueSummary.riskCost.href,
      visualization.valueSummary.auditReadiness.href,
      visualization.valueSummary.efficiencyGain.href,
    ]
      .filter(isSupportedHref)
      .map((href) => ({
        href,
        label:
          href.includes("/risks")
            ? "查看风险证据"
            : href.includes("/reports")
              ? "打开领导层报告"
              : href.includes("/roi")
                ? "查看 ROI"
                : href.includes("/capabilities")
                  ? "查看覆盖中心"
                  : "打开入口",
      }));

    return (
      <div className="grid gap-4">
        <div>
          <div className="text-xs uppercase tracking-[0.22em] text-[var(--muted)]">Scene</div>
          <h2 className="mt-2 text-lg font-semibold text-[var(--fg)]">{visualization.scene.title}</h2>
          <p className="mt-2 text-sm leading-6 text-[var(--muted)]">{visualization.scene.summary}</p>
          <div className="mt-3 text-xs text-[var(--muted)]">
            当前高亮路径: {focusedPathId ? pathById.get(focusedPathId)?.label ?? focusedPathId : "全部路径"}
          </div>
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

        <div className="grid gap-2 md:grid-cols-2">
          <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.32)] p-3">
            <div className="text-xs text-[var(--muted)]">分层规模</div>
            <div className="mt-2 grid gap-1 text-sm text-[var(--fg)]">
              <div>角色层 {visualization.roleNodes.length}</div>
              <div>行为层 {visualization.behaviorPaths.length}</div>
              <div>系统层 {visualization.systemNodes.length}</div>
              <div>证据层 {visualization.evidenceRefs.length}</div>
              <div>风险层 {visualization.findings.length}</div>
            </div>
          </div>
          <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.32)] p-3">
            <div className="text-xs text-[var(--muted)]">回放入口</div>
            <div className="mt-2">{renderReplayList(visualization.replayRefs, onSelect)}</div>
          </div>
        </div>

        <div>
          <div className="text-xs text-[var(--muted)]">审计轨迹</div>
          <div className="mt-2">{renderAuditList(visualization.auditRefs)}</div>
        </div>
      </div>
    );
  }

  if (selection.type === "role") {
    const role = roleById.get(selection.id);
    if (!role) return null;
    const relatedPaths = role.pathIds.map((pathId) => pathById.get(pathId)).filter((item): item is BehaviorPath => Boolean(item));
    return (
      <div className="grid gap-4">
        <div>
          <div className="text-xs uppercase tracking-[0.22em] text-[var(--muted)]">Role Layer</div>
          <h2 className="mt-2 text-lg font-semibold text-[var(--fg)]">{role.label}</h2>
          <p className="mt-2 text-sm leading-6 text-[var(--muted)]">{role.summary}</p>
          <div className="mt-2 text-sm text-[var(--muted)]">
            {summarizeStatus(role.status)} · 路径 {role.pathIds.length} · 风险 {role.riskCount}
          </div>
        </div>
        <div>
          <div className="text-xs text-[var(--muted)]">关联行为路径</div>
          <div className="mt-2 grid gap-2">
            {relatedPaths.map((path) => (
              <button
                key={path.pathId}
                type="button"
                onClick={() => onSelect({ type: "path", id: path.pathId })}
                className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.32)] px-3 py-3 text-left text-sm text-[var(--fg)] hover:border-[rgba(255,255,255,0.18)]"
              >
                <div>{path.label}</div>
                <div className="mt-1 text-xs text-[var(--muted)]">
                  coverage {coverageText(path)} · 风险 {path.riskCount}
                </div>
              </button>
            ))}
          </div>
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
          <div className="text-xs uppercase tracking-[0.22em] text-[var(--muted)]">System Layer</div>
          <h2 className="mt-2 text-lg font-semibold text-[var(--fg)]">{systemNode.label}</h2>
          <div className="mt-2 text-sm text-[var(--muted)]">
            {formatSystemKind(systemNode.kind)}
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
                    coverage {coverageText(path)} · 风险 {path.riskCount} · 阻断 {path.blockerCount}
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
          <div className="mt-2">{renderEvidenceList(relatedEvidence, getEvidenceHref, onOpenEvidenceDrawer)}</div>
        </div>
      </div>
    );
  }

  if (selection.type === "path") {
    const path = pathById.get(selection.id);
    if (!path) return null;
    const relatedRoles = path.roleIds.map((id) => roleById.get(id)).filter((item): item is BehaviorRoleNode => Boolean(item));
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
          <div className="text-xs uppercase tracking-[0.22em] text-[var(--muted)]">Behavior Layer</div>
          <h2 className="mt-2 text-lg font-semibold text-[var(--fg)]">{path.label}</h2>
          <div className="mt-2 text-sm text-[var(--muted)]">
            coverage {coverageText(path)} · {summarizeStatus(path.coverageStatus)} · 风险 {path.riskCount} · 阻断 {path.blockerCount}
          </div>
          <p className="mt-2 text-sm leading-6 text-[var(--muted)]">{path.summary ?? "该路径代表一条已建模的真实业务路径。"}</p>
          <ActionButtons actions={actions} />
        </div>

        <div>
          <div className="text-xs text-[var(--muted)]">角色入口</div>
          <div className="mt-2 flex flex-wrap gap-2">
            {relatedRoles.length ? (
              relatedRoles.map((role) => (
                <button
                  key={role.roleId}
                  type="button"
                  onClick={() => onSelect({ type: "role", id: role.roleId })}
                  className="rounded-full border border-[var(--border)] bg-[rgba(16,24,38,0.32)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(255,255,255,0.18)]"
                >
                  {role.label}
                </button>
              ))
            ) : (
              <div className="text-sm text-[var(--muted)]">暂无角色挂载。</div>
            )}
          </div>
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
                <div key={finding.findingId} className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.32)] p-3">
                  <div className="text-sm text-[var(--fg)]">{finding.title}</div>
                  <div className="mt-1 text-xs text-[var(--muted)]">
                    {finding.riskType} · {finding.severity} · {finding.launchBlocking ? "上线阻断" : "可后续处理"}
                  </div>
                  <div className="mt-3 flex flex-wrap gap-2">
                    <button
                      type="button"
                      onClick={() => onOpenFindingDrawer(finding)}
                      className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.44)] px-2.5 py-1.5 text-xs text-[var(--fg)] hover:border-[rgba(255,255,255,0.18)]"
                    >
                      打开 Drawer
                    </button>
                    <button
                      type="button"
                      onClick={() => onSelect({ type: "finding", id: finding.findingId })}
                      className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.44)] px-2.5 py-1.5 text-xs text-[var(--fg)] hover:border-[rgba(255,255,255,0.18)]"
                    >
                      查看详情
                    </button>
                  </div>
                </div>
              ))
            ) : (
              <div className="text-sm text-[var(--muted)]">当前路径未挂载风险。</div>
            )}
          </div>
        </div>

        <div>
          <div className="text-xs text-[var(--muted)]">证据摘要</div>
          <div className="mt-2">{renderEvidenceList(relatedEvidence, getEvidenceHref, onOpenEvidenceDrawer)}</div>
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
          <div className="text-xs uppercase tracking-[0.22em] text-[var(--muted)]">Risk Layer</div>
          <h2 className="mt-2 text-lg font-semibold text-[var(--fg)]">{finding.title}</h2>
          <div className="mt-2 text-sm text-[var(--muted)]">
            {finding.riskType} · {finding.severity} · {finding.launchBlocking ? "上线阻断" : "非阻断"}
          </div>
          <p className="mt-2 text-sm leading-6 text-[var(--muted)]">{finding.summary}</p>
          {finding.businessImpact ? <p className="mt-2 text-sm text-[var(--fg)]">业务影响：{finding.businessImpact}</p> : null}
          <div className="mt-3">
            <button
              type="button"
              onClick={() => onOpenFindingDrawer(finding)}
              className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(255,92,122,0.12)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(255,255,255,0.18)]"
            >
              打开证据 Drawer
            </button>
          </div>
          <ActionButtons actions={actions} />
        </div>

        <div>
          <div className="text-xs text-[var(--muted)]">证据入口</div>
          <div className="mt-2">{renderEvidenceList(evidenceRefs, getEvidenceHref, onOpenEvidenceDrawer)}</div>
        </div>

        {replay ? (
          <div>
            <div className="text-xs text-[var(--muted)]">Replay 摘要</div>
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
          <div className="text-xs uppercase tracking-[0.22em] text-[var(--muted)]">Evidence Replay</div>
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
          <div className="mt-2">{renderEvidenceList(evidenceRefs, getEvidenceHref, onOpenEvidenceDrawer)}</div>
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
        <div className="text-xs uppercase tracking-[0.22em] text-[var(--muted)]">Evidence Layer</div>
        <h2 className="mt-2 text-lg font-semibold text-[var(--fg)]">{evidence.label}</h2>
        <div className="mt-2 text-sm text-[var(--muted)]">{formatEvidenceKind(evidence.kind)}</div>
        <p className="mt-2 text-sm leading-6 text-[var(--muted)]">{evidence.summary}</p>
        <div className="mt-3">
          <button
            type="button"
            onClick={() => onOpenEvidenceDrawer(evidence)}
            className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(122,167,255,0.12)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(255,255,255,0.18)]"
          >
            打开证据 Drawer
          </button>
        </div>
        <ActionButtons actions={actions} />
      </div>
    </div>
  );
}

function styleGraphForFocus(
  visualization: BehaviorSpaceVisualization,
  graph: { nodes: GraphNode[]; edges: GraphEdge[] },
  focusedPathId: string | null,
  riskTypeFilter: string,
): { nodes: GraphNode[]; edges: GraphEdge[] } {
  const activeFindingIds = new Set(
    visualization.findings
      .filter((finding) => riskTypeFilter === DEFAULT_RISK_FILTER || finding.riskType === riskTypeFilter)
      .map((finding) => finding.findingId),
  );
  const filterPathIds = new Set(
    visualization.findings
      .filter((finding) => riskTypeFilter === DEFAULT_RISK_FILTER || finding.riskType === riskTypeFilter)
      .flatMap((finding) => finding.pathIds),
  );
  const activePathIds = focusedPathId ? new Set([focusedPathId]) : filterPathIds.size ? filterPathIds : new Set(visualization.behaviorPaths.map((path) => path.pathId));

  const nodes = graph.nodes.map((node) => {
    const pathMatch = node.data.relatedPathIds.some((pathId) => activePathIds.has(pathId));
    const riskTypeMatch =
      node.data.entityType !== "finding" || riskTypeFilter === DEFAULT_RISK_FILTER || node.data.riskType === riskTypeFilter;
    const isActive = pathMatch && riskTypeMatch;
    const tone = node.data.tone;
    const palette = toneStyles[tone];
    return {
      ...node,
      hidden: node.data.entityType === "finding" && !riskTypeMatch,
      style: {
        ...nodeDimensions(node.data.layerId),
        opacity: isActive ? 1 : 0.24,
        borderRadius: 18,
        transition: "opacity 120ms ease",
        boxShadow: isActive ? `0 0 0 1px ${palette.border}` : "none",
      },
      selected: node.data.entityType === "finding" && activeFindingIds.has(node.data.entityId),
    };
  });

  const edges = graph.edges.map((edge) => {
    const path = visualization.behaviorPaths.find((item) => item.pathId === edge.data?.pathId);
    const tone = path ? toneFromCoverageStatus(path.coverageStatus) : "neutral";
    const palette = toneStyles[tone];
    const isActive = edge.data?.pathId ? activePathIds.has(edge.data.pathId) : false;
    return {
      ...edge,
      animated: Boolean(focusedPathId && edge.data?.pathId === focusedPathId),
      style: {
        stroke: palette.solid,
        strokeWidth: isActive ? 2.6 : 1.2,
        strokeDasharray: focusedPathId && edge.data?.pathId === focusedPathId ? "0" : "6 4",
        opacity: isActive ? 0.95 : 0.16,
      },
      markerEnd: {
        type: MarkerType.ArrowClosed,
        width: 18,
        height: 18,
        color: palette.solid,
      },
      label: focusedPathId && edge.data?.pathId === focusedPathId ? path?.label : undefined,
      labelStyle: { fill: "#cfd7e3", fontSize: 11 },
    };
  });

  return { nodes, edges };
}

export function BehaviorSpaceFlow({ visualization }: { visualization: BehaviorSpaceVisualization }) {
  const graph = useMemo(() => buildBaseGraph(visualization), [visualization]);
  const [nodes, setNodes, onNodesChange] = useNodesState<GraphNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<GraphEdge>([]);
  const [selection, setSelection] = useState<Selection>(DEFAULT_SELECTION);
  const [focusedPathId, setFocusedPathId] = useState<string | null>(null);
  const [riskTypeFilter, setRiskTypeFilter] = useState<string>(DEFAULT_RISK_FILTER);
  const [drawerData, setDrawerData] = useState<EvidenceDrawerData | null>(null);

  const roleById = useMemo(() => new Map(visualization.roleNodes.map((item) => [item.roleId, item])), [visualization.roleNodes]);
  const pathById = useMemo(() => new Map(visualization.behaviorPaths.map((item) => [item.pathId, item])), [visualization.behaviorPaths]);
  const findingById = useMemo(() => new Map(visualization.findings.map((item) => [item.findingId, item])), [visualization.findings]);
  const evidenceById = useMemo(() => new Map(visualization.evidenceRefs.map((item) => [item.evidenceRefId, item])), [visualization.evidenceRefs]);
  const replayById = useMemo(() => new Map(visualization.replayRefs.map((item) => [item.replayRefId, item])), [visualization.replayRefs]);

  const riskTypeOptions = useMemo(
    () => [DEFAULT_RISK_FILTER, ...Array.from(new Set(visualization.findings.map((finding) => finding.riskType)))],
    [visualization.findings],
  );
  const overallCoverage = useMemo(() => {
    if (!visualization.behaviorPaths.length) return 0;
    const total = visualization.behaviorPaths.reduce((sum, path) => sum + path.coveragePercent, 0);
    return Math.round(total / visualization.behaviorPaths.length);
  }, [visualization.behaviorPaths]);
  const coveredCount = useMemo(
    () => visualization.behaviorPaths.filter((path) => path.coverageStatus === "covered").length,
    [visualization.behaviorPaths],
  );
  const visibleFindings = useMemo(
    () =>
      visualization.findings.filter((finding) => {
        const riskTypeMatch = riskTypeFilter === DEFAULT_RISK_FILTER || finding.riskType === riskTypeFilter;
        const pathMatch = focusedPathId === null || finding.pathIds.includes(focusedPathId);
        return riskTypeMatch && pathMatch;
      }),
    [focusedPathId, riskTypeFilter, visualization.findings],
  );

  useEffect(() => {
    const styled = styleGraphForFocus(visualization, graph, focusedPathId, riskTypeFilter);
    setNodes(styled.nodes);
    setEdges(styled.edges);
  }, [graph, focusedPathId, riskTypeFilter, setEdges, setNodes, visualization]);

  const openFindingDrawer = (finding: BehaviorFinding) => {
    const evidenceRefs = finding.evidenceRefIds.map((id) => evidenceById.get(id)).filter((item): item is BehaviorEvidenceRef => Boolean(item));
    const replay = finding.replayRefId ? replayById.get(finding.replayRefId) : undefined;
    setDrawerData(buildFindingDrawerData(visualization, finding, evidenceRefs, replay));
  };

  const openEvidenceDrawer = (evidence: BehaviorEvidenceRef) => {
    const finding = evidence.findingId ? findingById.get(evidence.findingId) : undefined;
    const replay = finding?.replayRefId ? replayById.get(finding.replayRefId) : undefined;
    setDrawerData(buildEvidenceDrawerData(visualization, evidence, finding, replay));
  };

  return (
    <>
      <div className="relative isolate grid gap-4 xl:items-start xl:grid-cols-[minmax(0,1fr)_360px]">
        <div className="grid min-w-0 gap-4">
          <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(16,24,38,0.55)] p-4 shadow-[var(--shadow-1)] backdrop-blur">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="max-w-3xl">
                <div className="text-xs text-[var(--muted)]">Layered Behavior Space</div>
                <div className="mt-2 text-lg font-semibold text-[var(--fg)]">角色 / 行为 / 系统 / 证据 / 风险 五层图谱</div>
                <div className="mt-2 text-sm text-[var(--muted)]">
                  先看 coverage，再按风险类型筛选，最后点选路径高亮整条真实业务路线；bug 和证据节点可直接打开 Drawer 或跳到 Evidence Replay。
                </div>
              </div>
              <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
                <div className="rounded-[var(--radius-sm)] border border-[rgba(89,243,194,0.18)] bg-[rgba(89,243,194,0.08)] px-3 py-3 text-sm text-[var(--fg)]">
                  平均 coverage
                  <div className="mt-1 text-lg font-semibold">{overallCoverage}%</div>
                </div>
                <div className="rounded-[var(--radius-sm)] border border-[rgba(122,167,255,0.18)] bg-[rgba(122,167,255,0.08)] px-3 py-3 text-sm text-[var(--fg)]">
                  已覆盖路径
                  <div className="mt-1 text-lg font-semibold">
                    {coveredCount}/{visualization.behaviorPaths.length}
                  </div>
                </div>
                <div className="rounded-[var(--radius-sm)] border border-[rgba(255,92,122,0.18)] bg-[rgba(255,92,122,0.08)] px-3 py-3 text-sm text-[var(--fg)]">
                  风险类型
                  <div className="mt-1 text-lg font-semibold">{riskTypeFilter}</div>
                </div>
              </div>
            </div>

            <div className="mt-4">
              <div className="text-xs text-[var(--muted)]">层级概览</div>
              <div className="mt-2 flex flex-wrap gap-2">
                <LayerChip active label="角色层" value={`${visualization.roleNodes.length}`} onClick={() => setSelection({ type: "scene", id: "scene" })} />
                <LayerChip active label="行为层" value={`${visualization.behaviorPaths.length}`} onClick={() => setSelection({ type: "scene", id: "scene" })} />
                <LayerChip active label="系统层" value={`${visualization.systemNodes.length}`} onClick={() => setSelection({ type: "scene", id: "scene" })} />
                <LayerChip active label="证据层" value={`${visualization.evidenceRefs.length}`} onClick={() => setSelection({ type: "scene", id: "scene" })} />
                <LayerChip active label="风险层" value={`${visualization.findings.length}`} onClick={() => setSelection({ type: "scene", id: "scene" })} />
              </div>
            </div>

            <div className="mt-4">
              <div className="text-xs text-[var(--muted)]">风险类型筛选</div>
              <div className="mt-2 flex flex-wrap gap-2">
                {riskTypeOptions.map((option) => (
                  <LayerChip
                    key={option}
                    active={riskTypeFilter === option}
                    label={option}
                    value={`${option === DEFAULT_RISK_FILTER ? visualization.findings.length : visualization.findings.filter((item) => item.riskType === option).length}`}
                    onClick={() => setRiskTypeFilter(option)}
                  />
                ))}
              </div>
            </div>

            <div className="mt-4">
              <div className="text-xs text-[var(--muted)]">路径高亮</div>
              <div className="mt-2 flex flex-wrap gap-2">
                <LayerChip active={focusedPathId === null} label="全部路径" value={`${visualization.behaviorPaths.length}`} onClick={() => setFocusedPathId(null)} />
                {visualization.behaviorPaths.map((path) => (
                  <LayerChip
                    key={path.pathId}
                    active={focusedPathId === path.pathId}
                    label={path.label}
                    value={coverageText(path)}
                    onClick={() => {
                      setFocusedPathId(path.pathId);
                      setSelection({ type: "path", id: path.pathId });
                    }}
                  />
                ))}
              </div>
            </div>
          </div>

          <div className="rounded-[var(--radius-md)] border border-[rgba(255,92,122,0.18)] bg-[rgba(255,92,122,0.06)] p-4 shadow-[var(--shadow-1)]">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="max-w-3xl">
                <div className="text-xs text-[var(--muted)]">稳定操作区</div>
                <div className="mt-2 text-base font-semibold text-[var(--fg)]">风险详情入口前置，不依赖图内节点点击</div>
                <div className="mt-2 text-sm text-[var(--muted)]">
                  这里提供更稳定、可视、可点击的独立风险卡片；五层图谱继续保留原有结构与联动，仅把容易受图层交互影响的风险详情入口前移。
                </div>
              </div>
              <div className="rounded-[var(--radius-sm)] border border-[rgba(255,255,255,0.1)] bg-[rgba(16,24,38,0.36)] px-3 py-2 text-xs text-[var(--muted)]">
                当前展示 {Math.min(visibleFindings.length, 4)} / {visibleFindings.length} 个风险
              </div>
            </div>
            <div className="mt-4 grid gap-3 xl:grid-cols-2">
              {visibleFindings.length ? (
                visibleFindings.slice(0, 4).map((finding) => (
                  <RiskQuickActionCard
                    key={finding.findingId}
                    finding={finding}
                    projectId={visualization.scene.projectId}
                    active={selection.type === "finding" && selection.id === finding.findingId}
                    onOpenDrawer={openFindingDrawer}
                    onFocus={(nextFinding) => {
                      const nextPathId = nextFinding.pathIds[0] ?? null;
                      setSelection({ type: "finding", id: nextFinding.findingId });
                      setFocusedPathId(nextPathId);
                    }}
                  />
                ))
              ) : (
                <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.32)] p-4 text-sm text-[var(--muted)] xl:col-span-2">
                  当前筛选条件下暂无风险卡片，可切换风险类型或路径高亮查看其他风险。
                </div>
              )}
            </div>
          </div>

          <div className="relative z-0 min-h-[900px] overflow-hidden rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(9,14,22,0.86)] xl:sticky xl:top-24">
            <ReactFlow<GraphNode, GraphEdge>
              nodes={nodes}
              edges={edges}
              nodeTypes={nodeTypes}
              className="[&_.react-flow__controls]:!z-[1] [&_.react-flow__minimap]:!z-[1] [&_.react-flow__panel]:!z-[1]"
              fitView
              fitViewOptions={{ padding: 0.12 }}
              minZoom={0.3}
              maxZoom={1.5}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onNodeClick={(_, node) => {
                const relatedPathId = firstPathId(node.data.relatedPathIds);
                if (relatedPathId) setFocusedPathId(relatedPathId);
                if (node.data.entityType === "finding") {
                  const finding = findingById.get(node.data.entityId);
                  if (finding) openFindingDrawer(finding);
                }
                if (node.data.entityType === "evidence") {
                  const evidence = evidenceById.get(node.data.entityId);
                  if (evidence) openEvidenceDrawer(evidence);
                }
                setSelection({ type: node.data.entityType, id: node.data.entityId });
              }}
              onEdgeClick={(_, edge) => {
                const pathId = typeof edge.data?.pathId === "string" ? edge.data.pathId : null;
                if (pathId) {
                  setFocusedPathId(pathId);
                  setSelection({ type: "path", id: pathId });
                }
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
                  return toneStyles[tone as BehaviorSpaceTone].solid;
                }}
                maskColor="rgba(7,10,18,0.72)"
                bgColor="rgba(11,15,20,0.96)"
              />
              <Controls position="bottom-left" />
              <Panel position="top-left">
                <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(11,15,20,0.88)] px-4 py-3 text-xs text-[var(--muted)] shadow-[var(--shadow-1)]">
                  <div className="font-semibold text-[var(--fg)]">2D 分层主视图</div>
                  <div className="mt-1">Role = 触发入口，Behavior = 真实路径，System = 建模模块，Evidence = 可下钻证据，Risk = bug 暴露点。</div>
                </div>
              </Panel>
              <Panel position="top-right">
                <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(11,15,20,0.88)] px-4 py-3 text-xs text-[var(--muted)] shadow-[var(--shadow-1)]">
                  当前焦点: {focusedPathId ? pathById.get(focusedPathId)?.label ?? focusedPathId : "全部路径"} · 风险筛选: {riskTypeFilter}
                </div>
              </Panel>
            </ReactFlow>
          </div>
        </div>

        <aside className="relative z-30 min-w-0 overflow-hidden rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(16,24,38,0.78)] p-5 shadow-[var(--shadow-1)] backdrop-blur xl:sticky xl:top-24 xl:max-h-[calc(100vh-7rem)] xl:self-start">
          <div className="text-xs text-[var(--muted)]">下钻面板</div>
          <div className="mt-4 xl:max-h-[calc(100vh-11rem)] xl:overflow-y-auto xl:pr-1">
            <BehaviorSpaceInspector
              selection={selection}
              visualization={visualization}
              onSelect={setSelection}
              focusedPathId={focusedPathId}
              onOpenFindingDrawer={openFindingDrawer}
              onOpenEvidenceDrawer={openEvidenceDrawer}
            />
          </div>
        </aside>
      </div>

      <EvidenceDrawer open={Boolean(drawerData)} data={drawerData} onClose={() => setDrawerData(null)} />
    </>
  );
}
