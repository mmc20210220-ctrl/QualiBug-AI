import { currentToken } from './client';

export type VisualBaselineAuthority = 'source_registered' | 'approved_copy' | string;
export type VisualBaselineStatus = 'active' | 'revoked' | string;

export type VisualBaselineRecord = {
  baseline_id: string;
  ref: string;
  namespace: string;
  status: VisualBaselineStatus;
  authority: VisualBaselineAuthority;
  sha256: string;
  size_bytes: number;
  image_width: number;
  image_height: number;
  viewport_width: number;
  viewport_height: number;
  full_page: boolean;
  renderer_profile: string;
  scroll_origin: string;
  font_readiness: string;
  created_at_utc: string;
  created_by: string;
  approved_from_baseline_id?: string;
  revoked_at_utc?: string;
  revoked_by?: string;
  revocation_reason?: string;
  raw_pixels_embedded_in_registry?: boolean;
};

export type VisualBaselineInventory = {
  ok: boolean;
  schema_version: string;
  project_id: string;
  baselines: VisualBaselineRecord[];
  summary: {
    active_count: number;
    revoked_count: number;
    source_registered_count: number;
    approved_copy_count: number;
  };
  raw_pixels_embedded: boolean;
};

type VisualBaselineEnvelope = {
  ok?: boolean;
  data?: VisualBaselineInventory | {
    ok?: boolean;
    status?: string;
    baseline?: VisualBaselineRecord;
  };
  error?: string;
  message?: string;
};

function asRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function asString(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

async function visualBaselineRequest(
  project: string,
  init?: RequestInit,
  query = '',
): Promise<VisualBaselineEnvelope> {
  const token = currentToken();
  if (!token) throw new Error('未登录或会话已失效，请重新登录。');
  const headers = new Headers(init?.headers);
  headers.set('Authorization', `Bearer ${token}`);
  if (init?.body) headers.set('Content-Type', 'application/json');
  const response = await fetch(
    `/api/v1/projects/${encodeURIComponent(project)}/visual-baselines${query}`,
    { ...init, headers, credentials: 'include' },
  );
  const raw = await response.text();
  let payload: VisualBaselineEnvelope = {};
  try {
    payload = asRecord(JSON.parse(raw)) as VisualBaselineEnvelope;
  } catch {
    payload = {};
  }
  if (!response.ok) {
    const message = asString(payload.message)
      || asString(payload.error)
      || raw.slice(0, 200)
      || `API ${response.status}`;
    throw new Error(message);
  }
  return payload;
}

function fileAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = typeof reader.result === 'string' ? reader.result : '';
      const encoded = result.includes(',') ? result.slice(result.indexOf(',') + 1) : '';
      if (!encoded) reject(new Error('无法读取视觉基线文件。'));
      else resolve(encoded);
    };
    reader.onerror = () => reject(reader.error || new Error('无法读取视觉基线文件。'));
    reader.readAsDataURL(file);
  });
}

export async function listVisualBaselines(
  project: string,
  options?: { includeRevoked?: boolean },
): Promise<VisualBaselineInventory> {
  if (!project.trim()) {
    return {
      ok: true,
      schema_version: '',
      project_id: '',
      baselines: [],
      summary: {
        active_count: 0,
        revoked_count: 0,
        source_registered_count: 0,
        approved_copy_count: 0,
      },
      raw_pixels_embedded: false,
    };
  }
  const query = options?.includeRevoked ? '?include_revoked=true' : '';
  const payload = await visualBaselineRequest(project, undefined, query);
  const data = asRecord(payload.data) as VisualBaselineInventory;
  return {
    ok: data.ok !== false,
    schema_version: asString(data.schema_version),
    project_id: asString(data.project_id) || project,
    baselines: Array.isArray(data.baselines) ? data.baselines : [],
    summary: {
      active_count: Number(data.summary?.active_count || 0),
      revoked_count: Number(data.summary?.revoked_count || 0),
      source_registered_count: Number(data.summary?.source_registered_count || 0),
      approved_copy_count: Number(data.summary?.approved_copy_count || 0),
    },
    raw_pixels_embedded: data.raw_pixels_embedded === true,
  };
}

export async function registerVisualBaseline(input: {
  project: string;
  file: File;
  baselineName: string;
  viewportWidth: number;
  viewportHeight: number;
  fullPage: boolean;
}): Promise<VisualBaselineRecord> {
  const content = await fileAsBase64(input.file);
  const payload = await visualBaselineRequest(input.project, {
    method: 'POST',
    body: JSON.stringify({
      action: 'register',
      project_id: input.project,
      filename: input.file.name,
      content,
      baseline_name: input.baselineName,
      viewport_width: input.viewportWidth,
      viewport_height: input.viewportHeight,
      full_page: input.fullPage,
    }),
  });
  const data = asRecord(payload.data);
  const baseline = asRecord(data.baseline) as VisualBaselineRecord;
  if (!baseline.baseline_id) throw new Error('后端未返回有效视觉基线记录。');
  return baseline;
}

export async function approveVisualBaseline(
  project: string,
  baselineId: string,
): Promise<VisualBaselineRecord> {
  const payload = await visualBaselineRequest(project, {
    method: 'POST',
    body: JSON.stringify({
      action: 'approve',
      project_id: project,
      baseline_id: baselineId,
    }),
  });
  const baseline = asRecord(asRecord(payload.data).baseline) as VisualBaselineRecord;
  if (!baseline.baseline_id) throw new Error('后端未返回有效审批记录。');
  return baseline;
}

export async function revokeVisualBaseline(
  project: string,
  baselineId: string,
  reason: string,
): Promise<VisualBaselineRecord> {
  const payload = await visualBaselineRequest(project, {
    method: 'POST',
    body: JSON.stringify({
      action: 'revoke',
      project_id: project,
      baseline_id: baselineId,
      reason,
    }),
  });
  const baseline = asRecord(asRecord(payload.data).baseline) as VisualBaselineRecord;
  if (!baseline.baseline_id) throw new Error('后端未返回有效撤销记录。');
  return baseline;
}
