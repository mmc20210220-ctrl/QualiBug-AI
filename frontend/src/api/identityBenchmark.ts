import { getSession } from './client';

const DEV_TOKEN_KEY = 'qualibug_dev_token';

export type IdentityAnnotationMention = {
  mention_ref?: string;
  raw_label?: string;
  source_id?: string;
  source_locator?: string;
  role?: string;
  scope?: Record<string, string>;
  annotation_status?: string;
  annotation_cluster_ref?: string;
};

export type IdentityAnnotationManifest = {
  schema?: string;
  manifest_id?: string;
  annotation_scope?: string;
  mention_count?: number;
  mentions?: IdentityAnnotationMention[];
  contains_product_cluster_suggestions?: boolean;
  contains_predicted_entity_ids?: boolean;
  is_ground_truth?: boolean;
  required_annotation_output_schema?: string;
};

export type IdentityBenchmarkWorkspace = {
  schema?: string;
  project_id?: string;
  manifest?: IdentityAnnotationManifest;
  benchmark?: Record<string, unknown>;
  regression?: Record<string, unknown>;
  identity_gate?: Record<string, unknown>;
  identity_quality_gate?: Record<string, unknown>;
  quality_policy?: Record<string, unknown>;
  ground_truth_summary?: Record<string, unknown>;
  history?: Record<string, unknown>;
  error_queue?: Record<string, unknown>;
  audit?: Record<string, unknown>;
  workflow?: Record<string, unknown>;
};

type ApiEnvelope<T> = { ok?: boolean; data?: T; error?: string; message?: string };

function errorMessage(status: number, payload: ApiEnvelope<unknown>): string {
  return payload.message || payload.error || `API ${status}`;
}

function controlledDevToken(): string {
  if (!import.meta.env.DEV || import.meta.env.VITE_QUALIBUG_ENABLE_DEV_TOKEN !== 'true') return '';
  return localStorage.getItem(DEV_TOKEN_KEY) || '';
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const session = await getSession();
  if (!session) throw new Error('未登录或会话已失效，请重新登录。');
  const headers = new Headers(init?.headers);
  const token = controlledDevToken();
  if (token) headers.set('Authorization', `Bearer ${token}`);
  if (init?.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  const response = await fetch(url, {
    ...init,
    credentials: 'include',
    cache: 'no-store',
    headers,
  });
  let payload: ApiEnvelope<T> = {};
  try {
    payload = (await response.json()) as ApiEnvelope<T>;
  } catch {
    payload = {};
  }
  if (!response.ok || payload.ok === false) {
    throw new Error(errorMessage(response.status, payload));
  }
  return payload.data as T;
}

function projectPath(project: string, suffix = ''): string {
  const id = encodeURIComponent(project.trim());
  return `/api/v1/projects/${id}/identity-benchmark${suffix}`;
}

export function getIdentityBenchmarkWorkspace(project: string): Promise<IdentityBenchmarkWorkspace> {
  return request<IdentityBenchmarkWorkspace>(projectPath(project));
}

export function getIdentityAnnotationManifest(project: string): Promise<IdentityAnnotationManifest> {
  return request<IdentityAnnotationManifest>(projectPath(project, '/manifest'));
}

export function importIdentityGroundTruth(
  project: string,
  manifestId: string,
  groundTruth: Record<string, unknown>,
): Promise<IdentityBenchmarkWorkspace> {
  return request<IdentityBenchmarkWorkspace>(projectPath(project, '/ground-truth'), {
    method: 'POST',
    body: JSON.stringify({ manifest_id: manifestId, ground_truth: groundTruth }),
  });
}

export function saveIdentityQualityPolicy(
  project: string,
  qualityPolicy: Record<string, unknown>,
): Promise<IdentityBenchmarkWorkspace> {
  return request<IdentityBenchmarkWorkspace>(projectPath(project, '/quality-policy'), {
    method: 'POST',
    body: JSON.stringify({ quality_policy: qualityPolicy }),
  });
}

export function runIdentityBenchmark(project: string): Promise<IdentityBenchmarkWorkspace> {
  return request<IdentityBenchmarkWorkspace>(projectPath(project, '/run'), {
    method: 'POST',
    body: JSON.stringify({}),
  });
}
