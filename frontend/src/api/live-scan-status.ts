export type LiveScanOwner = {
  schema?: string;
  project_id?: string;
  mode?: string;
  started_at_utc?: string;
};

export type LiveScanStatus = {
  status: string;
  message: string;
  active_scan: LiveScanOwner;
  active_scan_live: boolean;
  active_scan_elapsed_seconds: number;
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

export async function getLiveScanStatus(projectId: string): Promise<LiveScanStatus> {
  const project = projectId.trim();
  if (!project) {
    return {
      status: 'idle',
      message: '未选择有效项目。',
      active_scan: {},
      active_scan_live: false,
      active_scan_elapsed_seconds: 0,
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
  };
}
