import Link from "next/link";
import { ValueSurfacePanel } from "@/components/value-surface/ValueSurfacePanel";

export default async function ProjectWorkspacePage({
  params,
}: {
  params: Promise<{ projectId: string }>;
}) {
  const { projectId } = await params;
  const p = encodeURIComponent(projectId);

  return (
    <div className="grid gap-4">
      <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(16,24,38,0.55)] p-6 shadow-[var(--shadow-1)] backdrop-blur">
        <div className="text-xs text-[var(--muted)]">项目工作区</div>
        <h1 className="mt-2 text-xl font-semibold tracking-tight">{projectId}</h1>
        <p className="mt-2 text-sm text-[var(--muted)]">把核心决策信息放在一屏：上线建议、阻断、节省工时、下一步动作。</p>

        <div className="mt-5 flex flex-wrap gap-2">
          <Link
            href={`/projects/${p}/behavior-space`}
            className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(14,22,34,0.45)] px-3 py-2 text-sm hover:border-[rgba(89,243,194,0.30)]"
          >
            Behavior Space
          </Link>
          <Link
            href={`/projects/${p}/capabilities`}
            className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(14,22,34,0.45)] px-3 py-2 text-sm hover:border-[rgba(122,167,255,0.30)]"
          >
            能力中心
          </Link>
          <Link
            href={`/projects/${p}/risks`}
            className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(14,22,34,0.45)] px-3 py-2 text-sm hover:border-[rgba(255,92,122,0.30)]"
          >
            风险证据
          </Link>
          <Link
            href={`/projects/${p}/execution`}
            className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(14,22,34,0.45)] px-3 py-2 text-sm hover:border-[rgba(89,243,194,0.30)]"
          >
            执行
          </Link>
          <Link
            href={`/projects/${p}/reports/executive`}
            className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(14,22,34,0.45)] px-3 py-2 text-sm hover:border-[rgba(122,167,255,0.30)]"
          >
            报告
          </Link>
          <Link
            href={`/projects/${p}/roi`}
            className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(14,22,34,0.45)] px-3 py-2 text-sm hover:border-[rgba(89,243,194,0.30)]"
          >
            ROI/价值
          </Link>
        </div>
      </div>

      <ValueSurfacePanel pageId="project_workspace" />
    </div>
  );
}
