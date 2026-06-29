import Link from "next/link";
import { EnvironmentDiagnosisWorkspace } from "@/components/environment-diagnostics/EnvironmentDiagnosisWorkspace";
import { getEnvironmentDiagnosticGraph } from "@/features/environment-diagnostics";

export default async function EnvironmentDiagnosisPage({ params }: { params: Promise<{ projectId: string }> }) {
  const { projectId } = await params;
  const graph = await getEnvironmentDiagnosticGraph(projectId);

  return (
    <div className="grid gap-4">
      <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[linear-gradient(180deg,rgba(16,24,38,0.74),rgba(10,16,27,0.96))] p-6 shadow-[var(--shadow-1)] backdrop-blur">
        <div className="grid gap-5 xl:grid-cols-[minmax(0,1.1fr)_minmax(320px,0.9fr)]">
          <div>
            <div className="text-xs uppercase tracking-[0.22em] text-[var(--muted)]">环境诊断</div>
            <h1 className="mt-2 text-xl font-semibold tracking-tight text-[var(--fg)]">客户环境与运行门禁</h1>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-[var(--muted)]">
              页面继续统一消费 `EnvironmentDiagnosticGraph`，但主舞台升级为 2.5D 环境拓扑，让团队能直接看到 Runtime Allowed 之前卡在哪一层。
            </p>
            <div className="mt-4 flex flex-wrap gap-2">
              <Link
                href="#environment-quick-actions"
                className="rounded-[var(--radius-sm)] border border-[rgba(255,92,122,0.24)] bg-[rgba(255,92,122,0.10)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(255,92,122,0.42)]"
              >
                先看阻断下钻
              </Link>
              <Link
                href="#environment-topology"
                className="rounded-[var(--radius-sm)] border border-[rgba(122,167,255,0.24)] bg-[rgba(122,167,255,0.10)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(122,167,255,0.42)]"
              >
                打开环境拓扑
              </Link>
              <Link
                href="#environment-blockers"
                className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(14,22,34,0.45)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(255,255,255,0.18)]"
              >
                跳到阻断列表
              </Link>
            </div>
          </div>
          <div className="grid gap-3 sm:grid-cols-3 xl:grid-cols-1">
            <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(255,255,255,0.04)] p-4">
              <div className="text-xs text-[var(--muted)]">拓扑节点</div>
              <div className="mt-2 text-2xl font-semibold text-[var(--fg)]">{graph.nodes.length}</div>
              <div className="mt-1 text-xs text-[var(--muted)]">至少 10 个环境节点已接入同一拓扑</div>
            </div>
            <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(255,255,255,0.04)] p-4">
              <div className="text-xs text-[var(--muted)]">状态类型</div>
              <div className="mt-2 text-2xl font-semibold text-[var(--fg)]">5</div>
              <div className="mt-1 text-xs text-[var(--muted)]">passed / warning / blocked / checking / unknown</div>
            </div>
            <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(255,255,255,0.04)] p-4">
              <div className="text-xs text-[var(--muted)]">Runtime Verdict</div>
              <div className="mt-2 text-lg font-semibold text-[var(--fg)]">{graph.summary.verdict}</div>
              <div className="mt-1 text-xs text-[var(--muted)]">{graph.summary.safeExecutionMode}</div>
            </div>
          </div>
        </div>
      </div>

      <EnvironmentDiagnosisWorkspace graph={graph} />
    </div>
  );
}
