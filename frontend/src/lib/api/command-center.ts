import "server-only";
import { requestJson, ApiClientError } from "@/lib/api/client";
import { readAuthConfig } from "@/lib/auth/config";
import {
  getDemoBusinessModel,
  getDemoCommandCenterSnapshot,
  getDemoEnvironmentReadiness,
  getDemoExecutiveReport,
  getDemoLiveMap,
  getDemoOnboarding,
  getDemoRiskDetail,
  getDemoRisks,
  getDemoTestRun,
  getDemoValueMetrics,
  startDemoTestRun,
} from "@/lib/api/command-center-demo";
import { redactUnknown } from "@/lib/redact";

export interface ApiEnvelope<T> {
  success: boolean;
  data: T;
  error: { code?: string; message?: string; status?: number; details?: unknown } | null;
  meta?: unknown;
}

function buildQuery(params: Record<string, string | number | boolean | undefined | null>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    search.set(key, String(value));
  }
  const qs = search.toString();
  return qs ? `?${qs}` : "";
}

function toErrorMessage(err: unknown): string {
  if (err instanceof ApiClientError) return err.message;
  return "请求失败";
}

export async function getCommandCenterSnapshot(projectId: string): Promise<ApiEnvelope<unknown>> {
  if (readAuthConfig().mode === "demo") return getDemoCommandCenterSnapshot(projectId);
  return requestJson<ApiEnvelope<unknown>>({
    method: "GET",
    path: `/api/v1/projects/${encodeURIComponent(projectId)}/command-center`,
    timeoutMs: 8000,
    retry: { retries: 1, baseDelayMs: 250, maxDelayMs: 1200 },
  });
}

export async function getValueMetrics(projectId: string): Promise<ApiEnvelope<unknown>> {
  if (readAuthConfig().mode === "demo") return getDemoValueMetrics(projectId);
  return requestJson<ApiEnvelope<unknown>>({
    method: "GET",
    path: `/api/v1/projects/${encodeURIComponent(projectId)}/value-metrics`,
    timeoutMs: 8000,
    retry: { retries: 1, baseDelayMs: 250, maxDelayMs: 1200 },
  });
}

export async function getBusinessModel(projectId: string): Promise<ApiEnvelope<unknown>> {
  if (readAuthConfig().mode === "demo") return getDemoBusinessModel(projectId);
  return requestJson<ApiEnvelope<unknown>>({
    method: "GET",
    path: `/api/v1/projects/${encodeURIComponent(projectId)}/business-model`,
    timeoutMs: 8000,
    retry: { retries: 1, baseDelayMs: 250, maxDelayMs: 1200 },
  });
}

export async function getEnvironmentReadiness(projectId: string): Promise<ApiEnvelope<unknown>> {
  if (readAuthConfig().mode === "demo") return getDemoEnvironmentReadiness(projectId);
  return requestJson<ApiEnvelope<unknown>>({
    method: "GET",
    path: `/api/v1/projects/${encodeURIComponent(projectId)}/environment/readiness`,
    timeoutMs: 8000,
    retry: { retries: 1, baseDelayMs: 250, maxDelayMs: 1200 },
  });
}

export async function listRisks(
  projectId: string,
  input: { severity?: string; status?: string; business_flow_id?: string; launch_blocking?: boolean } = {},
): Promise<ApiEnvelope<unknown>> {
  if (readAuthConfig().mode === "demo") return getDemoRisks(projectId, input);
  const query = buildQuery(input);
  return requestJson<ApiEnvelope<unknown>>({
    method: "GET",
    path: `/api/v1/projects/${encodeURIComponent(projectId)}/risks${query}`,
    timeoutMs: 8000,
    retry: { retries: 1, baseDelayMs: 250, maxDelayMs: 1200 },
  });
}

export async function getRiskDetail(projectId: string, riskId: string): Promise<ApiEnvelope<unknown>> {
  if (readAuthConfig().mode === "demo") return getDemoRiskDetail(projectId, riskId);
  return requestJson<ApiEnvelope<unknown>>({
    method: "GET",
    path: `/api/v1/projects/${encodeURIComponent(projectId)}/risks/${encodeURIComponent(riskId)}`,
    timeoutMs: 8000,
    retry: { retries: 1, baseDelayMs: 250, maxDelayMs: 1200 },
  });
}

export async function getOnboarding(projectId: string): Promise<ApiEnvelope<unknown>> {
  if (readAuthConfig().mode === "demo") return getDemoOnboarding(projectId);
  return requestJson<ApiEnvelope<unknown>>({
    method: "GET",
    path: `/api/v1/projects/${encodeURIComponent(projectId)}/onboarding`,
    timeoutMs: 8000,
    retry: { retries: 1, baseDelayMs: 250, maxDelayMs: 1200 },
  });
}

export async function getTestRun(projectId: string, runId: string): Promise<ApiEnvelope<unknown>> {
  if (readAuthConfig().mode === "demo") return getDemoTestRun(projectId, runId);
  return requestJson<ApiEnvelope<unknown>>({
    method: "GET",
    path: `/api/v1/projects/${encodeURIComponent(projectId)}/test-runs/${encodeURIComponent(runId)}`,
    timeoutMs: 8000,
    retry: { retries: 1, baseDelayMs: 250, maxDelayMs: 1200 },
  });
}

export async function getLiveMap(projectId: string): Promise<ApiEnvelope<unknown>> {
  if (readAuthConfig().mode === "demo") return getDemoLiveMap(projectId);
  return requestJson<ApiEnvelope<unknown>>({
    method: "GET",
    path: `/api/v1/projects/${encodeURIComponent(projectId)}/live-map`,
    timeoutMs: 8000,
    retry: { retries: 1, baseDelayMs: 250, maxDelayMs: 1200 },
  });
}

export async function startTestRun(
  projectId: string,
  input: { run_id?: string; findings?: Record<string, unknown>[] } = {},
): Promise<ApiEnvelope<unknown>> {
  if (readAuthConfig().mode === "demo") return startDemoTestRun(projectId);
  return requestJson<ApiEnvelope<unknown>>({
    method: "POST",
    path: `/api/v1/projects/${encodeURIComponent(projectId)}/test-runs`,
    body: input,
    timeoutMs: 20000,
  });
}

export async function getExecutiveReport(projectId: string): Promise<ApiEnvelope<unknown>> {
  if (readAuthConfig().mode === "demo") return getDemoExecutiveReport(projectId);
  return requestJson<ApiEnvelope<unknown>>({
    method: "GET",
    path: `/api/v1/projects/${encodeURIComponent(projectId)}/reports/executive`,
    timeoutMs: 12000,
    retry: { retries: 1, baseDelayMs: 300, maxDelayMs: 1500 },
  });
}

export async function generateExecutiveReport(projectId: string): Promise<ApiEnvelope<unknown>> {
  return requestJson<ApiEnvelope<unknown>>({
    method: "POST",
    path: `/api/v1/projects/${encodeURIComponent(projectId)}/reports/generate`,
    timeoutMs: 20000,
  });
}

export function toSafeErrorView(err: unknown): { title: string; detail: string } {
  if (err instanceof ApiClientError) {
    const payload = redactUnknown(err.payload);
    const obj = payload && typeof payload === "object" ? (payload as Record<string, unknown>) : null;
    const msg =
      obj && typeof obj.error === "object" && obj.error ? (obj.error as Record<string, unknown>).message : undefined;
    return { title: err.kind === "http" ? `请求失败 (${err.status ?? "-"})` : "请求失败", detail: String(msg ?? err.message) };
  }
  return { title: "请求失败", detail: toErrorMessage(err) };
}
