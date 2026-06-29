export type RealtimeMode = "auto" | "sse" | "poll" | "ws";

export interface RealtimePolicy {
  mode: RealtimeMode;
  pollIntervalMs: number;
  sseOpenTimeoutMs: number;
}

function parseMode(input: string | undefined): RealtimeMode {
  if (input === "sse" || input === "poll" || input === "ws" || input === "auto") return input;
  return "auto";
}

function parseIntOrDefault(input: string | undefined, fallback: number): number {
  if (!input) return fallback;
  const value = Number.parseInt(input, 10);
  return Number.isFinite(value) && value > 0 ? value : fallback;
}

export function readRealtimePolicy(): RealtimePolicy {
  return {
    mode: parseMode(process.env.NEXT_PUBLIC_REALTIME_MODE),
    pollIntervalMs: parseIntOrDefault(process.env.NEXT_PUBLIC_REALTIME_POLL_INTERVAL_MS, 3000),
    sseOpenTimeoutMs: parseIntOrDefault(process.env.NEXT_PUBLIC_REALTIME_SSE_OPEN_TIMEOUT_MS, 4500),
  };
}

