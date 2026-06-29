import type { RealtimeConnectionState } from "@/lib/realtime/command-center";

function badgeClasses(phase: RealtimeConnectionState["phase"]): string {
  if (phase === "open") return "border-[rgba(89,243,194,0.35)] bg-[rgba(89,243,194,0.10)] text-[rgba(196,255,238,0.95)]";
  if (phase === "connecting")
    return "border-[rgba(255,210,94,0.35)] bg-[rgba(255,210,94,0.10)] text-[rgba(255,241,203,0.95)]";
  if (phase === "degraded")
    return "border-[rgba(255,210,94,0.35)] bg-[rgba(255,210,94,0.10)] text-[rgba(255,241,203,0.95)]";
  if (phase === "idle") return "border-[rgba(255,210,94,0.35)] bg-[rgba(255,210,94,0.10)] text-[rgba(255,241,203,0.95)]";
  if (phase === "closed") return "border-[rgba(255,86,86,0.35)] bg-[rgba(255,86,86,0.10)] text-[rgba(255,220,220,0.92)]";
  return "border-[rgba(255,86,86,0.35)] bg-[rgba(255,86,86,0.10)] text-[rgba(255,220,220,0.92)]";
}

function label(phase: RealtimeConnectionState["phase"]): string {
  if (phase === "open") return "connected";
  if (phase === "connecting") return "connecting";
  if (phase === "degraded") return "degraded";
  if (phase === "idle") return "idle";
  if (phase === "closed") return "closed";
  return "error";
}

export function RealtimeConnectionBadge({ state }: { state: RealtimeConnectionState }) {
  const detail = state.detail ? ` · ${state.detail}` : "";
  return (
    <div className={`inline-flex items-center gap-2 rounded-[999px] border px-3 py-1 text-xs ${badgeClasses(state.phase)}`}>
      <span className="font-medium">
        {label(state.phase)}({state.transport})
      </span>
      <span className="opacity-80">{state.mode}</span>
      {detail ? <span className="opacity-80">{detail}</span> : null}
    </div>
  );
}

