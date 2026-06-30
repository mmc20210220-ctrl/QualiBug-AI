import Link from "next/link";

const proofChain = [
  {
    step: "01",
    title: "客户环境先验诊断",
    caption: "不是拿到接口就盲跑，而是先确认网络、TLS、鉴权、账号矩阵、测试数据、快照和 cleanup。",
    metric: "4 个门禁",
    status: "先挡风险",
  },
  {
    step: "02",
    title: "系统行为空间建模",
    caption: "把角色、接口、业务对象、状态机、异常路径和权限边界放到同一张行为地图里。",
    metric: "75% 覆盖",
    status: "看清边界",
  },
  {
    step: "03",
    title: "Runtime 探针真实执行",
    caption: "用只读、写入、mutation、auth boundary 和业务流探针验证真实路径。",
    metric: "SSE 实时",
    status: "正在执行",
  },
  {
    step: "04",
    title: "Bug 证据回放",
    caption: "每个问题绑定 before / action / after / finding / reproduction pack，减少误报争议。",
    metric: "可复现",
    status: "证据闭环",
  },
  {
    step: "05",
    title: "客户交付与 ROI",
    caption: "把技术结果转换成上线建议、风险成本、修复动作和可审计交付包。",
    metric: "¥180k+",
    status: "能汇报",
  },
] as const;

const launchGates = [
  { label: "Runtime Start", value: "BLOCKED", tone: "rose", reason: "仍有 2 个 P0 环境阻断，暂不建议启动写入探针。" },
  { label: "Readonly Probe", value: "ALLOWED", tone: "emerald", reason: "可先用只读模式验证权限边界、接口返回和状态一致性。" },
  { label: "Write Probe", value: "CONTROLLED", tone: "amber", reason: "需要客户确认测试数据工厂与 cleanup 账号。" },
  { label: "P0/P1 Validation", value: "READY AFTER FIX", tone: "cyan", reason: "阻断关闭后进入高价值回归验证。" },
] as const;

const businessOutcomes = [
  { label: "上线阻断风险", value: "2", help: "支付与退款链路仍有真实路径阻断" },
  { label: "预计风险成本", value: "180k-420k CNY", help: "按一次上线事故、人工定位和回滚成本估算" },
  { label: "已节省定位", value: "36 h", help: "把日志翻查压缩为证据路径回放" },
  { label: "客户补料项", value: "3", help: "测试账号、sandbox 数据、cleanup 权限" },
] as const;

const demoMoves = [
  "先进入环境诊断页，关闭 DNS/TLS/Auth/Test Data 阻断",
  "再进入 Behavior Space，确认核心业务对象和状态机覆盖面",
  "最后运行只读探针，生成第一份可回放风险证据",
] as const;

function GatePill({ tone, children }: { tone: "rose" | "emerald" | "amber" | "cyan"; children: React.ReactNode }) {
  const map = {
    rose: "border-[rgba(255,92,122,0.36)] bg-[rgba(255,92,122,0.14)] text-[rgb(255,201,211)]",
    emerald: "border-[rgba(89,243,194,0.36)] bg-[rgba(89,243,194,0.12)] text-[rgb(200,255,239)]",
    amber: "border-[rgba(255,196,87,0.36)] bg-[rgba(255,196,87,0.12)] text-[rgb(255,232,180)]",
    cyan: "border-[rgba(122,167,255,0.36)] bg-[rgba(122,167,255,0.12)] text-[rgb(210,225,255)]",
  } as const;
  return <span className={`rounded-full border px-3 py-1 text-xs font-semibold tracking-wide ${map[tone]}`}>{children}</span>;
}

function ValueNode({ item, index }: { item: (typeof proofChain)[number]; index: number }) {
  const y = index % 2 === 0 ? "translate-y-0" : "translate-y-8";
  return (
    <div className={`relative min-w-[220px] flex-1 ${y}`}>
      <div className="absolute -left-3 top-7 hidden h-px w-6 bg-[rgba(89,243,194,0.28)] lg:block" />
      <div className="h-full rounded-[28px] border border-[rgba(255,255,255,0.10)] bg-[linear-gradient(180deg,rgba(18,31,48,0.92),rgba(8,13,22,0.94))] p-4 shadow-[0_24px_60px_rgba(0,0,0,0.25)] transition-transform hover:-translate-y-1">
        <div className="flex items-center justify-between gap-3">
          <span className="rounded-full border border-[rgba(89,243,194,0.22)] bg-[rgba(89,243,194,0.10)] px-2 py-1 text-xs text-[rgb(201,255,239)]">{item.step}</span>
          <span className="text-xs text-[var(--muted)]">{item.status}</span>
        </div>
        <h3 className="mt-4 text-base font-semibold text-[var(--fg)]">{item.title}</h3>
        <p className="mt-3 text-sm leading-6 text-[var(--muted)]">{item.caption}</p>
        <div className="mt-4 rounded-2xl border border-[rgba(255,255,255,0.08)] bg-[rgba(255,255,255,0.04)] px-3 py-2 text-sm font-semibold text-[var(--fg)]">{item.metric}</div>
      </div>
    </div>
  );
}

