import { asRecord, asString, asStrictNumber } from '../lib/value-guards';

export type EvidenceShareMetadata = {
  share_id: string;
  created_by: string;
  created_at: string;
  expires_at: string;
  revoked: boolean;
  active: boolean;
};

export type CreatedEvidenceShare = {
  share_id: string;
  token: string;
  created_at: string;
  expires_at: string;
  expires_unix: number;
  share_path: string;
};

export type PublicEvidenceSnapshot = {
  schema: string;
  generated_at_utc: string;
  project_name: string;
  severity: string;
  title: string;
  module: string;
  business_impact: string;
  expected: string;
  actual: string;
  evidence_quality: { label: string; score: number };
  repro_rate: number;
  reproduction_steps: string[];
  evidence_chain: Array<{ label: string; content: string; detail: string }>;
  relevant_apis: string[];
  relevant_tables: string[];
  trace_id: string;
  regression_obligations: string[];
  verification_status: string;
  handling_status: string;
  fix_version: string;
  notice: string;
};

export type ResolvedEvidenceShare = {
  share_id: string;
  expires_at: string;
  snapshot: PublicEvidenceSnapshot;
};

async function jsonPayload(response: Response): Promise<Record<string, unknown>> {
  return asRecord(await response.json().catch(() => ({})));
}

function apiError(response: Response, payload: Record<string, unknown>, fallback: string): Error {
  return new Error(asString(payload.message) || asString(payload.error) || `${fallback}：HTTP ${response.status}`);
}

export async function createEvidenceShare(
  projectId: string,
  findingPersistenceId: string,
  ttlSeconds = 24 * 60 * 60,
): Promise<CreatedEvidenceShare> {
  const response = await fetch('/api/v1/findings/evidence-shares', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    cache: 'no-store',
    body: JSON.stringify({
      project_id: projectId.trim(),
      finding_persistence_id: findingPersistenceId.trim(),
      ttl_seconds: ttlSeconds,
    }),
  });
  const payload = await jsonPayload(response);
  if (!response.ok || payload.ok !== true) throw apiError(response, payload, '分享链接创建失败');
  const share = asRecord(payload.share);
  const result: CreatedEvidenceShare = {
    share_id: asString(share.share_id),
    token: asString(share.token),
    created_at: asString(share.created_at),
    expires_at: asString(share.expires_at),
    expires_unix: asStrictNumber(share.expires_unix),
    share_path: asString(share.share_path),
  };
  if (!result.share_id || !result.token || !result.share_path) {
    throw new Error('分享接口未返回完整的一次性链接信息。');
  }
  return result;
}

export async function listEvidenceShares(
  projectId: string,
  findingPersistenceId: string,
): Promise<EvidenceShareMetadata[]> {
  const query = new URLSearchParams({
    project: projectId.trim(),
    finding_persistence_id: findingPersistenceId.trim(),
  });
  const response = await fetch(`/api/v1/findings/evidence-shares?${query.toString()}`, {
    method: 'GET',
    credentials: 'include',
    cache: 'no-store',
  });
  const payload = await jsonPayload(response);
  if (!response.ok || payload.ok !== true) throw apiError(response, payload, '分享记录读取失败');
  const rows = Array.isArray(payload.items) ? payload.items : [];
  return rows.map((value) => {
    const row = asRecord(value);
    return {
      share_id: asString(row.share_id),
      created_by: asString(row.created_by),
      created_at: asString(row.created_at),
      expires_at: asString(row.expires_at),
      revoked: row.revoked === true,
      active: row.active === true,
    };
  }).filter((row) => Boolean(row.share_id));
}

export async function revokeEvidenceShare(projectId: string, shareId: string): Promise<void> {
  const response = await fetch('/api/v1/findings/evidence-shares/revoke', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    cache: 'no-store',
    body: JSON.stringify({ project_id: projectId.trim(), share_id: shareId.trim() }),
  });
  const payload = await jsonPayload(response);
  if (!response.ok || payload.ok !== true) throw apiError(response, payload, '分享链接撤销失败');
}

export async function resolveEvidenceShare(token: string): Promise<ResolvedEvidenceShare> {
  const response = await fetch('/api/public/v1/evidence-share/resolve', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'omit',
    cache: 'no-store',
    body: JSON.stringify({ token: token.trim() }),
  });
  const payload = await jsonPayload(response);
  if (!response.ok || payload.ok !== true) throw apiError(response, payload, '分享链接读取失败');
  const snapshot = asRecord(payload.snapshot);
  const quality = asRecord(snapshot.evidence_quality);
  const chain = Array.isArray(snapshot.evidence_chain) ? snapshot.evidence_chain : [];
  return {
    share_id: asString(payload.share_id),
    expires_at: asString(payload.expires_at),
    snapshot: {
      schema: asString(snapshot.schema),
      generated_at_utc: asString(snapshot.generated_at_utc),
      project_name: asString(snapshot.project_name),
      severity: asString(snapshot.severity),
      title: asString(snapshot.title),
      module: asString(snapshot.module),
      business_impact: asString(snapshot.business_impact),
      expected: asString(snapshot.expected),
      actual: asString(snapshot.actual),
      evidence_quality: { label: asString(quality.label), score: asStrictNumber(quality.score) },
      repro_rate: asStrictNumber(snapshot.repro_rate),
      reproduction_steps: Array.isArray(snapshot.reproduction_steps) ? snapshot.reproduction_steps.map(asString).filter(Boolean) : [],
      evidence_chain: chain.map((value) => {
        const item = asRecord(value);
        return { label: asString(item.label), content: asString(item.content), detail: asString(item.detail) };
      }),
      relevant_apis: Array.isArray(snapshot.relevant_apis) ? snapshot.relevant_apis.map(asString).filter(Boolean) : [],
      relevant_tables: Array.isArray(snapshot.relevant_tables) ? snapshot.relevant_tables.map(asString).filter(Boolean) : [],
      trace_id: asString(snapshot.trace_id),
      regression_obligations: Array.isArray(snapshot.regression_obligations) ? snapshot.regression_obligations.map(asString).filter(Boolean) : [],
      verification_status: asString(snapshot.verification_status),
      handling_status: asString(snapshot.handling_status),
      fix_version: asString(snapshot.fix_version),
      notice: asString(snapshot.notice),
    },
  };
}
