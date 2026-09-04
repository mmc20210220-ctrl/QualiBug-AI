/**
 * 业务数据 API + 对外再导出桶。
 *
 * 本文件拆分自原 api/client.ts 的业务层：承载项目/资料/连接器/扫描/
 * 回归等业务接口；认证与传输基础设施已迁至 api/session.ts，纯类型迁至
 * api/client-types.ts。本文件保持原 client 的全部导出，对外 import 路径不变。
 */
import { asArray, asRecord, asString } from '../lib/value-guards';
import {
  API_BASE,
  API_V1_BASE,
  ApiError,
  asBoolean,
  fetchJSON,
  fetchPublicJSON,
  resolveProjectId,
} from './session';
import type {
  ConnectorRecord,
  JsonRecord,
  ProjectMetadata,
  RegressionRunResult,
  ScanPreflight,
  V12ScanResult,
} from './client-types';

// 对外再导出（保持原 api/client 的 import 路径不变）
export { API_BASE } from './session';
export {
  ApiError,
  authStorageEvent,
  clearDevToken,
  currentToken,
  fetchWithAuth,
  getProjects,
  getSession,
  hasUsableAuth,
  isAuthenticated,
  login,
  loginDetailed,
  logout,
  register,
  resetPassword,
  setAuthenticatedToken,
} from './session';
export type {
  CampaignGovernance,
  ConnectorRecord,
  CustomerWorkspace,
  LoginResult,
  ProjectMetadata,
  RegressionRunResult,
  RegisterResult,
  ScanPreflight,
  SessionResult,
  TestDataPlan,
  V12ScanResult,
} from './client-types';

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

// command-center 结果 in-flight 单飞缓存：同一 project 的并发请求合并为一次
// 网络往返（Sidebar + Topbar + 页面 hook 会同时触发 getFindings）。结果只在
// 请求 in-flight 期间共享，落地后立即释放，不引入跨轮询的陈旧数据。
const _findingsInflight = new Map<string, Promise<JsonRecord>>();

export async function getFindings(projectId: string): Promise<JsonRecord> {
  // URL 中的 project 已经是明确项目标识；读取接口由后端继续执行租户/项目校验。
  // 这里禁止先 await /api/v1/projects 再发 command-center，避免冷首屏串行瀑布。
  const project = projectId.trim();
  if (!project) return emptyFindingsSnapshot('');
  const inFlight = _findingsInflight.get(project);
  if (inFlight) return inFlight;
  const request = (async () => {
    try {
      const envelope = await fetchJSON<unknown>(`${API_V1_BASE}/projects/${encodeURIComponent(project)}/command-center`);
      return { resolvedProjectId: project, projectId: project, ...asRecord(asRecord(envelope).data) };
    } catch (error: unknown) {
      // 404 = 后端确认该项目尚无 command-center 快照：诚实空态，不是故障。
      // 其余任何失败（网络/5xx/解析）必须原样上抛，禁止吞掉或误判为空态。
      if (error instanceof ApiError && error.status === 404) {
        return emptyFindingsSnapshot(project);
      }
      throw error;
    } finally {
      _findingsInflight.delete(project);
    }
  })();
  _findingsInflight.set(project, request);
  return request;
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
  const project = projectId.trim();
  if (!project) return { knowledge_asset: { project_id: '', sources: [] } };
  return fetchJSON<unknown>(`${API_BASE}/knowledge/asset?project=${encodeURIComponent(project)}`);
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
  const project = projectId.trim();
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

export async function getScanPreflight(projectId: string): Promise<ScanPreflight> {
  const project = projectId.trim();
  if (!project) return { ok: false, ready: false, reasons: [{ code: 'NO_PROJECT', message: '未选择有效项目，无法运行检测。' }] };
  const payload = asRecord(await fetchJSON<unknown>(`${API_V1_BASE}/scan/preflight?project=${encodeURIComponent(project)}`));
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

export function getProjectMetadata(projectId: string): Promise<ProjectMetadata> {
  return fetchJSON<ProjectMetadata>(`${API_BASE}/v1/project/metadata?project=${encodeURIComponent(projectId)}`);
}

export function saveProjectMetadata(body: JsonRecord): Promise<unknown> {
  return fetchJSON<unknown>(`${API_BASE}/v1/project/metadata`, { method: 'POST', body: JSON.stringify(body) });
}
