/**
 * 认证 / 会话 / 传输 / 项目解析基础设施。
 * 拆分自 api/client.ts：承载 HTTP 传输、错误解析、登录/登出、
 * 会话缓存与项目解析，供 client.ts 的业务 API 复用。
 * 纯单向依赖：session.ts → client-types.ts / value-guards.ts。
 */
import { asArray, asRecord, asString } from '../lib/value-guards';
import type { CustomerWorkspace, JsonRecord, LoginResult, RegisterResult, SessionResult } from './client-types';

export const API_BASE = '/api';

export const API_V1_BASE = '/api/v1';
const DEV_TOKEN_KEY = 'qualibug_dev_token';
const SESSION_MARKER_KEY = 'qualibug_validated_session';

/**
 * 传输层错误：携带真实 HTTP 状态码。调用方必须按状态码分支（如 404=资源不存在），
 * 禁止对 error.message 做子串匹配来推断状态。
 */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

const devTokenEnabled = (): boolean => import.meta.env.DEV && import.meta.env.VITE_QUALIBUG_ENABLE_DEV_TOKEN === 'true';

export function asBoolean(value: unknown, fallback = false): boolean {
  return typeof value === 'boolean' ? value : fallback;
}

function toProjectSummary(value: unknown): CustomerWorkspace {
  const item = asRecord(value);
  return {
    project_id: asString(item.project_id) || undefined,
    customer_name: asString(item.customer_name) || undefined,
    project_name: asString(item.project_name) || undefined,
    system_name: asString(item.system_name) || undefined,
    industry: asString(item.industry) || undefined,
  };
}

const SUPPORT_HINT = '如问题持续，请运行 qualibug-doctor --export-bundle 并发送给技术支持';
const QB_CODE_RE = /QB-[A-Z]\d{3}/;

export function parseApiErrorMessage(status: number, text: string): string {
  const trimmed = text.trim();
  if (!trimmed) return `API ${status}`;
  try {
    const payload = asRecord(JSON.parse(trimmed));
    const errorCode = asString(payload.error_code);
    const supportHint = asString(payload.support_hint);
    const message = asString(payload.message) || asString(payload.error) || asString(payload.detail);
    if (errorCode && QB_CODE_RE.test(errorCode)) {
      const base = message || `系统错误 ${errorCode}`;
      return supportHint ? `${base}（${errorCode}）\n${supportHint}` : `${base}（${errorCode}）`;
    }
    if (message) {
      const codeMatch = message.match(QB_CODE_RE);
      if (codeMatch) return `${message}\n${SUPPORT_HINT}`;
      return `API ${status}: ${message}`;
    }
  } catch {
    // Preserve a bounded non-JSON response for operators.
  }
  if (QB_CODE_RE.test(trimmed)) return `${trimmed.slice(0, 200)}\n${SUPPORT_HINT}`;
  return `API ${status}: ${trimmed.slice(0, 200)}`;
}

export async function readResponsePayload(response: Response): Promise<{ data: JsonRecord; rawText: string }> {
  const rawText = await response.text();
  if (!rawText.trim()) return { data: {}, rawText: '' };
  try {
    return { data: asRecord(JSON.parse(rawText)), rawText };
  } catch {
    return { data: {}, rawText };
  }
}

export function authFacingError(status: number, data: JsonRecord, fallback: string): Error {
  const serverMessage = asString(data.message) || asString(data.error) || asString(data.detail);
  if (serverMessage) return new Error(serverMessage);
  if (status === 0 || status >= 500) return new Error('服务暂时不可用，请确认后端已启动后重试。');
  if (status === 409) return new Error('该工作区或账号已存在，请更换后重试，或直接登录。');
  if (status === 400 || status === 422) return new Error('提交信息不符合要求，请检查后重试。');
  if (status === 401 || status === 403) return new Error('账号或密码不正确，请确认后重试。');
  return new Error(fallback);
}

export function authStorageEvent(): void {
  window.dispatchEvent(new Event('qualibug-auth-change'));
}

function markValidatedSession(active: boolean): void {
  if (active) sessionStorage.setItem(SESSION_MARKER_KEY, '1');
  else sessionStorage.removeItem(SESSION_MARKER_KEY);
}

