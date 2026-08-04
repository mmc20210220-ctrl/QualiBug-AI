import { asArray, asRecord, asString } from '../lib/value-guards';

export const API_BASE = '/api';

const API_V1_BASE = '/api/v1';
const DEV_TOKEN_KEY = 'qualibug_dev_token';
const SESSION_MARKER_KEY = 'qualibug_validated_session';
type JsonRecord = Record<string, unknown>;

const devTokenEnabled = (): boolean => import.meta.env.DEV && import.meta.env.VITE_QUALIBUG_ENABLE_DEV_TOKEN === 'true';

export type LoginResult = {
  ok: boolean;
  token: string;
  tenantId: string;
  username: string;
  role: string;
};

export type SessionResult = {
  authenticated: boolean;
  tenantId: string;
  username: string;
  role: string;
  authType: string;
};

export type RegisterResult = {
  ok: boolean;
  tenantId: string;
  username: string;
  role: string;
};

export type CustomerWorkspace = {
  project_id?: string;
  customer_name?: string;
  project_name?: string;
  system_name?: string;
  industry?: string;
};

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

export type CampaignGovernance = {
  campaign_id?: string;
  campaign_status?: 'active' | 'completed' | 'coverage_deferred' | 'blocked' | string;
  scope_id?: string;
  environment_ref?: string;
  source_id?: string;
  source_hash?: string;
  source_snapshot_hash?: string;
  round_count?: number;
  slice_budget?: number;
  automatic_round_limit?: number;
  attempted_slice_count?: number;
  confirmed_slice_count?: number;
  current_campaign_confirmed_slice_count?: number;
  current_campaign_customer_ready_defect_count?: number;
  current_campaign_bundle_finding_count_raw?: number;
  family_customer_ready_defect_count?: number;
  family_report_real_finding_count?: number;
  family_historical_carryover_defect_count?: number;
  confirmed_shelf_alignment_status?: string;
  confirmed_shelf_reporting_scope?: string;
  coverage_deferred_reason?: string;
  next_campaign_reason?: string;
};

export type TestDataPlan = {
  status?: 'ready' | 'blocked_with_testability_gap' | string;
  strategy?: string;
  missing_requirements?: string[];
  coverage_gaps?: Array<{ kind?: string; code?: string; campaign_id?: string }>;
};

export type V12ScanResult = {
  ok: boolean;
  scan_id?: string;
  grade?: string;
  score?: number;
  coverage?: number;
  total_findings?: number;
  total_ms?: number;
  execution_status?: string;
  layers?: Record<string, { findings: number; ms: number; tool?: string }>;
  spectrum?: { capabilities_run: number; total_findings: number };
  auto_har?: Record<string, unknown>;
  campaign?: CampaignGovernance;
  coverage_gaps?: Array<Record<string, unknown>>;
  runtime_contract?: Record<string, unknown>;
  test_data_plan?: TestDataPlan;
  report_path?: string;
  error?: string;
  message?: string;
};

export type RegressionRunResult = {
  ok: boolean;
  project_id?: string;
  mode?: 'smoke' | 'release' | 'full' | string;
  summary?: {
    generated_at?: string;
    suite_mode?: string;
    suite_mode_label?: string;
    total_probe_count?: number;
    executed_count?: number;
    passed_count?: number;
    failed_count?: number;
    needs_review_count?: number;
    skipped_count?: number;
  };
  ci_feedback?: {
    gate_status?: string;
    ci_message?: string;
    exit_code?: number;
    reopen_issue_ids?: string[];
  };
  regression_summary?: Record<string, unknown>;
  failures?: Array<Record<string, unknown>>;
  artifacts?: Record<string, string>;
  governance?: {
    dry_run?: boolean;
    allow_destructive_execution?: boolean;
    safe_by_default?: boolean;
  };
  error?: string;
  message?: string;
};

