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

function pickNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
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
    const snapshotObj = pickRecord(snapshot) ?? {};
    const model = pickRecord(modelEnvelope.data) ?? {};
    const risks = Array.isArray(risksEnvelope.data) ? risksEnvelope.data : [];
    const coverage = pickRecord(snapshotObj.bug_family_coverage) ?? {};
    const matrix = pickRecord(snapshotObj.full_spectrum_capability_matrix) ?? {};
    const coverageRows = pickArray(coverage.rows).map((item) => pickRecord(item)).filter(Boolean) as Record<string, unknown>[];
    const sourceRows = pickArray(matrix.rows)
      .map((item) => pickRecord(item))
      .filter((item): item is Record<string, unknown> => Boolean(item) && Boolean(pickString(item?.source_id)))
      .slice(0, 20);
    const missingSourceFamilies = coverageRows
      .filter((row) => (pickNumber(row.missing_source_count) ?? 0) > 0)
      .sort((a, b) => (pickNumber(b.missing_source_count) ?? 0) - (pickNumber(a.missing_source_count) ?? 0))
      .slice(0, 8);
    const coverageSummaryCards = [
      { label: "已覆盖家族", value: pickNumber(coverage.covered_family_count) ?? 0 },
      { label: "声明 Source", value: pickNumber(coverage.declared_source_count) ?? 0 },
      { label: "已落地 Source", value: pickNumber(coverage.materialized_source_count) ?? 0 },
      { label: "缺失 Source", value: pickNumber(coverage.missing_source_count) ?? 0 },
    ];

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

        <div className="grid gap-4 lg:grid-cols-4">
          {coverageSummaryCards.map((item) => (
            <div
              key={item.label}
              className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)] p-5"
            >
              <div className="text-xs text-[var(--muted)]">{item.label}</div>
              <div className="mt-2 text-2xl font-semibold tracking-tight">{item.value}</div>
            </div>
          ))}
        </div>

        <div className="grid gap-4 xl:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
          <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)] p-5">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <div className="text-xs text-[var(--muted)]">Source 级覆盖缺口</div>
                <div className="mt-1 text-sm text-[var(--muted)]">按 defect family 展示声明 source、已落地 source 与当前缺口原因。</div>
              </div>
              <div className="text-xs text-[var(--muted)]">Top {missingSourceFamilies.length || 0}</div>
            </div>
            <div className="mt-4 overflow-hidden rounded-[var(--radius-md)] border border-[var(--border)]">
              <div className="grid grid-cols-[180px_110px_110px_minmax(0,1fr)] gap-3 bg-[rgba(16,24,38,0.35)] px-4 py-3 text-xs text-[var(--muted)]">
                <div>Family</div>
                <div>声明</div>
                <div>已落地</div>
                <div>缺失 Source / 原因</div>
              </div>
              <div className="divide-y divide-[var(--border)]">
                {missingSourceFamilies.length ? (
                  missingSourceFamilies.map((row) => {
                    const familyId = safeText(row.family_id);
                    const displayName = safeText(row.display_name, familyId);
                    const missingSources = pickArray(row.missing_declared_sources).map((item) => safeText(item)).filter((item) => item !== "—");
                    return (
                      <div key={familyId} className="grid grid-cols-[180px_110px_110px_minmax(0,1fr)] gap-3 px-4 py-3 text-sm">
                        <div>
                          <div className="text-[var(--fg)]">{displayName}</div>
                          <div className="mt-1 text-xs text-[var(--muted)]">{familyId}</div>
                        </div>
                        <div className="text-[var(--muted)]">{pickNumber(row.declared_source_count) ?? 0}</div>
                        <div className="text-[var(--muted)]">{pickNumber(row.materialized_source_count) ?? 0}</div>
                        <div className="min-w-0">
                          <div className="truncate text-[var(--fg)]">
                            {missingSources.length ? missingSources.join(", ") : safeText(row.source_gap_reason_code)}
                          </div>
                          <div className="mt-1 text-xs text-[var(--muted)]">{safeText(row.source_gap_reason, "待补齐 source 级链路")}</div>
                        </div>
                      </div>
                    );
                  })
                ) : (
                  <div className="px-4 py-6 text-sm text-[var(--muted)]">暂无 source 级缺口数据，说明当前快照尚未接入 full spectrum coverage。</div>
                )}
              </div>
            </div>
          </div>

          <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)] p-5">
            <div>
              <div className="text-xs text-[var(--muted)]">Source Gate 摘要</div>
              <div className="mt-1 text-sm text-[var(--muted)]">展示命令中心快照中透传的 source 级 preflight 状态与阻断原因。</div>
            </div>
            <div className="mt-4 space-y-3">
              {sourceRows.length ? (
                sourceRows.map((row) => (
                  <div key={`${safeText(row.defect_family)}:${safeText(row.source_id)}`} className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.32)] p-4">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="text-sm font-medium text-[var(--fg)]">{safeText(row.source_id)}</div>
                      <div className="text-xs text-[var(--muted)]">{safeText(row.preflight_lane)}</div>
                    </div>
                    <div className="mt-2 text-xs text-[var(--muted)]">family: {safeText(row.defect_family)}</div>
                    <div className="mt-2 text-sm text-[var(--muted)]">{safeText(row.reason, "已落地或暂无阻断说明")}</div>
                    <div className="mt-2 text-xs text-[var(--muted)]">action: {safeText(row.action, "—")}</div>
                  </div>
                ))
              ) : (
                <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.32)] p-4 text-sm text-[var(--muted)]">
                  当前快照没有 source 级 matrix rows。
                </div>
              )}
            </div>
          </div>
        </div>

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
