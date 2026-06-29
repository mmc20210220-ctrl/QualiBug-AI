import Link from "next/link";
import { ExecutionRealtimePanel } from "@/components/execution/ExecutionRealtimePanel";
import { DecisionSummary } from "@/components/value-summary/DecisionSummary";
import { getExecutionPageInitialState } from "@/features/execution-runtime/server";
import { toSafeErrorView } from "@/lib/api/command-center";
import { redactUnknown } from "@/lib/redact";

function pickRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

function pickString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function pickArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function safeText(value: unknown, fallback = "—"): string {
  if (typeof value === "number") return String(value);
  const raw = pickString(value);
  if (!raw) return fallback;
  const redacted = redactUnknown(raw);
  return typeof redacted === "string" ? redacted : fallback;
}

export default async function ExecutionPage({ params }: { params: Promise<{ projectId: string }> }) {
  const { projectId } = await params;
  const p = encodeURIComponent(projectId);
  let safeError: { title: string; detail: string } | null = null;
  let snapshotEnvelope: unknown = null;
  let onboardingEnvelope: unknown = null;
  let runEnvelope: unknown = null;
  let currentStep = "unknown";
  let steps: unknown[] = [];

  try {
    const initialState = await getExecutionPageInitialState(projectId);
    snapshotEnvelope = initialState.snapshotEnvelope;
    onboardingEnvelope = initialState.onboardingEnvelope;
    runEnvelope = initialState.runEnvelope;
  } catch (err) {
    safeError = toSafeErrorView(err);
  }

  if (safeError) {
    return (
      <div className="grid gap-4">
        <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(16,24,38,0.55)] p-6 shadow-[var(--shadow-1)] backdrop-blur">
          <div className="text-xs text-[var(--muted)]">执行</div>
          <h1 className="mt-2 text-xl font-semibold tracking-tight">执行剧场与任务生命周期</h1>
        </div>
        <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)] p-5">
          <div className="text-sm font-semibold text-[var(--fg)]">{safeError.title}</div>
          <div className="mt-2 text-sm text-[var(--muted)]">{safeError.detail}</div>
        </div>
      </div>
    );
  }

  const snapshot = pickRecord((pickRecord(snapshotEnvelope) ?? {}).data) ?? {};
  const onboarding = pickRecord((pickRecord(onboardingEnvelope) ?? {}).data) ?? {};
  steps = pickArray(onboarding.steps);
  currentStep = safeText(onboarding.current_step, "unknown");

  return (
    <div className="grid gap-4">
      <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(16,24,38,0.55)] p-6 shadow-[var(--shadow-1)] backdrop-blur">
        <div className="text-xs text-[var(--muted)]">执行</div>
        <h1 className="mt-2 text-xl font-semibold tracking-tight">执行剧场与任务生命周期</h1>
        <p className="mt-2 text-sm text-[var(--muted)]">统一 RuntimeEvent、执行画布、证据下钻和 summary card，持续映射 lifecycle / probe / finding 落点。</p>
        <div className="mt-4 flex flex-wrap gap-2">
          <Link
            href={`/projects/${p}/risks?launch_blocking=true`}
            className="rounded-[var(--radius-sm)] border border-[rgba(255,92,122,0.24)] bg-[rgba(255,92,122,0.10)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(255,92,122,0.42)]"
          >
            打开阻断风险
          </Link>
          <Link
            href={`/projects/${p}/risks`}
            className="rounded-[var(--radius-sm)] border border-[rgba(122,167,255,0.24)] bg-[rgba(122,167,255,0.10)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(122,167,255,0.42)]"
          >
            查看全部风险证据
          </Link>
          <Link
            href={`/projects/${p}/reports/executive`}
            className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(14,22,34,0.45)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(255,255,255,0.18)]"
          >
            打开领导层报告
          </Link>
        </div>
      </div>

      <DecisionSummary projectId={projectId} snapshot={snapshot} />

      <ExecutionRealtimePanel projectId={projectId} initialSnapshotEnvelope={snapshotEnvelope} initialRunEnvelope={runEnvelope} />

      <div className="grid gap-3 md:grid-cols-2">
        <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)] p-5">
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="text-xs text-[var(--muted)]">生命周期阶段</div>
              <div className="mt-1 text-sm text-[var(--muted)]">当前阶段：{currentStep}</div>
            </div>
            <Link
              href={`/projects/${p}`}
              className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(14,22,34,0.45)] px-3 py-2 text-sm hover:border-[rgba(255,255,255,0.18)]"
            >
              返回工作区
            </Link>
          </div>

          <div className="mt-4 grid gap-2">
            {steps.length ? (
              steps.slice(0, 8).map((item, index) => {
                const step = pickRecord(item) ?? {};
                const label = safeText(step.label, "—");
                const status = safeText(step.status, "pending");
                return (
                  <div
                    key={`step:${index}:${label}:${status}`}
                    className="flex items-center justify-between gap-3 rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.35)] px-4 py-3 text-sm"
                  >
                    <div className="text-[var(--fg)]">{label}</div>
                    <div className="text-xs text-[var(--muted)]">{status}</div>
                  </div>
                );
              })
            ) : (
              <div className="text-sm text-[var(--muted)]">暂无生命周期信息</div>
            )}
          </div>
        </div>

        <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)] p-5">
          <div className="text-xs text-[var(--muted)]">可重试策略（建议）</div>
          <div className="mt-4 rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.35)] p-4">
            <ul className="mt-2 grid gap-2 text-sm text-[var(--muted)]">
              <li>若“生命周期阶段”停留在环境/计划阶段：优先补齐阻断项后再启动测试。</li>
              <li>若运行已完成但风险较多：先处理上线阻断风险，再执行回归。</li>
              <li>所有动作均可从风险证据链下钻到证据入口。</li>
            </ul>
            <div className="mt-3 flex flex-wrap gap-2">
              <Link
                href={`/projects/${p}/risks?launch_blocking=true`}
                className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(255,92,122,0.10)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(255,255,255,0.18)]"
              >
                查看上线阻断风险
              </Link>
              <Link
                href={`/projects/${p}/reports/executive`}
                className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(14,22,34,0.45)] px-3 py-2 text-sm hover:border-[rgba(255,255,255,0.18)]"
              >
                打开领导层报告
              </Link>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
