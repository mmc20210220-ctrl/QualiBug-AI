import { currentToken } from './client';

export const AUTHORITY_DECISIONS_CHANGED_EVENT = 'qualibug-authority-decisions-change';

export type AuthorityDecisionAction = 'SELECT_FACT' | 'LEAVE_UNRESOLVED';

export type AuthorityDecisionRecord = {
  decision_id: string;
  conflict_id: string;
  action: AuthorityDecisionAction | string;
  status: string;
  selected_fact_id: string;
  authority_source_id: string;
  decided_at_utc: string;
  rationale: string;
  audit_receipt_id: string;
};

type AuthorityDecisionEnvelope = {
  ok?: boolean;
  data?: unknown;
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

function notifyAuthorityDecisionChange(project: string): void {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new CustomEvent(AUTHORITY_DECISIONS_CHANGED_EVENT, {
    detail: { project },
  }));
}

async function authorityDecisionRequest(
  project: string,
  init?: RequestInit,
): Promise<AuthorityDecisionEnvelope> {
  const token = currentToken();
  if (!token) throw new Error('未登录或会话已失效，请重新登录。');
  const headers = new Headers(init?.headers);
  headers.set('Authorization', `Bearer ${token}`);
  if (init?.body) headers.set('Content-Type', 'application/json');
  const response = await fetch(
    `/api/v1/projects/${encodeURIComponent(project)}/authority-decisions`,
    { ...init, headers, credentials: 'include' },
  );
  const raw = await response.text();
  let payload: AuthorityDecisionEnvelope = {};
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

export async function submitAuthorityDecision(input: {
  project: string;
  conflictId: string;
  action: AuthorityDecisionAction;
  selectedFactId?: string;
  rationale?: string;
  documentVersion?: string;
}): Promise<AuthorityDecisionRecord> {
  const payload = await authorityDecisionRequest(input.project, {
    method: 'POST',
    body: JSON.stringify({
      action: input.action,
      conflict_id: input.conflictId,
      selected_fact_id: input.selectedFactId || '',
      rationale: input.rationale || '',
      document_version: input.documentVersion || '',
    }),
  });
  const data = asRecord(payload.data);
  const decision = asRecord(data.decision);
  notifyAuthorityDecisionChange(input.project);
  return {
    decision_id: asString(decision.decision_id),
    conflict_id: asString(decision.conflict_id),
    action: asString(decision.action) || input.action,
    status: asString(decision.status),
    selected_fact_id: asString(decision.selected_fact_id),
    authority_source_id: asString(decision.authority_source_id),
    decided_at_utc: asString(decision.decided_at_utc),
    rationale: asString(decision.rationale),
    audit_receipt_id: asString(decision.audit_receipt_id),
  };
}
