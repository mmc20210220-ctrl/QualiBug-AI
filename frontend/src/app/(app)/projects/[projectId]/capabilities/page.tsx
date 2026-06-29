import Link from "next/link";
import { DecisionSummary } from "@/components/value-summary/DecisionSummary";
import { getBusinessModel, getCommandCenterSnapshot, listRisks, toSafeErrorView } from "@/lib/api/command-center";
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
  const raw = pickString(value);
  if (!raw) return fallback;
  const redacted = redactUnknown(raw);
  return typeof redacted === "string" ? redacted : fallback;
}

export default async function CapabilityCenterPage({ params }: { params: Promise<{ projectId: string }> }) {
  const { projectId } = await params;
  const p = encodeURIComponent(projectId);

  try {
    const [snapshotEnvelope, modelEnvelope, risksEnvelope] = await Promise.all([
      getCommandCenterSnapshot(projectId),
      getBusinessModel(projectId),
      listRisks(projectId),
    ]);

    const snapshot = snapshotEnvelope.data;
    const model = pickRecord(modelEnvelope.data) ?? {};
    const risks = Array.isArray(risksEnvelope.data) ? risksEnvelope.data : [];

    const flows = pickArray(model.confirmed_business_flows).filter((flow) => pickRecord(flow));
    const riskCountByFlow = new Map<string, number>();
    for (const item of risks) {
      const risk = pickRecord(item);
      const affected = risk ? pickRecord(risk.affected_business_flow) : null;
      const flowId = affected ? pickString(affected.business_flow_id) : null;
      if (!flowId) continue;
      riskCountByFlow.set(flowId, (riskCountByFlow.get(flowId) ?? 0) + 1);
    }

    return (
      <div className="grid gap-4">
        <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(16,24,38,0.55)] p-6 shadow-[var(--shadow-1)] backdrop-blur">
          <div className="text-xs text-[var(--muted)]">能力中心</div>
          <h1 className="mt-2 text-xl font-semibold tracking-tight">覆盖度与缺口</h1>
          <p className="mt-2 text-sm text-[var(--muted)]">
            该页聚焦“覆盖范围/缺口/下一步动作”，默认以业务链路视角呈现，并可下钻到风险证据链。
          </p>
        </div>

        <DecisionSummary projectId={projectId} snapshot={snapshot} />

        <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)] p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="text-xs text-[var(--muted)]">业务链路覆盖</div>
              <div className="mt-1 text-sm text-[var(--muted)]">每条链路展示状态、节点规模与已发现风险数量。</div>
            </div>
            <Link
              href={`/projects/${p}/risks`}
              className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(14,22,34,0.45)] px-3 py-2 text-sm hover:border-[rgba(255,255,255,0.18)]"
            >
              打开风险证据链
            </Link>
          </div>

          <div className="mt-4 overflow-hidden rounded-[var(--radius-md)] border border-[var(--border)]">
            <div className="grid grid-cols-[1fr_120px_120px_120px] gap-3 bg-[rgba(16,24,38,0.35)] px-4 py-3 text-xs text-[var(--muted)]">
              <div>链路</div>
              <div>状态</div>
              <div>节点</div>
              <div>风险</div>
            </div>
            <div className="divide-y divide-[var(--border)]">
              {flows.length ? (
                flows.slice(0, 40).map((flow) => {
                  const obj = pickRecord(flow) ?? {};
                  const flowId = safeText(obj.business_flow_id, "unknown");
                  const name = safeText(obj.name, "未命名链路");
                  const status = safeText(obj.status, "unknown");
                  const nodes = pickArray(obj.nodes);
                  const riskCount = riskCountByFlow.get(flowId) ?? 0;
                  return (
                    <div key={flowId} className="grid grid-cols-[1fr_120px_120px_120px] gap-3 px-4 py-3 text-sm">
                      <div className="min-w-0">
                        <div className="truncate text-[var(--fg)]">{name}</div>
                        <div className="mt-1 text-xs text-[var(--muted)]">flowId: {safeText(flowId)}</div>
                      </div>
                      <div className="text-[var(--muted)]">{status}</div>
                      <div className="text-[var(--muted)]">{nodes.length}</div>
                      <div className="text-[var(--muted)]">{riskCount}</div>
                    </div>
                  );
                })
              ) : (
                <div className="px-4 py-6 text-sm text-[var(--muted)]">暂无业务链路数据（请先完成业务链路建模）。</div>
              )}
            </div>
          </div>
        </div>
      </div>
    );
  } catch (err) {
    const safe = toSafeErrorView(err);
    return (
      <div className="grid gap-4">
        <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(16,24,38,0.55)] p-6 shadow-[var(--shadow-1)] backdrop-blur">
          <div className="text-xs text-[var(--muted)]">能力中心</div>
          <h1 className="mt-2 text-xl font-semibold tracking-tight">覆盖度与缺口</h1>
        </div>
        <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)] p-5">
          <div className="text-sm font-semibold text-[var(--fg)]">{safe.title}</div>
          <div className="mt-2 text-sm text-[var(--muted)]">{safe.detail}</div>
        </div>
      </div>
    );
  }
}
