"use client";

import { useMemo, useRef, useState } from "react";
import Link from "next/link";
import { Canvas, useFrame } from "@react-three/fiber";
import { Line, OrbitControls } from "@react-three/drei";
import type { Group } from "three";
import type { BehaviorFinding, BehaviorPath, BehaviorSpaceVisualization, BehaviorSystemNode } from "@/behavior-space/types";

type NodeLayout = {
  node: BehaviorSystemNode;
  position: readonly [number, number, number];
  relatedPaths: readonly BehaviorPath[];
  relatedFindings: readonly BehaviorFinding[];
};

const palette = {
  positive: "#59f3c2",
  warning: "#f5bf50",
  critical: "#ff5c7a",
  neutral: "#7aa7ff",
  floor: "#09111c",
  floorEdge: "#13253e",
  lane: "#173454",
} as const;

function statusColor(status: string): string {
  if (status === "ready" || status === "covered") return palette.positive;
  if (status === "warning" || status === "partial") return palette.warning;
  if (status === "blocked" || status === "uncovered") return palette.critical;
  return palette.neutral;
}

function formatSceneState(status: string): string {
  if (status === "ready" || status === "covered") return "已就绪";
  if (status === "warning" || status === "partial") return "需关注";
  if (status === "blocked" || status === "uncovered") return "已阻断";
  return "待判定";
}

function formatNodeKind(kind: string): string {
  if (kind === "frontend") return "前端入口";
  if (kind === "service") return "业务服务";
  if (kind === "database") return "数据存储";
  if (kind === "queue") return "消息队列";
  if (kind === "external_api") return "外部接口";
  if (kind === "worker") return "异步任务";
  if (kind === "environment") return "环境信号";
  return "系统节点";
}

function buildNodeLayouts(visualization: BehaviorSpaceVisualization): NodeLayout[] {
  const lanes = new Map<string, BehaviorSystemNode[]>();
  for (const node of visualization.systemNodes) {
    const domain = node.domain ?? "核心域";
    lanes.set(domain, [...(lanes.get(domain) ?? []), node]);
  }

  const domains = Array.from(lanes.keys());
  const domainCount = Math.max(domains.length, 1);
  return domains.flatMap((domain, laneIndex) => {
    const laneNodes = lanes.get(domain) ?? [];
    return laneNodes.map((node, nodeIndex) => {
      const relatedPaths = visualization.behaviorPaths.filter((path) => node.flowIds.includes(path.pathId));
      const relatedFindings = visualization.findings.filter((finding) =>
        finding.pathIds.some((pathId) => node.flowIds.includes(pathId)),
      );

      return {
        node,
        position: [(nodeIndex - (laneNodes.length - 1) / 2) * 8.2, 0, (laneIndex - (domainCount - 1) / 2) * 7.6] as const,
        relatedPaths,
        relatedFindings,
      };
    });
  });
}

