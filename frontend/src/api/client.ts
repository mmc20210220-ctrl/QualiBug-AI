export const API_BASE = '/api';

const API_V1_BASE = '/api/v1';
const TOKEN_KEY = 'qualibug_token';
type JsonRecord = Record<string, unknown>;
export type LoginResult = {
  ok: boolean;
  token: string;
  tenantId: string;
  role: string;
};

async function ensureAuth(): Promise<void> {
  if (localStorage.getItem(TOKEN_KEY)) return;
  const devToken = localStorage.getItem('qualibug_dev_token');
  if (devToken) {
    localStorage.setItem(TOKEN_KEY, devToken);
    return;
  }
  throw new Error(
    '未登录。请在浏览器控制台设置登录令牌，或联系管理员配置认证。\n' +
    '示例: localStorage.setItem("qualibug_dev_token", "<your-jwt-token>")'
  );
}

export async function loginDetailed(username: string, password: string): Promise<LoginResult | null> {
  const resp = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  const data = await resp.json() as Record<string, unknown>;
  if (data.token) {
    localStorage.setItem(TOKEN_KEY, data.token as string);
    authStorageEvent();
    return {
      ok: Boolean(data.ok ?? true),
      token: String(data.token),
      tenantId: String(data.tenant_id || data.tenantId || username),
      role: String(data.role || ''),
    };
  }
  if (!resp.ok) throw new Error(asString(data.message) || asString(data.error) || `HTTP ${resp.status}`);
  return null;
}

export async function login(username: string, password: string): Promise<boolean> {
  const result = await loginDetailed(username, password);
  return Boolean(result?.token);
}

export type RegisterResult = {
  ok: boolean;
  tenantId: string;
  username: string;
  role: string;
};

export async function register({
  tenantId,
  name,
  username,
  password,
  role = 'admin',
}: {
  tenantId: string;
  name: string;
  username: string;
  password: string;
  role?: string;
}): Promise<RegisterResult | null> {
  const resp = await fetch('/api/tenants/create', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      tenant_id: tenantId,
      name,
      username,
      password,
      role: role || 'admin',
    }),
  });
  const data = await resp.json() as Record<string, unknown>;
  if (!resp.ok) throw new Error(asString(data.message) || asString(data.error) || `HTTP ${resp.status}`);
  if (data.ok) {
    return {
      ok: true,
      tenantId: String(data.tenant_id || tenantId),
      username: String(data.username || username),
      role: String(data.role || 'admin'),
    };
  }
  return null;
}

export function currentToken(): string {
  return localStorage.getItem(TOKEN_KEY) || '';
}

export function clearDevToken(): void {
  localStorage.removeItem('qualibug_dev_token');
}

export function authStorageEvent(): void {
  window.dispatchEvent(new Event('qualibug-auth-change'));
}

export function setAuthenticatedToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
  authStorageEvent();
}

export function hasUsableAuth(): boolean {
  if (currentToken()) return true;
  if (localStorage.getItem('qualibug_dev_token')) return true;
  return false;
}

export function logout(): void {
  localStorage.removeItem(TOKEN_KEY);
  clearDevToken();
  authStorageEvent();
}

export function isAuthenticated(): boolean {
  return hasUsableAuth();
}

async function fetchWithTenant(url: string, init?: RequestInit): Promise<unknown> {
  await ensureAuth();
  const headers: Record<string, string> = { ...(init?.headers as Record<string, string> || {}) };
  const token = localStorage.getItem(TOKEN_KEY);
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const resp = await fetch(url, { ...init, headers, credentials: 'include' });
  if (!resp.ok) throw new Error(parseApiErrorMessage(resp.status, await resp.text()));
  return resp.json();
}

function fetchJSON<T>(url: string, init?: RequestInit): Promise<T> {
  return fetchWithTenant(url, init) as Promise<T>;
}