let projectsCache: Promise<CustomerWorkspace[]> | null = null;
let sessionCache: Promise<SessionResult | null> | null = null;

function clearAccountCaches(): void {
  projectsCache = null;
  sessionCache = null;
}

function devToken(): string {
  return devTokenEnabled() ? localStorage.getItem(DEV_TOKEN_KEY) || '' : '';
}

function authHeaders(init?: HeadersInit): Headers {
  const headers = new Headers(init);
  const token = devToken();
  if (token) headers.set('Authorization', `Bearer ${token}`);
  return headers;
}

export async function getSession(options?: { force?: boolean }): Promise<SessionResult | null> {
  if (options?.force) sessionCache = null;
  if (!sessionCache) {
    sessionCache = fetch('/api/auth/session', {
      method: 'GET',
      headers: authHeaders(),
      credentials: 'include',
      cache: 'no-store',
    }).then(async (response) => {
      if (response.status === 401 || response.status === 403) {
        markValidatedSession(false);
        return null;
      }
      const { data, rawText } = await readResponsePayload(response);
      if (!response.ok) throw new ApiError(response.status, parseApiErrorMessage(response.status, rawText));
      if (!asBoolean(data.authenticated)) {
        markValidatedSession(false);
        return null;
      }
      const session: SessionResult = {
        authenticated: true,
        tenantId: asString(data.tenant_id),
        username: asString(data.username),
        role: asString(data.role),
        authType: asString(data.auth_type),
      };
      markValidatedSession(true);
      return session;
    }).catch((error: unknown) => {
      sessionCache = null;
      throw error;
    });
  }
  return sessionCache;
}

async function ensureAuth(): Promise<SessionResult> {
  const session = await getSession();
  if (!session) throw new Error('未登录或会话已失效，请重新登录。');
  return session;
}

export async function loginDetailed(username: string, password: string): Promise<LoginResult | null> {
  let response: Response;
  try {
    response = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      cache: 'no-store',
      body: JSON.stringify({ username, password }),
    });
  } catch {
    throw new Error('无法连接服务，请确认后端已启动后重试。');
  }
  const { data } = await readResponsePayload(response);
  if (!response.ok) throw authFacingError(response.status, data, '登录失败，请确认账号密码后重试。');
  if (!asBoolean(data.ok)) return null;
  clearAccountCaches();
  const session = await getSession({ force: true });
  if (!session) throw new Error('登录 Cookie 未建立，请检查 HTTPS、域名和浏览器 Cookie 策略。');
  authStorageEvent();
  return {
    ok: true,
    token: '',
    tenantId: session.tenantId || asString(data.tenant_id) || username,
    username: session.username || asString(data.username) || username,
    role: session.role || asString(data.role),
  };
}

export async function login(username: string, password: string): Promise<boolean> {
  return Boolean((await loginDetailed(username, password))?.ok);
}

export async function register({ tenantId, name, username, password }: {
  tenantId: string;
  name: string;
  username: string;
  password: string;
  role?: string;
}): Promise<RegisterResult | null> {
  let response: Response;
  try {
    response = await fetch('/api/tenants/create', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      cache: 'no-store',
      body: JSON.stringify({ tenant_id: tenantId, name, username, password }),
    });
  } catch {
    throw new Error('无法连接服务，请确认后端已启动后重试。');
  }
  const { data } = await readResponsePayload(response);
  if (!response.ok) throw authFacingError(response.status, data, '创建工作区失败，请稍后重试。');
  if (!asBoolean(data.ok)) return null;
  clearAccountCaches();
  return {
    ok: true,
    tenantId: asString(data.tenant_id) || tenantId,
    username: asString(data.username) || username,
    role: asString(data.role),
  };
}

