import { currentToken, type V12ScanResult } from './client';
import { listUploadScenarios } from './ui-upload-scenarios';

type JsonRecord = Record<string, unknown>;

export const RUN_LIFECYCLE_EVENT = 'qualibug:run-lifecycle';

export type RunLifecycleDetail =
  | {
      phase: 'submitted';
      projectId: string;
      startedAt: number;
      executionMode?: RunCenterScanOptions['execution_mode'];
    }
  | {
      phase: 'completed';
      projectId: string;
      startedAt: number;
      finishedAt: number;
      executionMode?: RunCenterScanOptions['execution_mode'];
      executionStatus: string;
      campaignStatus: string;
      testDataStatus: string;
      totalFindings: number;
      evidenceCount: number;
      grade: string;
      coverage: number;
    }
  | {
      phase: 'failed';
      projectId: string;
      startedAt: number;
      finishedAt: number;
      executionMode?: RunCenterScanOptions['execution_mode'];
      message: string;
    };

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

function emitRunLifecycle(detail: RunLifecycleDetail): void {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new CustomEvent<RunLifecycleDetail>(RUN_LIFECYCLE_EVENT, { detail }));
}

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

async function activeApprovedScenarioIds(project: string): Promise<string[]> {
  let payload;
  try {
    payload = await listUploadScenarios(project, false);
  } catch (error) {
    const detail = error instanceof Error ? error.message : '上传场景读取失败';
    throw new Error(`无法在运行前同步活动审批上传场景：${detail}`);
  }
  const identities = payload.scenarios
    .filter((scenario) => scenario.status === 'active' && scenario.authority === 'approved_copy')
    .map((scenario) => String(scenario.scenario_ref || '').trim())
    .filter(Boolean);
  return normalizedIdentities(
    identities,
    /^uisr_[a-f0-9]{20}$/,
    '上传场景引用',
    20,
  );
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

function recordOf(value: unknown): JsonRecord {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as JsonRecord
    : {};
}

function textOf(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function numberOf(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0;
}

function evidenceCountOf(result: JsonRecord): number {
  const har = recordOf(result.auto_har);
  return Array.isArray(har.entries) ? har.entries.length : 0;
}

export async function runV12ScanFromRunCenter(
  projectId: string,
  options: RunCenterScanOptions = {},
): Promise<V12ScanResult> {
  const project = projectId.trim();
  if (!project) throw new Error('未选择有效项目，无法执行扫描。');

  // An explicit read-only request is the operator kill switch. It must suppress
  // every governed write input before scenario/fixture resolution, rather than
  // being silently overridden by an approved background asset.
  const forceReadOnly = options.execution_mode === 'safe_read_only';
  const fixtureIds = forceReadOnly
    ? []
    : normalizedIdentities(
      options.ui_upload_fixture_ids,
      /^uifb_[a-f0-9]{20}$/,
      '上传 Fixture binding_ref',
      20,
    );
  const scenarioIds = forceReadOnly
    ? []
    : options.ui_upload_scenario_ids === undefined
      ? await activeApprovedScenarioIds(project)
      : normalizedIdentities(
        options.ui_upload_scenario_ids,
        /^(?:uisr|uisa)_[a-f0-9]{20}$/,
        '上传场景引用',
        20,
      );
  const executionMode = forceReadOnly
    ? 'safe_read_only'
    : fixtureIds.length || scenarioIds.length
      ? 'approved_sandbox_write'
      : options.execution_mode;
  const headers = new Headers({ 'Content-Type': 'application/json' });
  const token = currentToken();
  if (token) headers.set('Authorization', `Bearer ${token}`);

  const startedAt = Date.now();
  emitRunLifecycle({
    phase: 'submitted',
    projectId: project,
    startedAt,
    executionMode,
  });

  try {
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

    const result = payload as V12ScanResult;
    const record = recordOf(payload);
    const campaign = recordOf(record.campaign);
    const testDataPlan = recordOf(record.test_data_plan);
    emitRunLifecycle({
      phase: 'completed',
      projectId: project,
      startedAt,
      finishedAt: Date.now(),
      executionMode,
      executionStatus: textOf(record.execution_status),
      campaignStatus: textOf(campaign.campaign_status),
      testDataStatus: textOf(testDataPlan.status),
      totalFindings: numberOf(record.total_findings),
      evidenceCount: evidenceCountOf(record),
      grade: textOf(record.grade),
      coverage: numberOf(record.coverage),
    });
    return result;
  } catch (error) {
    emitRunLifecycle({
      phase: 'failed',
      projectId: project,
      startedAt,
      finishedAt: Date.now(),
      executionMode,
      message: error instanceof Error ? error.message : '扫描请求失败',
    });
    throw error;
  }
}
