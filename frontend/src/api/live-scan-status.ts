import { fetchWithAuth } from './client';
import { asRecord, asString, asStrictNumber } from '../lib/value-guards';

export type LiveScanOwner = {
  schema?: string;
  project_id?: string;
  mode?: string;
  started_at_utc?: string;
};

export type ScanStageStatus = 'pending' | 'active' | 'completed' | 'failed' | 'blocked' | 'unreported';

export type ScanStageProgressItem = {
  status: ScanStageStatus;
  started_at_utc: string;
  finished_at_utc: string;
  detail: string;
};

export type ScanStageProgress = {
  schema: string;
  project_id: string;
  started_at_utc: string;
  updated_at_utc: string;
  stages: Record<string, ScanStageProgressItem>;
};

export type LiveScanStatus = {
  status: string;
  message: string;
  active_scan: LiveScanOwner;
  active_scan_live: boolean;
  active_scan_elapsed_seconds: number;
  scan_stage_progress: ScanStageProgress | null;
};

function asStageStatus(value: unknown): ScanStageStatus {
  const status = asString(value);
  if (status === 'active' || status === 'completed' || status === 'failed' || status === 'blocked' || status === 'unreported') {
    return status;
  }
  return 'pending';
}

function parseStageProgress(value: unknown): ScanStageProgress | null {
  const payload = asRecord(value);
  if (asString(payload.schema) !== 'qualibug.scan-stage-progress.v1') return null;
  const rawStages = asRecord(payload.stages);
  const stages: Record<string, ScanStageProgressItem> = {};
  for (const [key, raw] of Object.entries(rawStages)) {
    const item = asRecord(raw);
    stages[key] = {
      status: asStageStatus(item.status),
      started_at_utc: asString(item.started_at_utc),
      finished_at_utc: asString(item.finished_at_utc),
      detail: asString(item.detail),
    };
  }
  return {
    schema: 'qualibug.scan-stage-progress.v1',
    project_id: asString(payload.project_id),
    started_at_utc: asString(payload.started_at_utc),
    updated_at_utc: asString(payload.updated_at_utc),
    stages,
  };
}

export async function getLiveScanStatus(projectId: string): Promise<LiveScanStatus> {
  const project = projectId.trim();
  if (!project) {
    return {
      status: 'idle',
      message: '未选择有效项目。',
      active_scan: {},
      active_scan_live: false,
      active_scan_elapsed_seconds: 0,
      scan_stage_progress: null,
    };
  }

  const response = await fetchWithAuth('/api/v1/continuous/status', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ project_id: project }),
  });
  if (!response.ok) {
    throw new Error(`运行状态读取失败：HTTP ${response.status}`);
  }

  const payload = asRecord(await response.json());
  const owner = asRecord(payload.active_scan);
  return {
    status: asString(payload.status) || 'idle',
    message: asString(payload.message),
    active_scan: {
      schema: asString(owner.schema) || undefined,
      project_id: asString(owner.project_id) || undefined,
      mode: asString(owner.mode) || undefined,
      started_at_utc: asString(owner.started_at_utc) || undefined,
    },
    active_scan_live: payload.active_scan_live === true,
    active_scan_elapsed_seconds: asStrictNumber(payload.active_scan_elapsed_seconds),
    scan_stage_progress: parseStageProgress(payload.scan_stage_progress),
  };
}
