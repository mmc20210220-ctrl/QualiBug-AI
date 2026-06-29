import type { RuntimeHealthState } from "@/lib/api/runtime-health";

function badgeClasses(state: RuntimeHealthState["state"]): string {
  if (state === "online") return "border-[rgba(89,243,194,0.35)] bg-[rgba(89,243,194,0.10)] text-[rgba(196,255,238,0.95)]";
  if (state === "unverified") return "border-[rgba(255,210,94,0.35)] bg-[rgba(255,210,94,0.10)] text-[rgba(255,241,203,0.95)]";
  if (state === "offline") return "border-[rgba(255,86,86,0.35)] bg-[rgba(255,86,86,0.10)] text-[rgba(255,220,220,0.92)]";
  return "border-[rgba(255,86,86,0.35)] bg-[rgba(255,86,86,0.10)] text-[rgba(255,220,220,0.92)]";
}

function label(state: RuntimeHealthState): string {
  if (state.state === "online") return "online";
  if (state.state === "unverified") return "unverified";
  if (state.state === "offline") return "offline";
  return "error";
}

export function RuntimeHealthBadge({ health }: { health: RuntimeHealthState }) {
  return (
    <div className={`inline-flex items-center gap-2 rounded-[999px] border px-3 py-1 text-xs ${badgeClasses(health.state)}`}>
      <span className="font-medium">{label(health)}</span>
      <span className="opacity-80">{health.source}</span>
    </div>
  );
}

export function RuntimeHealthDetail({ health }: { health: RuntimeHealthState }) {
  if (health.state === "online") {
    return (
      <div className="mt-3 grid gap-1 text-xs text-[var(--muted)]">
        <div>version: {health.version ?? "unknown"}</div>
        <div>redaction: {health.redactionStatus ?? "unknown"}</div>
      </div>
    );
  }
  return <div className="mt-3 text-xs text-[var(--muted)]">{health.detail}</div>;
}

