import { currentToken, type V12ScanResult } from './client';

type JsonRecord = Record<string, unknown>;

export type RunCenterScanOptions = {
  api_doc?: string;
  base_url?: string;
  scope_id?: string;
  environment_ref?: string;
  source_id?: string;
  source_hash?: string;
  execution_mode?: 'safe_read_only' | 'approved_sandbox_write';
  test_data_contract?: JsonRecord;
  ui_upload_fixture_ids?: string[];
};

function normalizedFixtureIds(value: string[] | undefined): string[] {
  if (!Array.isArray(value)) return [];
  const seen = new Set<string>();
  const result: string[] = [];
  for (const item of value) {
    const identity = String(item || '').trim();
    if (!identity || seen.has(identity)) continue;
    if (!/^uifb_[a-f0-9]{20}$/.test(identity)) {
      throw new Error(`无效的上传 Fixture binding_ref：${identity}`);
    }
    seen.add(identity);
    result.push(identity);
  }
  if (result.length > 20) {
    throw new Error('单次扫描最多绑定 20 个上传 Fixture。');
  }
  return result;
}

function responseError(status: number, payload: unknown): Error {
  const record = payload && typeof payload === 'object' && !Array.isArray(payload)
    ? payload as Record<string, unknown>
    : {};
  const message = typeof record.message === 'string'
    ? record.message
    : typeof record.error === 'string'
      ? record.error
      : `API ${status}`;
  return new Error(message);
}

export async function runV12ScanFromRunCenter(
  projectId: string,
  options: RunCenterScanOptions = {},
): Promise<V12ScanResult> {
  const project = projectId.trim();
  if (!project) throw new Error('未选择有效项目，无法执行扫描。');
  const fixtureIds = normalizedFixtureIds(options.ui_upload_fixture_ids);
  const executionMode = fixtureIds.length
    ? 'approved_sandbox_write'
    : options.execution_mode;
  const headers = new Headers({ 'Content-Type': 'application/json' });
  const token = currentToken();
  if (token) headers.set('Authorization', `Bearer ${token}`);

  const response = await fetch('/api/v1/scan', {
    method: 'POST',
    headers,
    credentials: 'include',
    body: JSON.stringify({
      project_id: project,
      api_doc: options.api_doc || undefined,
      base_url: options.base_url || undefined,
      scope_id: options.scope_id || undefined,
      environment_ref: options.environment_ref || undefined,
      execution_mode: executionMode || undefined,
      source_manifest: options.source_id || options.source_hash
        ? { source_id: options.source_id || '', source_hash: options.source_hash || '' }
        : undefined,
      test_data_contract: options.test_data_contract,
      ui_upload_fixture_ids: fixtureIds.length ? fixtureIds : undefined,
    }),
  });

  let payload: unknown = {};
  try {
    payload = await response.json();
  } catch {
    payload = {};
  }
  if (!response.ok) throw responseError(response.status, payload);
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
    throw new Error('扫描接口返回了无效响应。');
  }
  return payload as V12ScanResult;
}
