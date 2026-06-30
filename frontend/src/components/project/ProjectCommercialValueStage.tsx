import Link from "next/link";
import type { EnvironmentDiagnosticGraph, GateStatus } from "@/features/environment-diagnostics";

function statusTone(status: GateStatus) {
  if (status === "passed") return "border-[rgba(89,243,194,0.34)] bg-[rgba(89,243,194,0.12)] text-[var(--fg)]";
  if (status === "warning") return "border-[rgba(255,196,87,0.36)] bg-[rgba(255,196,87,0.12)] text-[var(--fg)]";
  if (status === "blocked") return "border-[rgba(255,92,122,0.38)] bg-[rgba(255,92,122,0.13)] text-[var(--fg)]";
  if (status === "checking") return "border-[rgba(122,167,255,0.36)] bg-[rgba(122,167,255,0.12)] text-[var(--fg)]";
  return "border-[var(--border)] bg-[rgba(255,255,255,0.04)] text-[var(--muted)]";
}

function statusLabel(status: GateStatus) {
  if (status === "passed") return "已通过";
  if (status === "warning") return "受控推进";
  if (status === "blocked") return "阻断";
  if (status === "checking") return "检测中";
  return "未配置";
}

function asPercent(value: number | null | undefined, fallback = 0) {
  const safe = typeof value === "number" && Number.isFinite(value) ? value : fallback;
  return Math.max(0, Math.min(100, safe));
}

function summarize(graph: EnvironmentDiagnosticGraph) {
  const blockedNodes = graph.nodes.filter((node) => node.status === "blocked");
  const warningNodes = graph.nodes.filter((node) => node.status === "warning");
  const passedNodes = graph.nodes.filter((node) => node.status === "passed");
  const runtimeGate = graph.gates.find((gate) => gate.gateId === "runtime_start_allowed") ?? graph.gates[0];
  const writeGate = graph.gates.find((gate) => gate.gateId === "write_probe_allowed");
  const readonlyGate = graph.gates.find((gate) => gate.gateId === "readonly_probe_allowed");
  const allowedGateCount = graph.gates.filter((gate) => gate.allowed).length;
  const readinessScore = asPercent(graph.summary.score, passedNodes.length ? Math.round((passedNodes.length / graph.nodes.length) * 100) : 0);
  const coverageScore = Math.max(12, Math.min(92, 48 + allowedGateCount * 10 - blockedNodes.length * 8));
  const evidenceScore = Math.max(18, Math.min(96, 40 + passedNodes.length * 5 + warningNodes.length * 2 - blockedNodes.length * 7));
  const commercialRisk = runtimeGate?.allowed ? "可进入受控验证" : "暂不建议上线";

  return {
    blockedNodes,
    warningNodes,
    passedNodes,
    runtimeGate,
    writeGate,
    readonlyGate,
    allowedGateCount,
    readinessScore,
    coverageScore,
    evidenceScore,
    commercialRisk,
  };
}

const valueChain = [
  {
    title: "环境先验",
    eyebrow: "Gate 01",
    body: "先判断客户环境是否允许跑，不把环境问题误报成业务 Bug。",
  },
  {
    title: "行为空间建模",
    eyebrow: "Gate 02",
    body: "把角色、接口、业务对象和状态机路径组织成可解释的覆盖面。",
  },
  {
    title: "Runtime 探针",
    eyebrow: "Gate 03",
    body: "在真实链路里执行只读、写入和越权边界探测。",
  },
  {
    title: "证据回放",
    eyebrow: "Gate 04",
    body: "每个阻断风险都要能回到 before/after snapshot 和复现步骤。",
  },
  {
    title: "交付审计",
    eyebrow: "Gate 05",
    body: "最终形成可脱敏、可验证 hash、可给研发/管理层看的交付包。",
  },
] as const;

const demoProofPoints = [
  "把上线判断从经验争论变成证据门禁",
  "先定位环境阻断，减少无效调试和现场扯皮",
  "把 Bug 发现过程包装成客户能看懂的证据链",
  "让管理层看到风险成本、下一步动作和交付状态",
] as const;