export default async function CommercialDemoPage({
  params,
}: {
  params: Promise<{ projectId: string }>;
}) {
  const { projectId } = await params;
  const p = encodeURIComponent(projectId);

  return (
    <div className="grid gap-5">
      <section className="overflow-hidden rounded-[32px] border border-[rgba(255,255,255,0.10)] bg-[radial-gradient(circle_at_18%_10%,rgba(89,243,194,0.18),transparent_34%),radial-gradient(circle_at_82%_8%,rgba(122,167,255,0.18),transparent_32%),linear-gradient(180deg,rgba(18,27,43,0.96),rgba(7,11,18,0.96))] p-6 shadow-[0_30px_100px_rgba(0,0,0,0.38)]">
        <div className="grid gap-6 xl:grid-cols-[minmax(0,1.1fr)_420px]">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <GatePill tone="cyan">QualiBug Commercial Demo</GatePill>
              <GatePill tone="rose">上线前门禁优先</GatePill>
              <span className="rounded-full border border-[rgba(255,255,255,0.10)] bg-[rgba(255,255,255,0.05)] px-3 py-1 text-xs text-[var(--muted)]">Project: {projectId}</span>
            </div>
            <h1 className="mt-5 max-w-4xl text-3xl font-semibold tracking-tight text-[var(--fg)] md:text-5xl">
              把“测试工具页面”升级成客户能看懂的系统行为数字孪生驾驶舱
            </h1>
            <p className="mt-5 max-w-3xl text-sm leading-7 text-[var(--muted)] md:text-base">
              这个页面不再展示孤立卡片，而是直接表达 QualiBug 的商业价值：先诊断客户环境，再建模系统行为空间，再执行 runtime 探针，最后生成可复现、可审计、可汇报的证据链。
            </p>
            <div className="mt-6 grid gap-3 md:grid-cols-4">
              {businessOutcomes.map((outcome) => (
                <div key={outcome.label} className="rounded-3xl border border-[rgba(255,255,255,0.09)] bg-[rgba(9,14,23,0.62)] p-4">
                  <div className="text-xs text-[var(--muted)]">{outcome.label}</div>
                  <div className="mt-2 text-xl font-semibold text-[var(--fg)]">{outcome.value}</div>
                  <p className="mt-2 text-xs leading-5 text-[var(--muted)]">{outcome.help}</p>
                </div>
              ))}
            </div>
            <div className="mt-6 flex flex-wrap gap-2">
              <Link href={`/projects/${p}/environment`} className="rounded-2xl border border-[rgba(255,196,87,0.32)] bg-[rgba(255,196,87,0.14)] px-4 py-3 text-sm font-semibold text-[var(--fg)] hover:border-[rgba(255,196,87,0.56)]">
                先看客户环境门禁
              </Link>
              <Link href={`/projects/${p}/behavior-space`} className="rounded-2xl border border-[rgba(89,243,194,0.32)] bg-[rgba(89,243,194,0.12)] px-4 py-3 text-sm font-semibold text-[var(--fg)] hover:border-[rgba(89,243,194,0.56)]">
                打开行为空间地图
              </Link>
              <Link href={`/projects/${p}/reports/executive`} className="rounded-2xl border border-[rgba(122,167,255,0.28)] bg-[rgba(122,167,255,0.12)] px-4 py-3 text-sm font-semibold text-[var(--fg)] hover:border-[rgba(122,167,255,0.50)]">
                生成领导层报告
              </Link>
            </div>
          </div>

          <div className="rounded-[28px] border border-[rgba(255,255,255,0.10)] bg-[rgba(8,13,21,0.70)] p-5">
            <div className="text-xs uppercase tracking-[0.22em] text-[var(--muted)]">Launch Decision</div>
            <div className="mt-3 text-2xl font-semibold text-[var(--fg)]">暂不建议上线</div>
            <p className="mt-3 text-sm leading-6 text-[var(--muted)]">不是给客户一堆风险列表，而是给出可解释的上线门禁：当前可先推进只读探针，但写入探针与 P0/P1 验证需要关闭环境阻断后再执行。</p>
            <div className="mt-5 grid gap-3">
              {launchGates.map((gate) => (
                <div key={gate.label} className="rounded-2xl border border-[rgba(255,255,255,0.08)] bg-[rgba(255,255,255,0.04)] p-4">
                  <div className="flex items-center justify-between gap-3">
                    <div className="text-sm font-semibold text-[var(--fg)]">{gate.label}</div>
                    <GatePill tone={gate.tone}>{gate.value}</GatePill>
                  </div>
                  <p className="mt-2 text-xs leading-5 text-[var(--muted)]">{gate.reason}</p>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      <section className="rounded-[32px] border border-[rgba(255,255,255,0.10)] bg-[linear-gradient(180deg,rgba(15,23,37,0.92),rgba(8,13,21,0.95))] p-6 shadow-[0_24px_90px_rgba(0,0,0,0.30)]">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <div className="text-xs uppercase tracking-[0.22em] text-[var(--muted)]">Evidence Digital Twin</div>
            <h2 className="mt-2 text-2xl font-semibold text-[var(--fg)]">价值证明链：从环境到交付，不让客户只看到一个页面</h2>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-[var(--muted)]">客户要买的不是 UI，而是“我能不能安全上线、问题是否真实可复现、证据能不能交给研发和领导”。</p>
          </div>
          <GatePill tone="emerald">Demo Storyline Ready</GatePill>
        </div>

        <div className="mt-8 overflow-x-auto pb-10 pt-2">
          <div className="flex min-w-[1120px] gap-4 lg:gap-5">
            {proofChain.map((item, index) => (
              <ValueNode item={item} index={index} key={item.step} />
            ))}
          </div>
        </div>
      </section>

      <section className="grid gap-5 xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
        <div className="rounded-[32px] border border-[rgba(255,92,122,0.18)] bg-[linear-gradient(180deg,rgba(255,92,122,0.10),rgba(8,13,21,0.88))] p-6">
          <div className="text-xs uppercase tracking-[0.22em] text-[var(--muted)]">Why QualiBug</div>
          <h2 className="mt-3 text-2xl font-semibold text-[var(--fg)]">产品力必须体现在“决策能力”上</h2>
          <p className="mt-3 text-sm leading-7 text-[var(--muted)]">
            继续堆页面不会产生产品力。QualiBug 的前端要把后端行为建模、runtime evidence、环境诊断、复现包和交付 manifest 统一成一条决策链。
          </p>
          <div className="mt-5 space-y-3">
            {["不是展示 bug 数量，而是展示上线阻断原因", "不是展示日志，而是展示证据路径", "不是展示技术能力点，而是展示客户下一步动作", "不是承诺 99%，而是展示真实覆盖、真实证据、真实缺口"].map((item) => (
              <div key={item} className="rounded-2xl border border-[rgba(255,255,255,0.08)] bg-[rgba(255,255,255,0.04)] px-4 py-3 text-sm text-[var(--fg)]">
                {item}
              </div>
            ))}
          </div>
        </div>

        <div className="rounded-[32px] border border-[rgba(89,243,194,0.18)] bg-[linear-gradient(180deg,rgba(89,243,194,0.09),rgba(8,13,21,0.88))] p-6">
          <div className="text-xs uppercase tracking-[0.22em] text-[var(--muted)]">Next Three Moves</div>
          <h2 className="mt-3 text-2xl font-semibold text-[var(--fg)]">下一步只推进三件事</h2>
          <div className="mt-5 grid gap-3">
            {demoMoves.map((move, index) => (
              <div key={move} className="flex gap-3 rounded-2xl border border-[rgba(255,255,255,0.08)] bg-[rgba(255,255,255,0.04)] p-4">
                <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-[rgba(89,243,194,0.28)] bg-[rgba(89,243,194,0.10)] text-sm font-semibold text-[rgb(201,255,239)]">{index + 1}</div>
                <div>
                  <div className="text-sm font-semibold text-[var(--fg)]">{move}</div>
                  <p className="mt-1 text-xs leading-5 text-[var(--muted)]">每一步都必须产出可点击证据入口，而不是只在页面上显示一段文案。</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>
    </div>
  );
}
