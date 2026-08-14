import { fetchWithAuth, getSession } from './client';

type ApiEnvelope<T> = { ok?: boolean; data?: T; error?: string; message?: string };

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

export type IdentityAnnotationTaskPackage = {
  schema?: string;
  task_package_id?: string;
  manifest_id?: string;
  annotation_scope?: string;
  task_count?: number;
  batch_count?: number;
  batch_size?: number;
  tasks?: Array<Record<string, unknown>>;
  batches?: Array<Record<string, unknown>>;
  submission_template?: Record<string, unknown>;
  instructions?: Record<string, unknown>;
  review_modes?: string[];
  contains_product_cluster_suggestions?: boolean;
  contains_predicted_entity_ids?: boolean;
  contains_similarity_candidates?: boolean;
};

export type IdentityAnnotationImportResult = {
  schema?: string;
  status?: 'IMPORTED' | 'REVIEW_REQUIRED' | string;
  project_id?: string;
  task_package_id?: string;
  manifest_id?: string;
  compilation?: Record<string, unknown>;
  workspace?: IdentityBenchmarkWorkspace;
  ground_truth_imported?: boolean;
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

function errorMessage(status: number, payload: ApiEnvelope<unknown>): string {
  return payload.message || payload.error || `API ${status}`;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const session = await getSession();
  if (!session) throw new Error('未登录或会话已失效，请重新登录。');
  const headers = new Headers(init?.headers);
  if (init?.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  const response = await fetchWithAuth(url, { ...init, headers });
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

export function getIdentityAnnotationTaskPackage(project: string): Promise<IdentityAnnotationTaskPackage> {
  return request<IdentityAnnotationTaskPackage>(projectPath(project, '/annotation-package'));
}

export function compileIdentityAnnotationSubmissions(
  project: string,
  submissions: {
    primary_submission: Record<string, unknown>;
    secondary_submission?: Record<string, unknown>;
    adjudication_submission?: Record<string, unknown>;
  },
): Promise<IdentityAnnotationImportResult> {
  return request<IdentityAnnotationImportResult>(projectPath(project, '/annotation-compile'), {
    method: 'POST',
    body: JSON.stringify(submissions),
  });
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
