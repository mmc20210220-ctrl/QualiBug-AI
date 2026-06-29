import { DecisionSummary } from "@/components/value-summary/DecisionSummary";
import { RiskFilters } from "@/components/risk/RiskFilters";
import { RiskTable } from "@/components/risk/RiskTable";
import { getCommandCenterSnapshot, listRisks, toSafeErrorView } from "@/lib/api/command-center";

export default async function RiskEvidenceListPage({
  params,
  searchParams,
}: {
  params: Promise<{ projectId: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { projectId } = await params;
  const query = await searchParams;

  const severity = typeof query.severity === "string" ? query.severity : undefined;
  const status = typeof query.status === "string" ? query.status : undefined;
  const launchBlockingRaw = typeof query.launch_blocking === "string" ? query.launch_blocking : undefined;
  const launch_blocking =
    launchBlockingRaw === "true" ? true : launchBlockingRaw === "false" ? false : launchBlockingRaw ? undefined : undefined;

  try {
    const [snapshotEnvelope, risksEnvelope] = await Promise.all([
      getCommandCenterSnapshot(projectId),
      listRisks(projectId, { severity, status, launch_blocking }),
    ]);

    return (
      <div className="grid gap-4">
        <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(16,24,38,0.55)] p-6 shadow-[var(--shadow-1)] backdrop-blur">
          <div className="text-xs text-[var(--muted)]">风险证据</div>
          <h1 className="mt-2 text-xl font-semibold tracking-tight">风险证据链</h1>
          <p className="mt-2 text-sm text-[var(--muted)]">默认突出“上线阻断/业务影响/可复现证据”，支持按条件筛选。</p>
        </div>

        <DecisionSummary projectId={projectId} snapshot={snapshotEnvelope.data} />

        <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)] p-5">
          <div className="text-xs text-[var(--muted)]">筛选</div>
          <div className="mt-3">
            <RiskFilters />
          </div>
        </div>

        <RiskTable projectId={projectId} risks={risksEnvelope.data} />
      </div>
    );
  } catch (err) {
    const safe = toSafeErrorView(err);
    return (
      <div className="grid gap-4">
        <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(16,24,38,0.55)] p-6 shadow-[var(--shadow-1)] backdrop-blur">
          <div className="text-xs text-[var(--muted)]">风险证据</div>
          <h1 className="mt-2 text-xl font-semibold tracking-tight">风险证据链</h1>
        </div>
        <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)] p-5">
          <div className="text-sm font-semibold text-[var(--fg)]">{safe.title}</div>
          <div className="mt-2 text-sm text-[var(--muted)]">{safe.detail}</div>
        </div>
      </div>
    );
  }
}
