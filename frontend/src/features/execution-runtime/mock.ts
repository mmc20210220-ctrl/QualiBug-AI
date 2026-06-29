import type { RuntimeExecutionTheater, RuntimeStageEdge, RuntimeStageNode } from "./model";

const STAGE_NODES: readonly Omit<RuntimeStageNode, "status" | "headline" | "detail" | "badges" | "eventIds" | "emphasis">[] = [
  {
    nodeId: "stage-entry",
    label: "Run Lifecycle",
    kind: "entry",
    position: { x: 40, y: 150 },
  },
  {
    nodeId: "stage-probe",
    label: "Probe Path",
    kind: "probe",
    position: { x: 320, y: 150 },
  },
  {
    nodeId: "stage-traffic",
    label: "Request / Response",
    kind: "traffic",
    position: { x: 610, y: 150 },
  },
  {
    nodeId: "stage-snapshot",
    label: "Snapshot Diff",
    kind: "snapshot",
    position: { x: 900, y: 150 },
  },
  {
    nodeId: "stage-finding",
    label: "Finding Drop",
    kind: "finding",
    position: { x: 1180, y: 70 },
  },
  {
    nodeId: "stage-summary",
    label: "Summary Card",
    kind: "summary",
    position: { x: 1180, y: 250 },
  },
];

const STAGE_EDGES: readonly Omit<RuntimeStageEdge, "status" | "eventIds" | "emphasis">[] = [
  {
    edgeId: "edge-entry-probe",
    sourceNodeId: "stage-entry",
    targetNodeId: "stage-probe",
    label: "启动与派发",
  },
  {
    edgeId: "edge-probe-traffic",
    sourceNodeId: "stage-probe",
    targetNodeId: "stage-traffic",
    label: "注入真实路径",
  },
  {
    edgeId: "edge-traffic-snapshot",
    sourceNodeId: "stage-traffic",
    targetNodeId: "stage-snapshot",
    label: "捕获前后态",
  },
  {
    edgeId: "edge-snapshot-finding",
    sourceNodeId: "stage-snapshot",
    targetNodeId: "stage-finding",
    label: "产出 finding / blocker",
  },
  {
    edgeId: "edge-finding-summary",
    sourceNodeId: "stage-finding",
    targetNodeId: "stage-summary",
    label: "归档执行结论",
  },
];

export function buildRuntimeExecutionMockTheater(projectId: string): RuntimeExecutionTheater {
  return {
    projectId,
    source: "mock",
    runId: null,
    runStatus: "idle",
    metrics: {
      progress: null,
      probeTotal: null,
      probeCompleted: null,
      probeFailed: null,
      riskFound: null,
    },
    nodes: STAGE_NODES.map((node) => ({
      ...node,
      status: "pending",
      headline: "等待运行",
      detail: "尚未接收到 runtime 事件。",
      badges: [],
      eventIds: [],
      emphasis: false,
    })),
    edges: STAGE_EDGES.map((edge) => ({
      ...edge,
      status: "pending",
      eventIds: [],
      emphasis: false,
    })),
    events: [],
    summaryCard: null,
  };
}
