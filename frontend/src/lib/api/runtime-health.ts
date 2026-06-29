import { readAuthConfig } from "@/lib/auth/config";
import { ApiClientError, requestJson } from "@/lib/api/client";

export type RuntimeHealthSource = "demo" | "real";

export type RuntimeHealthState =
  | { state: "unverified"; source: RuntimeHealthSource; detail: string }
  | { state: "online"; source: RuntimeHealthSource; version?: string; redactionStatus?: string }
  | { state: "offline"; source: RuntimeHealthSource; detail: string }
  | { state: "error"; source: RuntimeHealthSource; detail: string; status?: number };

function pickString(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

export async function getRuntimeHealth(): Promise<RuntimeHealthState> {
  const config = readAuthConfig();
  if (config.mode === "demo") {
    return { state: "unverified", source: "demo", detail: "demo 模式不进行后端探测" };
  }

  try {
    const envelope = await requestJson<{
      success?: boolean;
      data?: unknown;
      error?: { message?: string } | null;
    }>({
      method: "GET",
      path: "/api/v1/health",
      timeoutMs: 1500,
      retry: { retries: 1, baseDelayMs: 200, maxDelayMs: 600 },
    });

    const ok = envelope && typeof envelope === "object" && (envelope.success === true || envelope.success === undefined);
    const data = ok ? (envelope.data as unknown) : null;
    const status = data && typeof data === "object" ? (data as Record<string, unknown>).status : undefined;
    const version = data && typeof data === "object" ? (data as Record<string, unknown>).version : undefined;
    const redaction = data && typeof data === "object" ? (data as Record<string, unknown>).redaction_status : undefined;

    if (pickString(status) === "ok") {
      return {
        state: "online",
        source: "real",
        version: pickString(version),
        redactionStatus: pickString(redaction),
      };
    }

    const message =
      envelope?.error && typeof envelope.error === "object" ? pickString((envelope.error as Record<string, unknown>).message) : undefined;
    return { state: "error", source: "real", detail: message ?? "健康检查返回非 ok 状态" };
  } catch (err) {
    if (err instanceof ApiClientError) {
      if (err.kind === "unverified") return { state: "unverified", source: "real", detail: err.message };
      if (err.kind === "network" || err.kind === "timeout") return { state: "offline", source: "real", detail: err.message };
      if (err.kind === "aborted") return { state: "offline", source: "real", detail: err.message };
      if (err.kind === "http") return { state: "error", source: "real", detail: err.message, status: err.status };
      return { state: "error", source: "real", detail: err.message };
    }
    return { state: "error", source: "real", detail: "健康检查失败" };
  }
}

