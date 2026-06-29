import Link from "next/link";
import { ValueSurfacePanel } from "@/components/value-surface/ValueSurfacePanel";

const valueNarrative = [
  {
    label: "上线决策",
    title: "先回答是否可上线",
    description: "把上线建议、阻断项和决策依据放在首屏，不需要先翻技术日志。",
    accent: "rgba(89,243,194,0.18)",
  },
  {
    label: "风险成本",
    title: "先看风险会损失什么",
    description: "把高优先级风险、回放入口和业务影响串成可执行的修复路径。",
    accent: "rgba(255,92,122,0.18)",
  },
  {
    label: "推进动作",
    title: "先知道下一步该做什么",
    description: "从行为建模、执行链路到领导层汇报，给出一条连续的推进路线。",
    accent: "rgba(122,167,255,0.18)",
  },
] as const;

const workspaceJourney = [
  { step: "01", title: "理解真实行为路径", description: "从 Behavior Space 快速识别建模完整度、覆盖状态和风险暴露点。" },
  { step: "02", title: "定位上线阻断与证据", description: "进入风险证据页查看阻断项、影响范围和复现回放入口。" },
  { step: "03", title: "推动执行与修复闭环", description: "在执行页跟进任务推进，在 ROI/报告页完成对内外沟通。" },
] as const;

const quickEntryMeta = [
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

export default async function ProjectWorkspacePage({
  params,
}: {
  params: Promise<{ projectId: string }>;
}) {
  const { projectId } = await params;
  const p = encodeURIComponent(projectId);

  return (
    <div className="grid gap-4">
      <div className="overflow-hidden rounded-[var(--radius-md)] border border-[var(--border)] bg-[linear-gradient(180deg,rgba(16,24,38,0.82),rgba(10,16,27,0.92))] p-6 shadow-[var(--shadow-1)] backdrop-blur">
        <div className="grid gap-6 xl:grid-cols-[minmax(0,1.2fr)_minmax(0,0.8fr)]">
          <div className="min-w-0">
            <div className="text-xs uppercase tracking-[0.22em] text-[var(--muted)]">项目工作区</div>
            <h1 className="mt-3 text-2xl font-semibold tracking-tight text-[var(--fg)]">{projectId}</h1>
            <p className="mt-3 max-w-3xl text-sm leading-6 text-[var(--muted)]">
              把“能不能上线、风险会造成什么损失、下一步谁该推进什么动作”压缩到一屏里，避免团队先陷入原始数据和技术细节。
            </p>

            <div className="mt-5 flex flex-wrap gap-2">
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
            </div>
          </div>

          <div className="grid gap-3 sm:grid-cols-3 xl:grid-cols-1">
            {valueNarrative.map((item) => (
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

        <div className="mt-6 grid gap-3 lg:grid-cols-3">
          {workspaceJourney.map((item) => (
            <div key={item.step} className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(8,13,21,0.48)] p-4">
              <div className="text-xs uppercase tracking-[0.2em] text-[var(--muted)]">{item.step}</div>
              <div className="mt-2 text-sm font-semibold text-[var(--fg)]">{item.title}</div>
              <div className="mt-2 text-sm leading-6 text-[var(--muted)]">{item.description}</div>
            </div>
          ))}
        </div>
      </div>

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
