import Link from "next/link";
import { redactUnknown, maskId } from "@/lib/redact";

function pickRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

function pickString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function pickBoolean(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

function safeText(value: unknown, fallback = "—"): string {
  const raw = pickString(value);
  if (!raw) return fallback;
  const redacted = redactUnknown(raw);
  return typeof redacted === "string" ? redacted : fallback;
}

function safeId(value: unknown): string {
  const raw = pickString(value);
  if (!raw) return "unknown";
  return raw.length > 60 ? maskId(raw, 6, 6) : raw;
}

export function RiskTable({ projectId, risks }: { projectId: string; risks: unknown }) {
  const items = Array.isArray(risks) ? risks : [];
  const p = encodeURIComponent(projectId);

  return (
    <div className="overflow-hidden rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)]">
      <div className="grid grid-cols-[1fr_110px_120px_110px] gap-3 border-b border-[var(--border)] bg-[rgba(16,24,38,0.35)] px-5 py-3 text-xs text-[var(--muted)]">
        <div>风险</div>
        <div>严重度</div>
        <div>业务链路</div>
        <div>上线阻断</div>
      </div>
      <div className="divide-y divide-[var(--border)]">
        {items.length ? (
          items.map((item) => {
            const obj = pickRecord(item) ?? {};
            const riskId = safeId(obj.risk_id);
            const title = safeText(obj.title);
            const severity = safeText(obj.severity);
            const launchBlocking = pickBoolean(obj.launch_blocking);
            const flow = pickRecord(obj.affected_business_flow);
            const flowName = safeText(flow?.name);
            const href = `/projects/${p}/risks/${encodeURIComponent(riskId)}`;

            return (
              <Link
                key={riskId}
                href={href}
                className="grid grid-cols-[1fr_110px_120px_110px] gap-3 px-5 py-4 text-sm hover:bg-[rgba(255,255,255,0.04)]"
              >
                <div className="min-w-0">
                  <div className="truncate text-[var(--fg)]">{title}</div>
                  <div className="mt-1 text-xs text-[var(--muted)]">riskId: {maskId(riskId, 6, 4)}</div>
                </div>
                <div className="text-[var(--fg)]">{severity}</div>
                <div className="truncate text-[var(--muted)]">{flowName}</div>
                <div className="text-[var(--fg)]">{launchBlocking === null ? "—" : launchBlocking ? "是" : "否"}</div>
              </Link>
            );
          })
        ) : (
          <div className="px-5 py-6 text-sm text-[var(--muted)]">暂无风险数据</div>
        )}
      </div>
    </div>
  );
}

