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
  ui_upload_scenario_ids?: string[];
};

function normalizedIdentities(
  value: string[] | undefined,
  pattern: RegExp,
  label: string,
  limit: number,
): string[] {
  if (!Array.isArray(value)) return [];
  const seen = new Set<string>();
  const result: string[] = [];
  for (const item of value) {
    const identity = String(item || '').trim();
    if (!identity || seen.has(identity)) continue;
    if (!pattern.test(identity)) throw new Error(`无效的${label}：${identity}`);
    seen.add(identity);
    result.push(identity);
  }
  if (result.length > limit) throw new Error(`单次扫描最多选择 ${limit} 个${label}。`);
  return result;
}

function scenarioDraft(project: string): string[] {
  try {
    const raw = globalThis.localStorage?.getItem(
      `qualibug.run.ui-upload-scenarios.${project}`,
    ) || '[]';
    const parsed = JSON.parse(raw) as unknown;
    const selected = Array.isArray(parsed)
      ? parsed.filter((item): item is string => typeof item === 'string' && Boolean(item.trim()))
      : [];
    const verified = globalThis.sessionStorage?.getItem(
      `qualibug.run.ui-upload-scenarios-verified.${project}`,
    );
    if (selected.length > 0 && verified !== 'true') {
      throw new Error('上传场景审批状态尚未完成刷新，请先刷新场景后再运行。');
    }
    return verified === 'true' ? selected : [];
  } catch (error) {
    if (error instanceof Error && error.message.includes('审批状态尚未完成刷新')) {
      throw error;
    }
    return [];
  }
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
  const fixtureIds = normalizedIdentities(
    options.ui_upload_fixture_ids,
    /^uifb_[a-f0-9]{20}$/,
    '上传 Fixture binding_ref',
    20,
  );
  const scenarioIds = normalizedIdentities(
    options.ui_upload_scenario_ids ?? scenarioDraft(project),
    /^(?:uisr|uisa)_[a-f0-9]{20}$/,
    '上传场景引用',
    20,
  );
  const executionMode = fixtureIds.length || scenarioIds.length
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
      ui_upload_scenario_ids: scenarioIds.length ? scenarioIds : undefined,
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
