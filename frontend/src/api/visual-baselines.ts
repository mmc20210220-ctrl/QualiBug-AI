import { currentToken } from './client';
import { asNum, asRecord, asString } from '../lib/value-guards';

export const VISUAL_BASELINES_CHANGED_EVENT = 'qualibug-visual-baselines-change';

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

export type VisualBaselineRevocationResult = {
  baseline: VisualBaselineRecord;
  cascadeRevokedCount: number;
  cascadeRevokedBaselineIds: string[];
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
  data?: unknown;
  error?: string;
  message?: string;
};

function asBoolean(value: unknown): boolean {
  return value === true;
}

function optionalString(value: unknown): string | undefined {
  const text = asString(value);
  return text || undefined;
}

function notifyVisualBaselineChange(project: string): void {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new CustomEvent(VISUAL_BASELINES_CHANGED_EVENT, {
    detail: { project },
  }));
}

function toVisualBaselineRecord(value: unknown): VisualBaselineRecord | null {
  const row = asRecord(value);
  const baselineId = asString(row.baseline_id);
  const ref = asString(row.ref);
  const sha256 = asString(row.sha256);
  if (!baselineId || !ref || !sha256) return null;
  return {
    baseline_id: baselineId,
    ref,
    namespace: asString(row.namespace),
    status: asString(row.status),
    authority: asString(row.authority),
    sha256,
    size_bytes: asNum(row.size_bytes),
    image_width: asNum(row.image_width),
    image_height: asNum(row.image_height),
    viewport_width: asNum(row.viewport_width),
    viewport_height: asNum(row.viewport_height),
    full_page: asBoolean(row.full_page),
    renderer_profile: asString(row.renderer_profile),
    scroll_origin: asString(row.scroll_origin),
    font_readiness: asString(row.font_readiness),
    created_at_utc: asString(row.created_at_utc),
    created_by: asString(row.created_by),
    approved_from_baseline_id: optionalString(row.approved_from_baseline_id),
    revoked_at_utc: optionalString(row.revoked_at_utc),
    revoked_by: optionalString(row.revoked_by),
    revocation_reason: optionalString(row.revocation_reason),
    raw_pixels_embedded_in_registry: row.raw_pixels_embedded_in_registry === true,
  };
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
    const parsed = asRecord(JSON.parse(raw));
    payload = {
      ok: parsed.ok === true,
      data: parsed.data,
      error: asString(parsed.error),
      message: asString(parsed.message),
    };
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
  const data = asRecord(payload.data);
  const summary = asRecord(data.summary);
  const baselines = Array.isArray(data.baselines)
    ? data.baselines
      .map(toVisualBaselineRecord)
      .filter((row): row is VisualBaselineRecord => row !== null)
    : [];
  return {
    ok: data.ok !== false,
    schema_version: asString(data.schema_version),
    project_id: asString(data.project_id) || project,
    baselines,
    summary: {
      active_count: asNum(summary.active_count),
      revoked_count: asNum(summary.revoked_count),
      source_registered_count: asNum(summary.source_registered_count),
      approved_copy_count: asNum(summary.approved_copy_count),
    },
    raw_pixels_embedded: data.raw_pixels_embedded === true,
  };
}

function requiredBaselineFromPayload(payload: VisualBaselineEnvelope, message: string): VisualBaselineRecord {
  const baseline = toVisualBaselineRecord(asRecord(payload.data).baseline);
  if (!baseline) throw new Error(message);
  return baseline;
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
  const baseline = requiredBaselineFromPayload(payload, '后端未返回有效视觉基线记录。');
  notifyVisualBaselineChange(input.project);
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
  const baseline = requiredBaselineFromPayload(payload, '后端未返回有效审批记录。');
  notifyVisualBaselineChange(project);
  return baseline;
}

export async function revokeVisualBaseline(
  project: string,
  baselineId: string,
  reason: string,
): Promise<VisualBaselineRevocationResult> {
  const payload = await visualBaselineRequest(project, {
    method: 'POST',
    body: JSON.stringify({
      action: 'revoke',
      project_id: project,
      baseline_id: baselineId,
      reason,
    }),
  });
  const data = asRecord(payload.data);
  const baseline = requiredBaselineFromPayload(payload, '后端未返回有效撤销记录。');
  const cascadeRevokedBaselineIds = Array.isArray(data.cascade_revoked_baseline_ids)
    ? data.cascade_revoked_baseline_ids
      .map(asString)
      .filter(Boolean)
    : [];
  const declaredCount = Math.max(0, Math.trunc(asNum(data.cascade_revoked_count)));
  const cascadeRevokedCount = Math.max(
    declaredCount,
    cascadeRevokedBaselineIds.length,
  );
  notifyVisualBaselineChange(project);
  return {
    baseline,
    cascadeRevokedCount,
    cascadeRevokedBaselineIds,
  };
}