function EnvironmentBeacon({
  index,
  status,
}: {
  index: number;
  status: string;
}) {
  const ref = useRef<Group>(null);
  const color = statusColor(status);

  useFrame(({ clock }) => {
    const pulse = 1 + Math.sin(clock.elapsedTime * 1.8 + index) * 0.06;
    if (ref.current) ref.current.scale.set(pulse, 1, pulse);
  });

  return (
    <group position={[-18, 0, 8 - index * 8]}>
      <mesh position={[0, 0.4, 0]} castShadow receiveShadow>
        <cylinderGeometry args={[0.8, 0.8, 0.8, 32]} />
        <meshStandardMaterial color={palette.floorEdge} metalness={0.15} roughness={0.7} />
      </mesh>
      <group ref={ref}>
        <mesh position={[0, 1.85, 0]} castShadow>
          <cylinderGeometry args={[0.38, 0.38, 2.1, 18]} />
          <meshStandardMaterial color={color} emissive={color} emissiveIntensity={0.6} metalness={0.2} roughness={0.35} />
        </mesh>
        <mesh position={[0, 3.15, 0]}>
          <sphereGeometry args={[0.6, 20, 20]} />
          <meshStandardMaterial color={color} emissive={color} emissiveIntensity={0.9} />
        </mesh>
      </group>
      <mesh position={[0, 0.03, 0]} rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
        <ringGeometry args={[1.2, 1.48, 36]} />
        <meshStandardMaterial color={color} emissive={color} emissiveIntensity={0.35} side={2} />
      </mesh>
      <mesh position={[0, 0.06, 0]} rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
        <planeGeometry args={[2.9, 1.8]} />
        <meshStandardMaterial color={palette.floorEdge} metalness={0.1} roughness={0.9} />
      </mesh>
      <mesh position={[0, 0.08, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        <planeGeometry args={[2.1, 1]} />
        <meshStandardMaterial color={color} emissive={color} emissiveIntensity={0.15} />
      </mesh>
      <mesh position={[0, 0.1, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        <planeGeometry args={[1.9, 0.82]} />
        <meshStandardMaterial color={palette.floor} />
      </mesh>
    </group>
  );
}

function RiskPulse({ active, riskCount }: { active: boolean; riskCount: number }) {
  const ref = useRef<Group>(null);

  useFrame(({ clock }) => {
    if (!ref.current) return;
    const pulse = 1 + Math.sin(clock.elapsedTime * 2.6) * 0.08;
    ref.current.scale.setScalar(active ? pulse : 1);
  });

  return (
    <group ref={ref}>
      <mesh position={[0, 0, 0]}>
        <sphereGeometry args={[0.55 + Math.min(riskCount, 4) * 0.12, 18, 18]} />
        <meshStandardMaterial color={palette.critical} emissive={palette.critical} emissiveIntensity={active ? 1.4 : 0.75} />
      </mesh>
      <mesh position={[0, -0.86, 0]}>
        <cylinderGeometry args={[0.1, 0.16, 1.4, 16]} />
        <meshStandardMaterial color={palette.critical} emissive={palette.critical} emissiveIntensity={0.6} />
      </mesh>
    </group>
  );
}

function SystemTower({
  layout,
  selected,
  onSelect,
}: {
  layout: NodeLayout;
  selected: boolean;
  onSelect: (nodeId: string) => void;
}) {
  const height = 1.5 + Math.min(layout.node.riskCount, 4) * 0.55 + (layout.node.status === "blocked" ? 0.65 : 0);
  const color = statusColor(layout.node.status);

  return (
    <group position={layout.position}>
      <mesh position={[0, height / 2, 0]} castShadow receiveShadow onClick={() => onSelect(layout.node.nodeId)}>
        <boxGeometry args={[3.3, height, 3.3]} />
        <meshStandardMaterial color={color} emissive={color} emissiveIntensity={selected ? 0.45 : 0.18} metalness={0.3} roughness={0.42} />
      </mesh>
      <mesh position={[0, 0.08, 0]} rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
        <ringGeometry args={[2.35, 2.9, 40]} />
        <meshStandardMaterial color={selected ? palette.neutral : color} emissive={selected ? palette.neutral : color} emissiveIntensity={0.25} side={2} />
      </mesh>
      {layout.node.riskCount > 0 ? <group position={[0, height + 1.1, 0]}><RiskPulse active={selected} riskCount={layout.node.riskCount} /></group> : null}
    </group>
  );
}

function PathRibbon({
  from,
  to,
  color,
}: {
  from: readonly [number, number, number];
  to: readonly [number, number, number];
  color: string;
}) {
  const lift = Math.max(Math.abs(to[0] - from[0]) * 0.06, 1.1);
  return (
    <Line
      points={[
        [from[0], 0.36, from[2]],
        [(from[0] + to[0]) / 2, lift, (from[2] + to[2]) / 2],
        [to[0], 0.36, to[2]],
      ]}
      color={color}
      lineWidth={2.4}
      transparent
      opacity={0.8}
    />
  );
}

export function BehaviorSpaceSandbox({ visualization }: { visualization: BehaviorSpaceVisualization }) {
  const [open, setOpen] = useState(false);
  const layouts = useMemo(() => buildNodeLayouts(visualization), [visualization]);
  const [selectedNodeId, setSelectedNodeId] = useState<string>(layouts.find((item) => item.node.riskCount > 0)?.node.nodeId ?? layouts[0]?.node.nodeId ?? "scene");

  const selected = layouts.find((item) => item.node.nodeId === selectedNodeId) ?? layouts[0];
  const links = [
    { label: "继续用 2D 主视图分析", href: "#behavior-space-2d" },
    { label: "打开风险证据", href: `/projects/${encodeURIComponent(visualization.scene.projectId)}/risks` },
    { label: "查看领导层报告", href: `/projects/${encodeURIComponent(visualization.scene.projectId)}/reports/executive` },
  ];

  if (!layouts.length) return null;

  return (
    <section className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[linear-gradient(180deg,rgba(8,14,24,0.96),rgba(12,21,35,0.88))] p-5 shadow-[var(--shadow-1)]">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="max-w-3xl">
          <div className="text-xs uppercase tracking-[0.24em] text-[var(--muted)]">2.5D Showcase</div>
          <h2 className="mt-2 text-lg font-semibold text-[var(--fg)]">旗舰演示层入口</h2>
          <p className="mt-2 text-sm leading-6 text-[var(--muted)]">
            把系统节点映射成体块、把环境状态映射成信号灯、把真实业务路径映射成穿行轨迹、把风险暴露映射成脉冲警示点，只用于高价值演示，不替代 2D 主工作流。
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => setOpen((value) => !value)}
            className="inline-flex min-h-[40px] items-center justify-center rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(122,167,255,0.12)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(255,255,255,0.18)]"
          >
            {open ? "收起 2.5D 演示层" : "打开 2.5D 演示层"}
          </button>
          {links.slice(1).map((link) => (
            <Link
              key={link.href}
              href={link.href}
              className="inline-flex min-h-[40px] items-center justify-center rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.42)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(255,255,255,0.18)]"
            >
              {link.label}
            </Link>
          ))}
        </div>
      </div>

      <div className="mt-4 grid gap-3 md:grid-cols-4">
        <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(255,255,255,0.03)] p-3"><div className="text-xs text-[var(--muted)]">场景状态</div><div className="mt-2 text-sm font-semibold text-[var(--fg)]">{formatSceneState(visualization.scene.status)}</div></div>
        <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(255,255,255,0.03)] p-3"><div className="text-xs text-[var(--muted)]">系统节点</div><div className="mt-2 text-sm font-semibold text-[var(--fg)]">{visualization.systemNodes.length} 个已建模对象</div></div>
        <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(255,255,255,0.03)] p-3"><div className="text-xs text-[var(--muted)]">行为穿行</div><div className="mt-2 text-sm font-semibold text-[var(--fg)]">{visualization.behaviorPaths.length} 条真实路径</div></div>
        <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(255,255,255,0.03)] p-3"><div className="text-xs text-[var(--muted)]">风险暴露</div><div className="mt-2 text-sm font-semibold text-[var(--fg)]">{visualization.findings.length} 个风险脉冲点</div></div>
      </div>

      {open ? (
        <div className="relative isolate mt-5 grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
          <div className="relative z-0 min-h-[560px] overflow-hidden rounded-[20px] border border-[var(--border)] bg-[radial-gradient(circle_at_top,rgba(122,167,255,0.18),rgba(6,10,18,0.96)_62%)]">
            <Canvas camera={{ position: [0, 18, 24], fov: 42 }} shadows>
              <color attach="background" args={["#050913"]} />
              <ambientLight intensity={0.75} />
              <directionalLight position={[10, 18, 12]} intensity={1.35} castShadow />
              <pointLight position={[-14, 10, -8]} intensity={0.7} color={palette.neutral} />
              <mesh rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
                <planeGeometry args={[58, 42]} />
                <meshStandardMaterial color={palette.floor} metalness={0.15} roughness={0.96} />
              </mesh>
              {[-11.4, -3.8, 3.8, 11.4].map((z) => (
                <Line key={z} points={[[-20, 0.02, z], [20, 0.02, z]]} color={palette.lane} lineWidth={1.1} transparent opacity={0.45} />
              ))}
              {layouts.flatMap((layout) =>
                layout.relatedPaths.flatMap((path) => {
                  const nodes = path.nodeIds
                    .map((nodeId) => layouts.find((item) => item.node.nodeId === nodeId))
                    .filter((item): item is NodeLayout => Boolean(item));
                  return nodes.slice(0, -1).map((node, index) => (
                    <PathRibbon key={`${path.pathId}:${node.node.nodeId}:${index}`} from={node.position} to={nodes[index + 1].position} color={statusColor(path.coverageStatus)} />
                  ));
                }),
              )}
              {[
                { label: "环境", status: visualization.scene.environmentStatus },
                { label: "覆盖", status: visualization.scene.coverageStatus },
                { label: "场景", status: visualization.scene.status },
              ].map((item, index) => (
                <EnvironmentBeacon key={item.label} index={index} status={item.status} />
              ))}
              {layouts.map((layout) => (
                <SystemTower key={layout.node.nodeId} layout={layout} selected={layout.node.nodeId === selected?.node.nodeId} onSelect={setSelectedNodeId} />
              ))}
              <OrbitControls enablePan enableRotate enableZoom maxPolarAngle={Math.PI / 2.2} minDistance={12} maxDistance={42} target={[0, 1.4, 0]} />
            </Canvas>
          </div>

          <aside className="relative z-10 min-w-0 rounded-[20px] border border-[var(--border)] bg-[rgba(11,18,29,0.76)] p-4">
            <div className="text-xs uppercase tracking-[0.24em] text-[var(--muted)]">Showcase Focus</div>
            <div className="mt-3 text-base font-semibold text-[var(--fg)]">{selected?.node.label}</div>
            <div className="mt-1 text-sm text-[var(--muted)]">
              {formatNodeKind(selected?.node.kind ?? "other")}
              {selected?.node.domain ? ` · ${selected.node.domain}` : ""}
              {" · "}
              {formatSceneState(selected?.node.status ?? "unknown")}
            </div>
            <div className="mt-4 grid gap-2">
              <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(255,255,255,0.03)] p-3 text-sm text-[var(--fg)]">风险暴露 {selected?.node.riskCount ?? 0} 个</div>
              <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(255,255,255,0.03)] p-3 text-sm text-[var(--fg)]">关联路径 {(selected?.relatedPaths.length ?? 0)} 条</div>
              <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(255,255,255,0.03)] p-3 text-sm text-[var(--fg)]">证据入口 {selected?.node.evidenceRefIds.length ?? 0} 个</div>
            </div>
            <div className="mt-4 text-xs text-[var(--muted)]">该视图只回答“系统哪里被建模、风险沿哪条路径暴露、环境是否允许继续推进”，细节分析仍回到 2D 主视图和风险详情。</div>
            <div className="mt-4 grid gap-2">
              {selected?.relatedFindings.slice(0, 3).map((finding) => (
                <div key={finding.findingId} className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(255,92,122,0.08)] p-3">
                  <div className="text-sm font-medium text-[var(--fg)]">{finding.title}</div>
                  <div className="mt-1 text-xs text-[var(--muted)]">{finding.severity} · {finding.launchBlocking ? "上线阻断" : "可排期处理"}</div>
                </div>
              ))}
            </div>
            <div className="mt-4 flex flex-wrap gap-2">
              {links.map((link) => (
                <Link
                  key={link.href}
                  href={link.href}
                  className="inline-flex min-h-[40px] items-center justify-center rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.44)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(255,255,255,0.18)]"
                >
                  {link.label}
                </Link>
              ))}
            </div>
          </aside>
        </div>
      ) : null}
    </section>
  );
}
