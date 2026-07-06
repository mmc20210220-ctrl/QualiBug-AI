import { currentToken, hasUsableAuth } from './client';

type JsonRecord = Record<string, unknown>;

const ENTERPRISE_API_BASE = String(import.meta.env.VITE_QUALIBUG_ENTERPRISE_API_BASE || '/enterprise-api/v1').replace(/\/+$/, '');

export type SourceManifest = {
  source_id: string;
  source_hash: string;
  source_version_id?: string;
  source_origin?: string;
  source_type?: string;
  blob_ref?: string;
};

export type SourceAssetSummary = {
  source_id: string;
  source_type: string;
  latest_source_hash: string;
  latest_version_id: string;
  version_count: number;
  updated_at_utc: string;
};

export type TestDataReceipt = {
  receipt_id: string;
  kind: 'provenance' | 'creation' | 'cleanup' | 'fixture' | string;
  campaign_id: string;
  scope_id: string;
  environment_ref: string;
  data_scope_ref?: string;
  fixture_ref?: string;
  provenance_ref?: string;
  operation_ref?: string;
  issued_at_utc?: string;
};

export type ExecutionApproval = {
  approval_id: string;
  campaign_id: string;
  scope_id: string;
  environment_ref: string;
  source_hash: string;
  target_origin: string;
  execution_mode: 'safe_read_only' | 'approved_sandbox_write' | string;
  expires_at_utc: string;
};

export type ReleaseGate = {
  verdict: 'pass' | 'fail' | 'not_ready' | string;
  status: 'release_ready' | 'blocked' | 'inconclusive' | string;
  campaign_id?: string;
  campaign_status?: string;
  execution_status?: string;
  runtime_contract_status?: string;
  evidence_bundle_id?: string;
  evidence_bundle_verified?: boolean;
  reasons?: Array<{ code: string; detail: string }>;
};

export type EnterpriseScanResult = {
  success: boolean;
  scan_id: string;
  grade: string;
  total_findings: number;
  total_candidates: number;
  execution_status: string;
  campaign: JsonRecord;
  runtime_contract: JsonRecord;
  test_data_plan: JsonRecord;
  evidence_bundle: JsonRecord;
  release_gate: ReleaseGate;
  coverage_gaps: JsonRecord[];
  input_gaps: JsonRecord[];
  report_path?: string;
};

export type EnterpriseScanRequest = {
  project_id: string;
  prd_text?: string;
  api_doc_text?: string;
  base_url?: string;
  scope_id?: string;
  environment_ref?: string;
  source_manifest?: SourceManifest;
  test_data_contract?: JsonRecord;
  execution_approval_id?: string;
  execution_mode?: 'safe_read_only' | 'approved_sandbox_write';
  release_policy?: JsonRecord;
  ci_gate?: boolean;
};

function asRecord(value: unknown): JsonRecord {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as JsonRecord : {};
}

function asString(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

async function enterpriseRequest<T>(path: string, init?: RequestInit): Promise<T> {
  if (!hasUsableAuth()) throw new Error('未登录或会话已失效，请重新登录。');
  const headers = new Headers(init?.headers);
  const token = currentToken();
  if (token) headers.set('Authorization', `Bearer ${token}`);
  if (typeof init?.body === 'string' && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  const response = await fetch(`${ENTERPRISE_API_BASE}${path}`, { ...init, headers, credentials: 'include' });
  if (!response.ok) {
    const text = await response.text();
    let detail = text.slice(0, 240);
    try {
      const payload = asRecord(JSON.parse(text));
      detail = asString(payload.detail) || asString(payload.error) || detail;
    } catch {
      // Keep bounded raw response for operations diagnosis.
    }
    throw new Error(`API ${response.status}: ${detail || '请求失败'}`);
  }
  return response.json() as Promise<T>;
}

export function registerEnterpriseSourceAsset(input: {
  project_id: string;
  source_id: string;
  source_type: string;
  content: string;
  filename?: string;
  origin?: string;
  external_ref?: string;
  metadata?: JsonRecord;
}): Promise<SourceManifest> {
  return enterpriseRequest<SourceManifest>('/source-assets/register', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export async function listEnterpriseSourceAssets(projectId: string): Promise<SourceAssetSummary[]> {
  const payload = await enterpriseRequest<unknown>(`/source-assets/${encodeURIComponent(projectId)}`);
  const assets = asRecord(payload).assets;
  return Array.isArray(assets) ? assets.map(asRecord).map((asset) => ({
    source_id: asString(asset.source_id),
    source_type: asString(asset.source_type),
    latest_source_hash: asString(asset.latest_source_hash),
    latest_version_id: asString(asset.latest_version_id),
    version_count: Number(asset.version_count) || 0,
    updated_at_utc: asString(asset.updated_at_utc),
  })) : [];
}

export function issueEnterpriseTestDataReceipt(input: {
  project_id: string;
  kind: 'provenance' | 'creation' | 'cleanup' | 'fixture';
  campaign_id: string;
  scope_id: string;
  environment_ref: string;
  data_scope_ref?: string;
  fixture_ref?: string;
  provenance_ref?: string;
  operation_ref?: string;
}): Promise<TestDataReceipt> {
  return enterpriseRequest<TestDataReceipt>('/test-data-receipts', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export function issueEnterpriseExecutionApproval(input: {
  project_id: string;
  campaign_id: string;
  scope_id: string;
  environment_ref: string;
  source_hash: string;
  target_base_url: string;
  execution_mode: 'safe_read_only' | 'approved_sandbox_write';
  expires_at_utc: string;
}): Promise<ExecutionApproval> {
  return enterpriseRequest<ExecutionApproval>('/execution-approvals', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export function runEnterpriseScan(input: EnterpriseScanRequest): Promise<EnterpriseScanResult> {
  return enterpriseRequest<EnterpriseScanResult>('/scans', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export function verifyEnterpriseEvidenceBundle(projectId: string, bundleId: string): Promise<{ valid: boolean; code?: string; checked?: number; bundle_sha256?: string }> {
  return enterpriseRequest(`/evidence-bundles/${encodeURIComponent(projectId)}/${encodeURIComponent(bundleId)}/verify`);
}