function asBoolean(value: unknown, fallback = false): boolean {
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

function parseApiErrorMessage(status: number, text: string): string {
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

async function readResponsePayload(response: Response): Promise<{ data: JsonRecord; rawText: string }> {
  const rawText = await response.text();
  if (!rawText.trim()) return { data: {}, rawText: '' };
  try {
    return { data: asRecord(JSON.parse(rawText)), rawText };
  } catch {
    return { data: {}, rawText };
  }
}

function authFacingError(status: number, data: JsonRecord, fallback: string): Error {
  const serverMessage = asString(data.message) || asString(data.error) || asString(data.detail);
  if (serverMessage) return new Error(serverMessage);
  if (status === 0 || status >= 500) return new Error('服务暂时不可用，请确认后端已启动后重试。');
  if (status === 409) return new Error('该工作区或账号已存在，请更换后重试，或直接登录。');
  if (status === 400 || status === 422) return new Error('提交信息不符合要求，请检查后重试。');
  if (status === 401 || status === 403) return new Error('账号或密码不正确，请确认后重试。');
  return new Error(fallback);
}

function authStorageEvent(): void {
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
      if (!response.ok) throw new Error(parseApiErrorMessage(response.status, rawText));
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

export { authStorageEvent };

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

async function fetchWithTenant(url: string, init?: RequestInit): Promise<unknown> {
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
  if (!response.ok) throw new Error(parseApiErrorMessage(response.status, await response.text()));
  return response.json() as Promise<unknown>;
}

function fetchJSON<T>(url: string, init?: RequestInit): Promise<T> {
  return fetchWithTenant(url, init) as Promise<T>;
}

async function fetchPublicJSON<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, { ...init, credentials: 'include', cache: init?.cache || 'no-store' });
  if (!response.ok) throw new Error(parseApiErrorMessage(response.status, await response.text()));
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

async function resolveProjectId(projectId: string): Promise<string> {
  const normalized = projectId.trim();
  if (!normalized) return '';
  const projects = await listProjects();
  return projects.some((item) => item.project_id === normalized) ? normalized : '';
}

function emptyFindingsSnapshot(projectId: string): JsonRecord {
  return {
    resolvedProjectId: projectId,
    projectId,
    projectName: projectId,
    status: 'idle',
    updatedAt: '',
    risks: [],
    scan_meta: {},
    value_metrics: {},
    executive_summary: {},
    knowledge_summary: {},
    campaign: {},
    coverage_gaps: [],
  };
}

export async function getFindings(projectId: string): Promise<JsonRecord> {
  const resolvedProjectId = await resolveProjectId(projectId);
  if (!resolvedProjectId) return emptyFindingsSnapshot('');
  try {
    const envelope = await fetchJSON<unknown>(`${API_V1_BASE}/projects/${encodeURIComponent(resolvedProjectId)}/command-center`);
    return { resolvedProjectId, projectId: resolvedProjectId, ...asRecord(asRecord(envelope).data) };
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : String(error);
    if (message.includes('404')) return emptyFindingsSnapshot(resolvedProjectId);
    throw error;
  }
}

export async function replayFinding(projectId: string, findingId: string, baseUrl = ''): Promise<unknown> {
  const resolvedProjectId = await resolveProjectId(projectId);
  if (!resolvedProjectId || !findingId.trim()) return { ok: false, error: '缺少 project_id 或 finding_id' };
  return fetchJSON<unknown>(`${API_V1_BASE}/replay`, {
    method: 'POST',
    body: JSON.stringify({
      project_id: resolvedProjectId,
      finding_id: findingId.trim(),
      ...(baseUrl.trim() ? { base_url: baseUrl.trim() } : {}),
    }),
  });
}

export async function getKnowledgeAsset(projectId: string): Promise<unknown> {
  const resolvedProjectId = await resolveProjectId(projectId);
  if (!resolvedProjectId) return { knowledge_asset: { project_id: '', sources: [] } };
  return fetchJSON<unknown>(`${API_BASE}/knowledge/asset?project=${encodeURIComponent(resolvedProjectId)}`);
}

export function getKnowledgePreview(sourceId: string): Promise<unknown> {
  return fetchJSON<unknown>(`${API_BASE}/knowledge/preview?source_id=${encodeURIComponent(sourceId)}`);
}

export function evidenceArtifactUrl(projectId: string, ref: string): string {
  return `${API_BASE}/evidence/artifact?project=${encodeURIComponent(projectId)}&ref=${encodeURIComponent(ref)}`;
}

export function saveSettings(body: JsonRecord): Promise<unknown> {
  return fetchJSON<unknown>(`${API_BASE}/settings/save`, { method: 'POST', body: JSON.stringify(body) });
}

export function getHealth(): Promise<unknown> {
  return fetchPublicJSON<unknown>(`${API_BASE}/health`);
}

export async function listConnectors(projectId: string): Promise<ConnectorRecord[]> {
  const project = await resolveProjectId(projectId);
  if (!project) return [];
  const payload = asRecord(await fetchJSON<unknown>(`${API_BASE}/connectors/list?project=${encodeURIComponent(project)}`));
  return asArray(payload.connectors)
    .map(asRecord)
    .filter((item) => Boolean(asString(item.connector_id)))
    .map((item) => ({
      connector_id: asString(item.connector_id),
      kind: asString(item.kind),
      display_name: asString(item.display_name),
      enabled: asBoolean(item.enabled),
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

export function registerConnector(body: JsonRecord): Promise<unknown> {
  return fetchJSON<unknown>(`${API_BASE}/connectors/register`, { method: 'POST', body: JSON.stringify(body) });
}

export function ingestKnowledge(projectId: string, file: File, type: string): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = async () => {
      try {
        const resolvedProjectId = await resolveProjectId(projectId);
        if (!resolvedProjectId) throw new Error('未选择有效项目，无法导入资料');
        const encoded = typeof reader.result === 'string' ? reader.result.split(',')[1] : '';
        if (!encoded) throw new Error('无法读取上传资料');
        resolve(await fetchJSON<unknown>(`${API_BASE}/knowledge/ingest`, {
          method: 'POST',
          body: JSON.stringify({ project_id: resolvedProjectId, type, filename: file.name, content: encoded }),
        }));
      } catch (error: unknown) {
        reject(error);
      }
    };
    reader.onerror = () => reject(reader.error || new Error('无法读取资料'));
    reader.readAsDataURL(file);
  });
}

export function deleteKnowledge(projectId: string, sourceId: string): Promise<unknown> {
  return fetchJSON<unknown>(`${API_BASE}/knowledge/delete`, { method: 'POST', body: JSON.stringify({ project_id: projectId, source_id: sourceId }) });
}

export type ScanPreflight = {
  ok: boolean;
  ready: boolean;
  reasons: Array<{ code: string; message: string }>;
};

export async function getScanPreflight(projectId: string): Promise<ScanPreflight> {
  const resolvedProjectId = await resolveProjectId(projectId);
  if (!resolvedProjectId) return { ok: false, ready: false, reasons: [{ code: 'NO_PROJECT', message: '未选择有效项目，无法运行检测。' }] };
  const payload = asRecord(await fetchJSON<unknown>(`${API_V1_BASE}/scan/preflight?project=${encodeURIComponent(resolvedProjectId)}`));
  return {
    ok: asBoolean(payload.ok, true),
    ready: asBoolean(payload.ready),
    reasons: asArray(payload.reasons).map(asRecord).map((item) => ({ code: asString(item.code), message: asString(item.message) })).filter((item) => item.code || item.message),
  };
}

export function runV12Scan(projectId: string, options?: { api_doc?: string; base_url?: string; scope_id?: string; environment_ref?: string; source_id?: string; source_hash?: string; test_data_contract?: JsonRecord }): Promise<V12ScanResult> {
  return fetchJSON<V12ScanResult>(`${API_BASE}/v1/scan`, {
    method: 'POST',
    body: JSON.stringify({
      project_id: projectId,
      api_doc: options?.api_doc || undefined,
      base_url: options?.base_url || undefined,
      scope_id: options?.scope_id || undefined,
      environment_ref: options?.environment_ref || undefined,
      source_manifest: options?.source_id || options?.source_hash ? { source_id: options?.source_id || '', source_hash: options?.source_hash || '' } : undefined,
      test_data_contract: options?.test_data_contract,
    }),
  });
}

export async function runRegression(projectId: string, options?: { mode?: 'smoke' | 'release' | 'full'; dry_run?: boolean; allow_destructive_execution?: boolean; execution_approval_id?: string }): Promise<RegressionRunResult> {
  const resolvedProjectId = await resolveProjectId(projectId);
  if (!resolvedProjectId) return { ok: false, error: '未选择有效项目，无法执行回归。' };
  return fetchJSON<RegressionRunResult>(`${API_V1_BASE}/projects/${encodeURIComponent(resolvedProjectId)}/regression/run`, {
    method: 'POST',
    body: JSON.stringify({
      project_id: resolvedProjectId,
      mode: options?.mode || 'release',
      dry_run: options?.dry_run === true,
      allow_destructive_execution: options?.allow_destructive_execution === true,
      execution_approval_id: options?.execution_approval_id || undefined,
    }),
  });
}

export function testDbConnection(dsn: string): Promise<{ ok: boolean; message?: string; error?: string; db_type?: string; host?: string; port?: number }> {
  return fetchJSON(`${API_BASE}/v1/db-test`, { method: 'POST', body: JSON.stringify({ dsn }) });
}

export function getServiceCredentials(projectId: string): Promise<unknown> {
  return fetchJSON<unknown>(`${API_BASE}/v1/services/credentials?project=${encodeURIComponent(projectId)}`);
}

export function saveServiceCredentials(body: JsonRecord): Promise<unknown> {
  return fetchJSON<unknown>(`${API_BASE}/v1/services/credentials`, { method: 'POST', body: JSON.stringify(body) });
}

export interface ProjectMetadata {
  industry?: string;
  module_scope?: string[] | string;
  production_data_exclusion?: string[] | string;
}

export function getProjectMetadata(projectId: string): Promise<ProjectMetadata> {
  return fetchJSON<ProjectMetadata>(`${API_BASE}/v1/project/metadata?project=${encodeURIComponent(projectId)}`);
}

export function saveProjectMetadata(body: JsonRecord): Promise<unknown> {
  return fetchJSON<unknown>(`${API_BASE}/v1/project/metadata`, { method: 'POST', body: JSON.stringify(body) });
}
