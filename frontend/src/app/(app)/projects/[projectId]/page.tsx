import Link from "next/link";
import { EnvironmentDiagnosticPanel } from "@/components/environment-diagnostics/EnvironmentDiagnosticPanel";
import { WorkspaceBlockerDrilldown } from "@/components/environment-diagnostics/WorkspaceBlockerDrilldown";
import { ValueSurfacePanel } from "@/components/value-surface/ValueSurfacePanel";
import { getEnvironmentDiagnosticGraph, type EnvironmentDiagnosticGraph, type GateStatus } from "@/features/environment-diagnostics";

const decisionTracks = [
  {
    label: "Environment Gate",
    title: "先回答 Runtime Allowed",
    description: "把正式 runtime、只读探针、写入探针和 P0/P1 验证的门禁状态放到首屏中央。",
    accent: "rgba(255,196,87,0.2)",
  },
  {
    label: "Blockers",
    title: "先收敛阻断而不是翻日志",
    description: "优先暴露阻断项数量、原因和 remediation action，避免团队直接陷入技术细节。",
    accent: "rgba(255,92,122,0.2)",
  },
  {
    label: "Next Moves",
    title: "先明确下一步推进动作",
    description: "把客户补料、探针模式和后续入口整理成一条连续的推进路径。",
    accent: "rgba(122,167,255,0.2)",
  },
] as const;

const quickEntryMeta = [
  {
    title: "环境诊断",
    description: "先确认客户环境门禁、阻断项和 remediation action，再决定是否推进 runtime。",
    hrefSuffix: "/environment",
    tone: "rgba(255,196,87,0.12)",
    badge: "第一关口",
  },
  {
    title: "Behavior Space",
    description: "用一张主视图理解系统建模、真实路径、风险暴露和证据绑定。",
    hrefSuffix: "/behavior-space",
    tone: "rgba(89,243,194,0.12)",
    badge: "推荐先看",
  },
  {
    title: "能力中心",
    description: "查看当前能力覆盖、缺失 source 和补齐动作。",
    hrefSuffix: "/capabilities",
    tone: "rgba(122,167,255,0.12)",
    badge: "覆盖透明",
  },
  {
    title: "风险证据",
    description: "聚焦上线阻断、业务影响、证据入口和回放。",
    hrefSuffix: "/risks",
    tone: "rgba(255,92,122,0.12)",
    badge: "阻断优先",
  },
  {
    title: "执行",
    description: "把发现的问题推到执行链路，跟踪修复与验证。",
    hrefSuffix: "/execution",
    tone: "rgba(89,243,194,0.08)",
    badge: "闭环推进",
  },
  {
    title: "领导层报告",
    description: "把技术发现转换成可汇报的上线建议、风险成本和行动建议。",
    hrefSuffix: "/reports/executive",
    tone: "rgba(122,167,255,0.08)",
    badge: "汇报出口",
  },
  {
    title: "ROI / 价值",
    description: "查看节省工时、收益空间和投入产出判断。",
    hrefSuffix: "/roi",
    tone: "rgba(89,243,194,0.08)",
    badge: "价值量化",
  },
] as const;

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