type ProjectSummary = {
  project_id?: string;
  customer_name?: string;
  project_name?: string;
  system_name?: string;
  industry?: string;
};
export type CustomerWorkspace = ProjectSummary;
export type ConnectorRecord = {
  connector_id: string;
  kind: string;
  display_name: string;
  enabled: boolean;
  system_name?: string;
  module_name?: string;
  endpoint_ref?: string;
  credential_ref?: string;
  external_ref?: string;
  created_at_utc?: string;
  last_sync_at_utc?: string;
  last_sync_status?: string;
};
let projectsCache: Promise<ProjectSummary[]> | null = null;

function asRecord(value: unknown): JsonRecord {
  return value && typeof value === 'object' ? (value as JsonRecord) : {};
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function asString(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function asNumber(value: unknown, fallback = 0): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}

function parseApiErrorMessage(status: number, text: string) {
  const trimmed = text.trim();
  if (!trimmed) return `API ${status}`;
  try {
    const payload = asRecord(JSON.parse(trimmed));
    const message = asString(payload.message) || asString(payload.error);
    if (message) return `API ${status}: ${message}`;
  } catch {
    // fall back to raw text
  }
  return `API ${status}: ${trimmed.slice(0, 200)}`;
}

async function listProjects() {
  if (!projectsCache) {
    projectsCache = fetchJSON<unknown>(`${API_V1_BASE}/projects`)
      .then((payload) => {
        const data = asRecord(payload).data;
        return asArray(data).map((item) => asRecord(item) as ProjectSummary);
      })
      .catch((error) => {
        projectsCache = null;
        throw error;
      });
  }
  return projectsCache;
}

export async function getProjects(options?: { force?: boolean }) {
  if (options?.force) projectsCache = null;
  return listProjects();
}

async function resolveProjectId(projectId: string) {
  const normalized = String(projectId || '').trim();
  if (!normalized) return '';
  try {
    const projects = await listProjects();
    return projects.some((item) => item.project_id === normalized) ? normalized : normalized;
  } catch {
    return normalized;
  }
}

/**
 * Fetch command-center data — pure passthrough, zero processing.
 * Backend returns display-ready JSON, frontend just passes it through.
 */
export async function getFindings(projectId: string): Promise<JsonRecord> {
  const resolvedProjectId = await resolveProjectId(projectId);
  if (!resolvedProjectId) return emptyFindingsSnapshot('');
  try {
    const snapshotEnvelope = await fetchJSON<unknown>(
      `${API_V1_BASE}/projects/${encodeURIComponent(resolvedProjectId)}/command-center`
    );
    const data = asRecord(asRecord(snapshotEnvelope).data);
    return {
      resolvedProjectId,
      projectId: resolvedProjectId,
      ...data,
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (message.includes('404')) {
      return emptyFindingsSnapshot(resolvedProjectId);
    }
    throw error;
  }
}

function emptyFindingsSnapshot(projectId: string): JsonRecord {
  return {
    resolvedProjectId: projectId,
    projectId,
    projectName: projectId,
    status: 'idle',
    updatedAt: '',
    industry: '',
    risks: [],
    scan_meta: {},
    value_metrics: {},
    executive_summary: {},
    knowledge_summary: {},
  };
}

export async function getKnowledgeAsset(projectId: string) {
  const resolvedProjectId = await resolveProjectId(projectId);
  if (!resolvedProjectId) return { knowledge_asset: { project_id: '', sources: [] } };
  return fetchJSON<unknown>(`${API_BASE}/knowledge/asset?project=${encodeURIComponent(resolvedProjectId)}`);
}

export async function getKnowledgePreview(sourceId: string) {
  return fetchJSON<unknown>(`${API_BASE}/knowledge/preview?sourceId=${encodeURIComponent(sourceId)}`);
}

export async function saveSettings(body: Record<string, unknown>) {
  return fetchJSON<unknown>(`${API_BASE}/settings/save`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export async function getHealth() {
  return fetchJSON<unknown>(`${API_BASE}/health`);
}

export async function listConnectors(projectId: string): Promise<ConnectorRecord[]> {
  const project = String(projectId || '').trim();
  if (!project) return [];
  const payload = await fetchJSON<unknown>(`${API_BASE}/connectors/list?project=${encodeURIComponent(project)}`);
  const connectors = asArray(asRecord(payload).connectors).map((item) => asRecord(item));
  return connectors
    .filter((item) => Boolean(asString(item.connector_id)))
    .map((item) => ({
      connector_id: asString(item.connector_id),
      kind: asString(item.kind),
      display_name: asString(item.display_name),
      enabled: Boolean(item.enabled),
      system_name: asString(item.system_name) || undefined,
      module_name: asString(item.module_name) || undefined,
      endpoint_ref: asString(item.endpoint_ref) || undefined,
      credential_ref: asString(item.credential_ref) || undefined,
      external_ref: asString(item.external_ref) || undefined,
      created_at_utc: asString(item.created_at_utc) || undefined,
      last_sync_at_utc: asString(item.last_sync_at_utc) || undefined,
      last_sync_status: asString(item.last_sync_status) || undefined,
    }));
}

export async function registerConnector(body: Record<string, unknown>) {
  return fetchJSON<unknown>(`${API_BASE}/connectors/register`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export async function ingestKnowledge(projectId: string, file: File, type: string) {
  return new Promise<unknown>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = async () => {
      try {
        const resolvedProjectId = await resolveProjectId(projectId);
        if (!resolvedProjectId) {
          throw new Error('未选择有效项目，无法导入资料');
        }
        const b64 = (reader.result as string).split(',')[1];
        const result = await fetchJSON<unknown>(`${API_BASE}/knowledge/ingest`, {
          method: 'POST',
          body: JSON.stringify({
            project_id: resolvedProjectId,
            type,
            filename: file.name,
            content: b64,
          }),
        });
        resolve(result);
      } catch (e) {
        reject(e);
      }
    };
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

export async function deleteKnowledge(projectId: string, sourceId: string) {
  return fetchJSON<unknown>(`${API_BASE}/knowledge/delete`, {
    method: 'POST',
    body: JSON.stringify({ project_id: projectId, source_id: sourceId }),
  });
}

// V12 Unified Scan
export type V12ScanResult = {
  ok: boolean;
  scan_id?: string;
  grade?: string;
  score?: number;
  coverage?: number;
  total_findings?: number;
  total_ms?: number;
  layers?: Record<string, { findings: number; ms: number; tool?: string }>;
  spectrum?: { capabilities_run: number; total_findings: number };
  auto_har?: Record<string, unknown>;
  report_path?: string;
  error?: string;
  message?: string;
};

export async function runV12Scan(projectId: string, options?: { api_doc?: string; base_url?: string }): Promise<V12ScanResult> {
  return fetchJSON<V12ScanResult>(`${API_BASE}/v1/scan`, {
    method: 'POST',
    body: JSON.stringify({
      project_id: projectId,
      api_doc: options?.api_doc || undefined,
      base_url: options?.base_url || undefined,
    }),
  });
}

export async function testDbConnection(dsn: string): Promise<{ ok: boolean; message?: string; error?: string; db_type?: string; host?: string; port?: number }> {
  return fetchJSON(`${API_BASE}/v1/db-test`, {
    method: 'POST',
    body: JSON.stringify({ dsn }),
  });
}

export async function getServiceCredentials(projectId: string) {
  return fetchJSON(`${API_BASE}/v1/services/credentials?project=${encodeURIComponent(projectId)}`);
}

export async function saveServiceCredentials(body: Record<string, unknown>) {
  return fetchJSON(`${API_BASE}/v1/services/credentials`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

// Replay API — real-time bug reproduction
export async function replayFinding(projectId: string, findingId: string, baseUrl?: string) {
  return fetchJSON<unknown>(`${API_BASE}/v1/replay`, {
    method: 'POST',
    body: JSON.stringify({
      project_id: projectId,
      finding_id: findingId,
      base_url: baseUrl || undefined,
    }),
  });
}
