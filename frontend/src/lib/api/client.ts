import "server-only";
import { headers } from "next/headers";
import { readAuthConfig } from "@/lib/auth/config";
import { getSession } from "@/lib/auth/server";
import { redactUnknown } from "@/lib/redact";

export type HttpMethod = "GET" | "POST" | "PATCH" | "DELETE";

export type ApiErrorKind = "http" | "network" | "timeout" | "aborted" | "unverified" | "parse";

export class ApiClientError extends Error {
  kind: ApiErrorKind;
  status?: number;
  url?: string;
  payload?: unknown;

  constructor(input: { kind: ApiErrorKind; message: string; status?: number; url?: string; payload?: unknown }) {
    super(input.message);
    this.name = "ApiClientError";
    this.kind = input.kind;
    this.status = input.status;
    this.url = input.url;
    this.payload = input.payload;
  }
}

const FORBIDDEN_TRUSTED_HEADERS = new Set([
  "x-qualibug-actor",
  "x-qualibug-role",
  "x-qualibug-project-scopes",
  "x-trusted-actor",
  "x-trusted-role",
]);

export function sanitizeOutgoingHeaders(headersInit: HeadersInit | undefined): HeadersInit | undefined {
  if (!headersInit) return headersInit;
  const normalized = new Headers(headersInit);
  for (const key of Array.from(normalized.keys())) {
    if (FORBIDDEN_TRUSTED_HEADERS.has(key.toLowerCase())) {
      normalized.delete(key);
    }
  }
  return normalized;
}

async function resolveApiUrl(apiPath: string): Promise<string> {
  if (/^https?:\/\//i.test(apiPath)) return apiPath;
  const base = process.env.QUALIBUG_API_BASE_URL ?? process.env.NEXT_PUBLIC_QUALIBUG_API_BASE_URL;
  if (base) return new URL(apiPath, base).toString();

  try {
    const hdrs = await headers();
    const host = hdrs.get("x-forwarded-host") ?? hdrs.get("host");
    const proto = hdrs.get("x-forwarded-proto") ?? "http";
    if (!host) throw new Error("missing host");
    return `${proto}://${host}${apiPath.startsWith("/") ? apiPath : `/${apiPath}`}`;
  } catch {
    throw new ApiClientError({
      kind: "unverified",
      message: "API_BASE_URL 未配置，且当前上下文无法推导服务地址。",
      url: apiPath,
    });
  }
}

function isRetriableStatus(status: number): boolean {
  return status === 408 || status === 425 || status === 429 || status === 502 || status === 503 || status === 504;
}

async function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  if (ms <= 0) return;
  await new Promise<void>((resolve, reject) => {
    const timer = setTimeout(resolve, ms);
    if (!signal) return;
    if (signal.aborted) {
      clearTimeout(timer);
      reject(new DOMException("Aborted", "AbortError"));
      return;
    }
    const onAbort = () => {
      clearTimeout(timer);
      reject(new DOMException("Aborted", "AbortError"));
    };
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

export interface RequestJsonOptions {
  method: HttpMethod;
  path: string;
  body?: unknown;
  headers?: HeadersInit;
  timeoutMs?: number;
  signal?: AbortSignal;
  retry?: {
    retries: number;
    baseDelayMs?: number;
    maxDelayMs?: number;
  };
}

export async function requestJson<T>(input: RequestJsonOptions): Promise<T> {
  const url = await resolveApiUrl(input.path);
  const config = readAuthConfig();

  const headersInit = new Headers(sanitizeOutgoingHeaders(input.headers));
  if (!headersInit.has("Content-Type")) headersInit.set("Content-Type", "application/json");

  if (config.mode === "oidc") {
    const session = await getSession();
    if (session?.accessToken && !headersInit.has("Authorization")) {
      headersInit.set("Authorization", `Bearer ${session.accessToken}`);
    }
  }

  const retries = input.retry?.retries ?? 0;
  const baseDelayMs = input.retry?.baseDelayMs ?? 200;
  const maxDelayMs = input.retry?.maxDelayMs ?? 2000;

  for (let attempt = 0; attempt <= retries; attempt++) {
    const controller = new AbortController();
    let timeoutFired = false;
    const timer =
      input.timeoutMs && input.timeoutMs > 0
        ? setTimeout(() => {
            timeoutFired = true;
            controller.abort();
          }, input.timeoutMs)
        : null;

    const onAbort = () => controller.abort();
    if (input.signal) {
      if (input.signal.aborted) controller.abort();
      else input.signal.addEventListener("abort", onAbort, { once: true });
    }

    try {
      const response = await fetch(url, {
        method: input.method,
        headers: headersInit,
        body: input.body === undefined ? undefined : JSON.stringify(input.body),
        signal: controller.signal,
      });

      const text = await response.text();
      let payload: unknown = null;
      try {
        payload = text ? (JSON.parse(text) as unknown) : null;
      } catch {
        if (!response.ok) {
          throw new ApiClientError({
            kind: "http",
            status: response.status,
            url,
            message: `API ${input.method} ${input.path} failed with ${response.status}`,
            payload: text,
          });
        }
        throw new ApiClientError({
          kind: "parse",
          status: response.status,
          url,
          message: `API ${input.method} ${input.path} 返回了不可解析的 JSON`,
          payload: text,
        });
      }

      if (!response.ok) {
        if (attempt < retries && input.method === "GET" && isRetriableStatus(response.status)) {
          const delay = Math.min(maxDelayMs, baseDelayMs * 2 ** attempt);
          await sleep(delay, input.signal);
          continue;
        }

        const safe = redactUnknown(payload) as Record<string, unknown> | null;
        const message =
          safe && typeof safe.message === "string"
            ? safe.message
            : `API ${input.method} ${input.path} failed with ${response.status}`;
        throw new ApiClientError({ kind: "http", status: response.status, url, message, payload: safe ?? payload });
      }

      return payload as T;
    } catch (err) {
      if (attempt < retries && input.method === "GET") {
        if (err instanceof ApiClientError && (err.kind === "network" || err.kind === "timeout")) {
          const delay = Math.min(maxDelayMs, baseDelayMs * 2 ** attempt);
          await sleep(delay, input.signal);
          continue;
        }
        if (!(err instanceof ApiClientError) && err instanceof TypeError) {
          const delay = Math.min(maxDelayMs, baseDelayMs * 2 ** attempt);
          await sleep(delay, input.signal);
          continue;
        }
      }

      if (err instanceof ApiClientError) throw err;
      if (err instanceof DOMException && err.name === "AbortError") {
        if (timeoutFired) {
          throw new ApiClientError({ kind: "timeout", message: `API ${input.method} ${input.path} 超时`, url });
        }
        throw new ApiClientError({ kind: "aborted", message: `API ${input.method} ${input.path} 已取消`, url });
      }
      if (err instanceof TypeError) {
        throw new ApiClientError({ kind: "network", message: `API ${input.method} ${input.path} 网络异常`, url });
      }
      throw new ApiClientError({ kind: "network", message: `API ${input.method} ${input.path} 请求失败`, url, payload: err });
    } finally {
      if (timer) clearTimeout(timer);
      if (input.signal) input.signal.removeEventListener("abort", onAbort);
    }
  }

  throw new ApiClientError({ kind: "network", message: `API ${input.method} ${input.path} 请求失败`, url });
}