export async function resetPassword({ tenantId, username, currentPassword, newPassword }: {
  tenantId: string;
  username: string;
  currentPassword?: string;
  newPassword: string;
}): Promise<{ ok: boolean; tenantId: string; username: string } | null> {
  let response: Response;
  try {
    response = await fetch('/api/auth/password/reset', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      cache: 'no-store',
      body: JSON.stringify({
        tenant_id: tenantId,
        username,
        current_password: currentPassword || '',
        new_password: newPassword,
      }),
    });
  } catch {
    throw new Error('无法连接服务，请确认后端已启动后重试。');
  }
  const { data } = await readResponsePayload(response);
  if (!response.ok) throw authFacingError(response.status, data, '密码变更失败，请确认当前密码后重试。');
  if (!asBoolean(data.ok)) return null;
  clearAccountCaches();
  markValidatedSession(false);
  authStorageEvent();
  return {
    ok: true,
    tenantId: asString(data.tenant_id) || tenantId,
    username: asString(data.username) || username,
  };
}

export function currentToken(): string {
  return devToken();
}

export function clearDevToken(): void {
  localStorage.removeItem(DEV_TOKEN_KEY);
}

export function setAuthenticatedToken(token: string): void {
  if (!devTokenEnabled()) throw new Error('生产环境禁止把认证 Token 写入浏览器存储。');
  localStorage.setItem(DEV_TOKEN_KEY, token);
  clearAccountCaches();
  authStorageEvent();
}

export function hasUsableAuth(): boolean {
  return sessionStorage.getItem(SESSION_MARKER_KEY) === '1' || Boolean(devToken());
}

export async function logout(): Promise<void> {
  try {
    await fetch('/api/auth/logout', {
      method: 'POST',
      headers: authHeaders(),
      credentials: 'include',
      cache: 'no-store',
    });
  } finally {
    clearDevToken();
    clearAccountCaches();
    markValidatedSession(false);
    authStorageEvent();
  }
}

export function isAuthenticated(): boolean {
  return hasUsableAuth();
}

export async function fetchWithTenant(url: string, init?: RequestInit): Promise<unknown> {
  await ensureAuth();
  const response = await fetch(url, {
    ...init,
    headers: authHeaders(init?.headers),
    credentials: 'include',
    cache: init?.cache || 'no-store',
  });
  if (response.status === 401 || response.status === 403) {
    clearAccountCaches();
    markValidatedSession(false);
    authStorageEvent();
  }
  if (!response.ok) throw new ApiError(response.status, parseApiErrorMessage(response.status, await response.text()));
  return response.json() as Promise<unknown>;
}

/**
 * 统一的受认证 fetch 原语：为请求补充认证头（dev token）与 Cookie，
 * 并在 401/403 时统一清会话缓存并广播 auth-change。返回原始 Response，
 * 由调用方自行解析响应体（供需要自定义错误语义的业务模块复用），
 * 避免每个模块重复实现 Authorization 头与 401 会话失效处理。
 */
export async function fetchWithAuth(url: string, init?: RequestInit): Promise<Response> {
  const response = await fetch(url, {
    ...init,
    headers: authHeaders(init?.headers),
    credentials: 'include',
    cache: init?.cache || 'no-store',
  });
  if (response.status === 401 || response.status === 403) {
    clearAccountCaches();
    markValidatedSession(false);
    authStorageEvent();
  }
  return response;
}

export function fetchJSON<T>(url: string, init?: RequestInit): Promise<T> {
  return fetchWithTenant(url, init) as Promise<T>;
}

export async function fetchPublicJSON<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, { ...init, credentials: 'include', cache: init?.cache || 'no-store' });
  if (!response.ok) throw new ApiError(response.status, parseApiErrorMessage(response.status, await response.text()));
  return response.json() as Promise<T>;
}

async function listProjects(): Promise<CustomerWorkspace[]> {
  if (!projectsCache) {
    projectsCache = fetchJSON<unknown>(`${API_V1_BASE}/projects`)
      .then((payload) => asArray(asRecord(payload).data).map(toProjectSummary))
      .catch((error: unknown) => {
        projectsCache = null;
        throw error;
      });
  }
  return projectsCache;
}

export async function getProjects(options?: { force?: boolean }): Promise<CustomerWorkspace[]> {
  if (options?.force) projectsCache = null;
  return listProjects();
}

export async function resolveProjectId(projectId: string): Promise<string> {
  const normalized = projectId.trim();
  if (!normalized) return '';
  const projects = await listProjects();
  return projects.some((item) => item.project_id === normalized) ? normalized : '';
}