function formatCheckedAt(value?: string): string {
  if (!value) return "暂未记录";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function summarizeGraph(graph: EnvironmentDiagnosticGraph) {
  const runtimeGate = graph.gates.find((gate) => gate.gateId === "runtime_start_allowed") ?? graph.gates[0];
  const allowedGateCount = graph.gates.filter((gate) => gate.allowed).length;
  const blockedNodeCount = graph.nodes.filter((node) => node.status === "blocked").length;
  const warningNodeCount = graph.nodes.filter((node) => node.status === "warning").length;
  return {
    runtimeGate,
    allowedGateCount,
    blockedNodeCount,
    warningNodeCount,
  };
}

export default async function ProjectWorkspacePage({
  params,
}: {
  params: Promise<{ projectId: string }>;
}) {
  const { projectId } = await params;
  const p = encodeURIComponent(projectId);
  const environmentGraph = await getEnvironmentDiagnosticGraph(projectId);
  const graphSummary = summarizeGraph(environmentGraph);

  return (
    <div className="grid gap-4">
      <div className="overflow-hidden rounded-[var(--radius-md)] border border-[var(--border)] bg-[linear-gradient(180deg,rgba(16,24,38,0.82),rgba(10,16,27,0.92))] p-6 shadow-[var(--shadow-1)] backdrop-blur">
        <div className="grid gap-6 xl:grid-cols-[minmax(0,1.15fr)_minmax(0,0.85fr)]">
          <div className="min-w-0">
            <div className="text-xs uppercase tracking-[0.22em] text-[var(--muted)]">Environment Gate Overview</div>
            <h1 className="mt-3 text-2xl font-semibold tracking-tight text-[var(--fg)]">{projectId}</h1>
            <p className="mt-3 max-w-3xl text-sm leading-6 text-[var(--muted)]">
              首页首屏优先回答“当前环境能否放行 runtime、卡在哪一层、下一步先补什么”，避免团队先陷入原始数据和技术细节。
            </p>

            <div className="mt-5 flex flex-wrap items-center gap-2 text-xs text-[var(--muted)]">
              <span className={`rounded-full border px-3 py-1 ${statusClasses(environmentGraph.summary.status)}`}>{statusLabel(environmentGraph.summary.status)}</span>
              <span className="rounded-full border border-[var(--border)] bg-[rgba(255,255,255,0.05)] px-3 py-1">
                评分 {environmentGraph.summary.score ?? "—"}
              </span>
              <span className="rounded-full border border-[var(--border)] bg-[rgba(255,255,255,0.05)] px-3 py-1">
                安全模式 {environmentGraph.summary.safeExecutionMode}
              </span>
              <span className="rounded-full border border-[var(--border)] bg-[rgba(255,255,255,0.05)] px-3 py-1">
                最近检查 {formatCheckedAt(environmentGraph.summary.checkedAt)}
              </span>
            </div>

            <div className="mt-5 rounded-[var(--radius-md)] border border-[rgba(255,196,87,0.22)] bg-[linear-gradient(180deg,rgba(255,196,87,0.12),rgba(14,22,34,0.28))] p-5">
              <div className="text-xs uppercase tracking-[0.22em] text-[var(--muted)]">Runtime Allowed</div>
              <div className="mt-2 flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h2 className="text-xl font-semibold tracking-tight text-[var(--fg)]">{environmentGraph.summary.verdict}</h2>
                  <p className="mt-2 max-w-3xl text-sm leading-6 text-[var(--muted)]">{environmentGraph.summary.reason}</p>
                </div>
                <span className={`rounded-full border px-3 py-1 text-sm ${statusClasses(graphSummary.runtimeGate.status)}`}>
                  {graphSummary.runtimeGate.allowed ? "允许启动" : "暂不放行"}
                </span>
              </div>
              <div className="mt-4 grid gap-3 md:grid-cols-4">
                <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(8,13,21,0.52)] p-4">
                  <div className="text-xs text-[var(--muted)]">可执行门禁</div>
                  <div className="mt-2 text-2xl font-semibold text-[var(--fg)]">{graphSummary.allowedGateCount} / {environmentGraph.gates.length}</div>
                  <div className="mt-1 text-xs text-[var(--muted)]">当前允许推进的门禁数量</div>
                </div>
                <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(8,13,21,0.52)] p-4">
                  <div className="text-xs text-[var(--muted)]">阻断节点</div>
                  <div className="mt-2 text-2xl font-semibold text-[var(--fg)]">{graphSummary.blockedNodeCount}</div>
                  <div className="mt-1 text-xs text-[var(--muted)]">会直接卡住 runtime 的节点</div>
                </div>
                <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(8,13,21,0.52)] p-4">
                  <div className="text-xs text-[var(--muted)]">警告节点</div>
                  <div className="mt-2 text-2xl font-semibold text-[var(--fg)]">{graphSummary.warningNodeCount}</div>
                  <div className="mt-1 text-xs text-[var(--muted)]">建议先以只读或受限模式推进</div>
                </div>
                <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(8,13,21,0.52)] p-4">
                  <div className="text-xs text-[var(--muted)]">客户补料</div>
                  <div className="mt-2 text-2xl font-semibold text-[var(--fg)]">{environmentGraph.requiredCustomerInputs.length}</div>
                  <div className="mt-1 text-xs text-[var(--muted)]">仍需补齐的资料项</div>
                </div>
              </div>
            </div>

            <div className="mt-5 flex flex-wrap gap-2">
              <Link
                href="#workspace-blockers"
                className="rounded-[var(--radius-sm)] border border-[rgba(255,92,122,0.24)] bg-[rgba(255,92,122,0.10)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(255,92,122,0.42)]"
              >
                先看阻断下钻
              </Link>
              <Link
                href={`/projects/${p}/environment`}
                className="rounded-[var(--radius-sm)] border border-[rgba(255,196,87,0.28)] bg-[rgba(255,196,87,0.10)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(255,196,87,0.42)]"
              >
                打开环境拓扑
              </Link>
              <Link
                href={`/projects/${p}/behavior-space`}
                className="rounded-[var(--radius-sm)] border border-[rgba(89,243,194,0.28)] bg-[rgba(89,243,194,0.12)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(89,243,194,0.42)]"
              >
                从 Behavior Space 开始
              </Link>
              <Link
                href={`/projects/${p}/risks`}
                className="rounded-[var(--radius-sm)] border border-[rgba(255,92,122,0.22)] bg-[rgba(255,92,122,0.08)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(255,92,122,0.34)]"
              >
                查看上线阻断
              </Link>
              <Link
                href={`/projects/${p}/execution`}
                className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(14,22,34,0.45)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(255,255,255,0.18)]"
              >
                推进执行闭环
              </Link>
              <Link
                href={`/projects/${p}/roi`}
                className="rounded-[var(--radius-sm)] border border-[rgba(89,243,194,0.24)] bg-[rgba(89,243,194,0.10)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(89,243,194,0.42)]"
              >
                查看 ROI / 价值
              </Link>
            </div>
          </div>

          <div className="grid gap-3">
            <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(255,255,255,0.03)] p-5">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="text-xs uppercase tracking-[0.22em] text-[var(--muted)]">Gate Matrix</div>
                  <div className="mt-2 text-lg font-semibold text-[var(--fg)]">四类门禁一屏汇总</div>
                </div>
                <span className={`rounded-full border px-3 py-1 text-xs ${statusClasses(graphSummary.runtimeGate.status)}`}>{statusLabel(graphSummary.runtimeGate.status)}</span>
              </div>
              <div className="mt-4 grid gap-3 sm:grid-cols-2">
                {environmentGraph.gates.map((gate) => (
                  <div key={gate.gateId} className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(8,13,21,0.48)] p-4">
                    <div className="flex items-center justify-between gap-2">
                      <div className="text-sm font-semibold text-[var(--fg)]">{gate.label}</div>
                      <span className={`rounded-full border px-2 py-1 text-[11px] ${statusClasses(gate.status)}`}>{statusLabel(gate.status)}</span>
                    </div>
                    <div className="mt-2 text-xs text-[var(--muted)]">{gate.summary} · {gate.allowed ? "允许" : "未允许"}</div>
                    <div className="mt-2 text-sm leading-6 text-[var(--muted)]">{gate.reason}</div>
                  </div>
                ))}
              </div>
            </div>

            {decisionTracks.map((item) => (
              <div
                key={item.label}
                className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(255,255,255,0.03)] p-4"
                style={{ boxShadow: `inset 0 1px 0 ${item.accent}` }}
              >
                <div className="text-xs text-[var(--muted)]">{item.label}</div>
                <div className="mt-2 text-sm font-semibold text-[var(--fg)]">{item.title}</div>
                <div className="mt-2 text-sm leading-6 text-[var(--muted)]">{item.description}</div>
              </div>
            ))}
          </div>
        </div>

        <div className="mt-6 grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          <WorkspaceBlockerDrilldown graph={environmentGraph} />
          <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(8,13,21,0.48)] p-5">
            <div className="text-xs uppercase tracking-[0.22em] text-[var(--muted)]">下一步动作</div>
            <div className="mt-4 grid gap-3">
              {environmentGraph.suggestedActions.slice(0, 4).map((action, index) => (
                <div key={`workspace-action:${index}:${action}`} className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(255,255,255,0.03)] p-4 text-sm leading-6 text-[var(--muted)]">
                  {action}
                </div>
              ))}
              <div className="rounded-[var(--radius-sm)] border border-[rgba(122,167,255,0.24)] bg-[rgba(122,167,255,0.08)] p-4 text-sm text-[var(--muted)]">
                当前 Runtime Gate 建议：{graphSummary.runtimeGate.remediationAction ?? "继续按当前策略推进。"}
              </div>
            </div>
          </div>
        </div>
      </div>

      <EnvironmentDiagnosticPanel graph={environmentGraph} mode="summary" />

      <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(16,24,38,0.55)] p-6 shadow-[var(--shadow-1)] backdrop-blur">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-xs text-[var(--muted)]">快捷入口</div>
            <h2 className="mt-2 text-lg font-semibold tracking-tight text-[var(--fg)]">按决策任务进入，不必先记页面名字</h2>
            <p className="mt-2 max-w-3xl text-sm text-[var(--muted)]">首屏优先暴露高频入口，按“理解现状 → 看阻断 → 推动作 → 做汇报”的顺序组织。</p>
          </div>
          <Link
            href={`/projects/${p}/reports/executive`}
            className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(122,167,255,0.08)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(122,167,255,0.30)]"
          >
            打开领导层报告
          </Link>
        </div>

        <div className="mt-5 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {quickEntryMeta.map((entry) => (
            <Link
              key={entry.hrefSuffix}
              href={`/projects/${p}${entry.hrefSuffix}`}
              className="rounded-[var(--radius-sm)] border border-[var(--border)] p-4 transition hover:border-[rgba(255,255,255,0.18)]"
              style={{ background: `linear-gradient(180deg, ${entry.tone}, rgba(14,22,34,0.4))` }}
            >
              <div className="flex items-center justify-between gap-3">
                <div className="text-sm font-semibold text-[var(--fg)]">{entry.title}</div>
                <span className="rounded-full border border-[var(--border)] bg-[rgba(255,255,255,0.05)] px-2 py-1 text-[11px] text-[var(--muted)]">
                  {entry.badge}
                </span>
              </div>
              <div className="mt-2 text-sm leading-6 text-[var(--muted)]">{entry.description}</div>
            </Link>
          ))}
        </div>
      </div>

      <ValueSurfacePanel pageId="project_workspace" />
    </div>
  );
}
