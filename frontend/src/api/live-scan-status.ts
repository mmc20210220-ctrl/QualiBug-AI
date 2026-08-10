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

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function asText(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function asNumber(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0;
}

function asStageStatus(value: unknown): ScanStageStatus {
  const status = asText(value);
  if (status === 'active' || status === 'completed' || status === 'failed' || status === 'blocked' || status === 'unreported') {
    return status;
  }
  return 'pending';
}

function parseStageProgress(value: unknown): ScanStageProgress | null {
  const payload = asRecord(value);
  if (asText(payload.schema) !== 'qualibug.scan-stage-progress.v1') return null;
  const rawStages = asRecord(payload.stages);
  const stages: Record<string, ScanStageProgressItem> = {};
  for (const [key, raw] of Object.entries(rawStages)) {
    const item = asRecord(raw);
    stages[key] = {
      status: asStageStatus(item.status),
      started_at_utc: asText(item.started_at_utc),
      finished_at_utc: asText(item.finished_at_utc),
      detail: asText(item.detail),
    };
  }
  return {
    schema: 'qualibug.scan-stage-progress.v1',
    project_id: asText(payload.project_id),
    started_at_utc: asText(payload.started_at_utc),
    updated_at_utc: asText(payload.updated_at_utc),
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

  const response = await fetch('/api/v1/continuous/status', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    cache: 'no-store',
    body: JSON.stringify({ project_id: project }),
  });
  if (!response.ok) {
    throw new Error(`运行状态读取失败：HTTP ${response.status}`);
  }

  const payload = asRecord(await response.json());
  const owner = asRecord(payload.active_scan);
  return {
    status: asText(payload.status) || 'idle',
    message: asText(payload.message),
    active_scan: {
      schema: asText(owner.schema) || undefined,
      project_id: asText(owner.project_id) || undefined,
      mode: asText(owner.mode) || undefined,
      started_at_utc: asText(owner.started_at_utc) || undefined,
    },
    active_scan_live: payload.active_scan_live === true,
    active_scan_elapsed_seconds: asNumber(payload.active_scan_elapsed_seconds),
    scan_stage_progress: parseStageProgress(payload.scan_stage_progress),
  };
}