export function ProjectCommercialValueStage({
  projectId,
  graph,
}: {
  projectId: string;
  graph: EnvironmentDiagnosticGraph;
}) {
  const p = encodeURIComponent(projectId);
  const summary = summarize(graph);
  const topBlockers = graph.blockers.slice(0, 3);
  const topInputs = graph.requiredCustomerInputs.slice(0, 3);
  const topNodes = [...summary.blockedNodes, ...summary.warningNodes, ...summary.passedNodes].slice(0, 8);

  return (
    <section className="grid gap-5">
      <div className="relative overflow-hidden rounded-[28px] border border-[rgba(89,243,194,0.18)] bg-[radial-gradient(circle_at_18%_0%,rgba(89,243,194,0.20),transparent_30%),radial-gradient(circle_at_92%_12%,rgba(122,167,255,0.18),transparent_34%),linear-gradient(135deg,rgba(12,19,31,0.96),rgba(8,12,20,0.98))] p-6 shadow-[0_24px_80px_rgba(0,0,0,0.34)]">
        <div className="absolute right-[-120px] top-[-140px] h-[320px] w-[320px] rounded-full border border-[rgba(89,243,194,0.18)] bg-[rgba(89,243,194,0.06)] blur-2xl" />
        <div className="relative grid gap-6 xl:grid-cols-[minmax(0,1.05fr)_minmax(360px,0.95fr)]">
          <div className="min-w-0">
            <div className="inline-flex rounded-full border border-[rgba(89,243,194,0.24)] bg-[rgba(89,243,194,0.10)] px-3 py-1 text-xs font-medium tracking-[0.18em] text-[var(--fg)]">
              QUALIBUG SYSTEM BEHAVIOR DIGITAL TWIN
            </div>
            <h1 className="mt-5 max-w-4xl text-3xl font-semibold tracking-tight text-[var(--fg)] md:text-5xl">
              把客户系统从“能不能测”变成“能不能上线”的证据驾驶舱
            </h1>
            <p className="mt-5 max-w-3xl text-base leading-8 text-[var(--muted)]">
              QualiBug 不是普通测试后台。它先诊断客户环境，再映射系统行为空间，然后用 runtime 探针暴露真实业务路径风险，最后输出可复现、可审计、可交付的证据包。
            </p>

            <div className="mt-6 grid gap-3 sm:grid-cols-3">
              <div className="rounded-[18px] border border-[rgba(255,92,122,0.24)] bg-[rgba(255,92,122,0.10)] p-4">
                <div className="text-xs text-[var(--muted)]">上线判断</div>
                <div className="mt-2 text-2xl font-semibold text-[var(--fg)]">{summary.commercialRisk}</div>
                <div className="mt-2 text-xs leading-5 text-[var(--muted)]">{summary.runtimeGate?.reason ?? graph.summary.reason}</div>
              </div>
              <div className="rounded-[18px] border border-[rgba(255,196,87,0.24)] bg-[rgba(255,196,87,0.10)] p-4">
                <div className="text-xs text-[var(--muted)]">环境就绪度</div>
                <div className="mt-2 text-3xl font-semibold text-[var(--fg)]">{summary.readinessScore}%</div>
                <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-[rgba(255,255,255,0.08)]">
                  <div className="h-full rounded-full bg-[rgba(255,196,87,0.75)]" style={{ width: `${summary.readinessScore}%` }} />
                </div>
              </div>
              <div className="rounded-[18px] border border-[rgba(89,243,194,0.24)] bg-[rgba(89,243,194,0.10)] p-4">
                <div className="text-xs text-[var(--muted)]">可推进门禁</div>
                <div className="mt-2 text-3xl font-semibold text-[var(--fg)]">{summary.allowedGateCount}/{graph.gates.length}</div>
                <div className="mt-2 text-xs leading-5 text-[var(--muted)]">只把可控范围放行，避免把客户环境问题误判为产品问题。</div>
              </div>
            </div>

            <div className="mt-6 flex flex-wrap gap-2">
              <Link href={`/projects/${p}/environment`} className="rounded-[var(--radius-sm)] border border-[rgba(255,196,87,0.35)] bg-[rgba(255,196,87,0.12)] px-4 py-2 text-sm font-medium text-[var(--fg)] hover:border-[rgba(255,196,87,0.55)]">
                先看环境诊断
              </Link>
              <Link href={`/projects/${p}/behavior-space`} className="rounded-[var(--radius-sm)] border border-[rgba(89,243,194,0.32)] bg-[rgba(89,243,194,0.12)] px-4 py-2 text-sm font-medium text-[var(--fg)] hover:border-[rgba(89,243,194,0.55)]">
                查看行为空间
              </Link>
              <Link href={`/projects/${p}/risks`} className="rounded-[var(--radius-sm)] border border-[rgba(255,92,122,0.32)] bg-[rgba(255,92,122,0.10)] px-4 py-2 text-sm font-medium text-[var(--fg)] hover:border-[rgba(255,92,122,0.50)]">
                下钻阻断风险
              </Link>
              <Link href={`/projects/${p}/reports/executive`} className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(255,255,255,0.05)] px-4 py-2 text-sm font-medium text-[var(--fg)] hover:border-[rgba(255,255,255,0.20)]">
                生成领导层报告
              </Link>
            </div>
          </div>

          <div className="relative min-h-[420px] overflow-hidden rounded-[24px] border border-[rgba(122,167,255,0.18)] bg-[linear-gradient(180deg,rgba(14,22,34,0.82),rgba(7,11,19,0.92))] p-5">
            <div className="flex items-center justify-between gap-3">
              <div>
                <div className="text-xs uppercase tracking-[0.22em] text-[var(--muted)]">2.5D Value Map</div>
                <div className="mt-2 text-lg font-semibold text-[var(--fg)]">从环境到证据的成功路径</div>
              </div>
              <span className={`rounded-full border px-3 py-1 text-xs ${statusTone(graph.summary.status)}`}>{statusLabel(graph.summary.status)}</span>
            </div>

            <div className="relative mt-8 h-[310px] [perspective:1000px]">
              <div className="absolute inset-x-5 bottom-3 top-8 rounded-[30px] border border-[rgba(255,255,255,0.08)] bg-[linear-gradient(135deg,rgba(89,243,194,0.08),rgba(122,167,255,0.05))] shadow-[0_30px_90px_rgba(0,0,0,0.28)] [transform:rotateX(58deg)_rotateZ(-5deg)]" />
              {valueChain.map((item, index) => {
                const left = 7 + index * 18;
                const top = index % 2 === 0 ? 92 : 145;
                const status: GateStatus = index === 0 ? graph.summary.status : index === 2 ? summary.runtimeGate?.status ?? "unknown" : index === 4 ? (summary.blockedNodes.length ? "warning" : "passed") : "passed";
                return (
                  <div key={item.title} className="absolute w-[150px] rounded-[18px] border bg-[rgba(11,18,30,0.92)] p-3 shadow-[0_18px_40px_rgba(0,0,0,0.32)]" style={{ left: `${left}%`, top }}>
                    <div className={`inline-flex rounded-full border px-2 py-0.5 text-[10px] ${statusTone(status)}`}>{item.eyebrow}</div>
                    <div className="mt-2 text-sm font-semibold text-[var(--fg)]">{item.title}</div>
                    <div className="mt-2 text-[11px] leading-5 text-[var(--muted)]">{item.body}</div>
                  </div>
                );
              })}
              <div className="absolute left-[12%] right-[10%] top-[142px] h-px bg-[linear-gradient(90deg,rgba(89,243,194,0.0),rgba(89,243,194,0.78),rgba(122,167,255,0.72),rgba(255,196,87,0.0))] shadow-[0_0_22px_rgba(89,243,194,0.35)]" />
            </div>
          </div>
        </div>
      </div>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1.1fr)_minmax(320px,0.9fr)]">
        <div className="rounded-[24px] border border-[var(--border)] bg-[rgba(16,24,38,0.62)] p-5 shadow-[var(--shadow-1)] backdrop-blur">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="text-xs uppercase tracking-[0.22em] text-[var(--muted)]">Commercial Proof Chain</div>
              <h2 className="mt-2 text-xl font-semibold text-[var(--fg)]">客户为什么愿意买：不是发现问题，而是把问题变成可交付证据</h2>
              <p className="mt-2 max-w-3xl text-sm leading-6 text-[var(--muted)]">把“环境、行为、执行、证据、报告”串成一条可汇报链路，客户才能相信这不是 demo 页面，而是企业级质量门禁。</p>
            </div>
            <Link href={`/projects/${p}/roi`} className="rounded-[var(--radius-sm)] border border-[rgba(89,243,194,0.28)] bg-[rgba(89,243,194,0.10)] px-3 py-2 text-sm text-[var(--fg)]">查看 ROI</Link>
          </div>
          <div className="mt-5 grid gap-3 md:grid-cols-2">
            {demoProofPoints.map((point, index) => (
              <div key={point} className="rounded-[18px] border border-[var(--border)] bg-[rgba(255,255,255,0.035)] p-4">
                <div className="text-xs text-[var(--muted)]">Proof {index + 1}</div>
                <div className="mt-2 text-sm font-semibold leading-6 text-[var(--fg)]">{point}</div>
              </div>
            ))}
          </div>

          <div className="mt-5 grid gap-3 md:grid-cols-3">
            <div className="rounded-[18px] border border-[rgba(122,167,255,0.20)] bg-[rgba(122,167,255,0.08)] p-4">
              <div className="text-xs text-[var(--muted)]">行为空间覆盖表达</div>
              <div className="mt-2 text-3xl font-semibold text-[var(--fg)]">{summary.coverageScore}%</div>
              <p className="mt-2 text-xs leading-5 text-[var(--muted)]">基于角色、接口、业务对象和门禁状态估算展示覆盖。</p>
            </div>
            <div className="rounded-[18px] border border-[rgba(89,243,194,0.20)] bg-[rgba(89,243,194,0.08)] p-4">
              <div className="text-xs text-[var(--muted)]">证据成熟度表达</div>
              <div className="mt-2 text-3xl font-semibold text-[var(--fg)]">{summary.evidenceScore}%</div>
              <p className="mt-2 text-xs leading-5 text-[var(--muted)]">不是承诺发现率，而是展示证据链可交付程度。</p>
            </div>
            <div className="rounded-[18px] border border-[rgba(255,92,122,0.20)] bg-[rgba(255,92,122,0.08)] p-4">
              <div className="text-xs text-[var(--muted)]">当前阻断</div>
              <div className="mt-2 text-3xl font-semibold text-[var(--fg)]">{summary.blockedNodes.length}</div>
              <p className="mt-2 text-xs leading-5 text-[var(--muted)]">先消除阻断，再谈规模化 runtime。</p>
            </div>
          </div>
        </div>

        <div className="grid gap-5">
          <div className="rounded-[24px] border border-[rgba(255,92,122,0.20)] bg-[rgba(255,92,122,0.08)] p-5">
            <div className="text-xs uppercase tracking-[0.22em] text-[var(--muted)]">Top Blockers</div>
            <h3 className="mt-2 text-lg font-semibold text-[var(--fg)]">先处理这几个点，产品才真正能跑</h3>
            <div className="mt-4 grid gap-3">
              {(topBlockers.length ? topBlockers : graph.blockers).slice(0, 3).map((blocker) => (
                <div key={blocker.blockerId} className="rounded-[16px] border border-[rgba(255,255,255,0.10)] bg-[rgba(8,13,21,0.42)] p-4">
                  <div className="flex items-center justify-between gap-2">
                    <div className="text-sm font-semibold text-[var(--fg)]">{blocker.title}</div>
                    <span className={`rounded-full border px-2 py-0.5 text-[11px] ${statusTone(blocker.status)}`}>{statusLabel(blocker.status)}</span>
                  </div>
                  <div className="mt-2 text-xs leading-5 text-[var(--muted)]">{blocker.reason}</div>
                  <div className="mt-2 rounded-[12px] border border-[rgba(255,196,87,0.18)] bg-[rgba(255,196,87,0.08)] px-3 py-2 text-xs leading-5 text-[var(--muted)]">下一步：{blocker.remediationAction}</div>
                </div>
              ))}
            </div>
          </div>

          <div className="rounded-[24px] border border-[var(--border)] bg-[rgba(16,24,38,0.62)] p-5">
            <div className="text-xs uppercase tracking-[0.22em] text-[var(--muted)]">Customer Inputs</div>
            <h3 className="mt-2 text-lg font-semibold text-[var(--fg)]">客户需要补什么，不再靠口头沟通</h3>
            <div className="mt-4 grid gap-3">
              {(topInputs.length ? topInputs : graph.requiredCustomerInputs).slice(0, 3).map((input) => (
                <div key={input.inputId} className="rounded-[16px] border border-[var(--border)] bg-[rgba(255,255,255,0.035)] p-4">
                  <div className="text-sm font-semibold text-[var(--fg)]">{input.title}</div>
                  <div className="mt-2 text-xs leading-5 text-[var(--muted)]">{input.whyNeeded}</div>
                  <div className="mt-2 text-xs text-[rgba(89,243,194,0.86)]">建议输入：{input.suggestedInput}</div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      <div className="rounded-[24px] border border-[var(--border)] bg-[rgba(16,24,38,0.58)] p-5 shadow-[var(--shadow-1)]">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="text-xs uppercase tracking-[0.22em] text-[var(--muted)]">Environment Nodes</div>
            <h2 className="mt-2 text-xl font-semibold text-[var(--fg)]">客户环境不是一行配置，而是一条准入链</h2>
          </div>
          <Link href={`/projects/${p}/environment`} className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(255,255,255,0.05)] px-3 py-2 text-sm text-[var(--fg)]">打开完整拓扑</Link>
        </div>
        <div className="mt-5 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          {topNodes.map((node) => (
            <div key={node.nodeId} className="rounded-[18px] border border-[var(--border)] bg-[rgba(255,255,255,0.035)] p-4">
              <div className="flex items-center justify-between gap-2">
                <div className="text-sm font-semibold text-[var(--fg)]">{node.label}</div>
                <span className={`rounded-full border px-2 py-0.5 text-[11px] ${statusTone(node.status)}`}>{statusLabel(node.status)}</span>
              </div>
              <div className="mt-2 text-xs font-medium text-[var(--fg)]">{node.headline}</div>
              <div className="mt-2 text-xs leading-5 text-[var(--muted)]">{node.businessExplanation}</div>
              <div className="mt-3 rounded-[12px] border border-[rgba(255,255,255,0.08)] bg-[rgba(8,13,21,0.36)] px-3 py-2 text-xs leading-5 text-[var(--muted)]">{node.nextAction}</div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
