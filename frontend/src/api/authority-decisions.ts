import { fetchWithAuth } from './client';
import { asArray, asRecord, asString } from '../lib/value-guards';

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
  actor_name: string;
  actor_role: string;
  selected_source_ref: {
    fact_id: string;
    source_id: string;
    source_locator: string;
    quote_hash: string;
    document_version: string;
  };
};

export type AuthorityAuditReceipt = {
  audit_receipt_id: string;
  decision_id: string;
  conflict_id: string;
  action: string;
  selected_fact_id: string;
  decided_at_utc: string;
  rationale: string;
  actor_name: string;
  actor_role: string;
};

export type AuthorityDecisionSubmitResult = {
  decision: AuthorityDecisionRecord;
  audit_receipt_id: string;
  comprehension_entry_allowed: boolean | null;
  understanding_gate_status: string;
  ledger_decision_count: number;
};

export type AuthorityDecisionLedger = {
  decisions: AuthorityDecisionRecord[];
  auditReceipts: AuthorityAuditReceipt[];
  updatedAtUtc: string;
  decisionCount: number;
};

type AuthorityDecisionEnvelope = {
  ok?: boolean;
  data?: unknown;
  error?: string;
  message?: string;
};

function asBoolean(value: unknown): boolean | null {
  if (value === true) return true;
  if (value === false) return false;
  return null;
}

function notifyAuthorityDecisionChange(project: string): void {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new CustomEvent(AUTHORITY_DECISIONS_CHANGED_EVENT, {
    detail: { project },
  }));
}

function mapDecision(value: unknown): AuthorityDecisionRecord {
  const decision = asRecord(value);
  const actor = asRecord(decision.actor);
  const selectedSourceRef = asRecord(decision.selected_source_ref);
  return {
    decision_id: asString(decision.decision_id),
    conflict_id: asString(decision.conflict_id),
    action: asString(decision.action),
    status: asString(decision.status),
    selected_fact_id: asString(decision.selected_fact_id),
    authority_source_id: asString(decision.authority_source_id),
    decided_at_utc: asString(decision.decided_at_utc),
    rationale: asString(decision.rationale),
    audit_receipt_id: asString(decision.audit_receipt_id),
    actor_name: asString(actor.name),
    actor_role: asString(actor.role),
    selected_source_ref: {
      fact_id: asString(selectedSourceRef.fact_id),
      source_id: asString(selectedSourceRef.source_id),
      source_locator: asString(selectedSourceRef.source_locator),
      quote_hash: asString(selectedSourceRef.quote_hash),
      document_version: asString(selectedSourceRef.document_version),
    },
  };
}

function mapAuditReceipt(value: unknown): AuthorityAuditReceipt {
  const receipt = asRecord(value);
  const actor = asRecord(receipt.actor);
  return {
    audit_receipt_id: asString(receipt.audit_receipt_id),
    decision_id: asString(receipt.decision_id),
    conflict_id: asString(receipt.conflict_id),
    action: asString(receipt.action),
    selected_fact_id: asString(receipt.selected_fact_id),
    decided_at_utc: asString(receipt.decided_at_utc),
    rationale: asString(receipt.rationale),
    actor_name: asString(actor.name),
    actor_role: asString(actor.role),
  };
}

async function authorityDecisionRequest(
  project: string,
  init?: RequestInit,
): Promise<AuthorityDecisionEnvelope> {
  const headers = new Headers(init?.headers);
  if (init?.body) headers.set('Content-Type', 'application/json');
  const response = await fetchWithAuth(
    `/api/v1/projects/${encodeURIComponent(project)}/authority-decisions`,
    { ...init, headers },
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

export async function listAuthorityDecisions(project: string): Promise<AuthorityDecisionLedger> {
  const payload = await authorityDecisionRequest(project, { method: 'GET' });
  const data = asRecord(payload.data);
  const decisions = asArray(data.decisions).map(mapDecision);
  const auditReceipts = asArray(data.audit_receipts).map(mapAuditReceipt);
  return {
    decisions,
    auditReceipts,
    updatedAtUtc: asString(data.updated_at_utc),
    decisionCount: decisions.length,
  };
}

export async function submitAuthorityDecision(input: {
  project: string;
  conflictId: string;
  action: AuthorityDecisionAction;
  selectedFactId?: string;
  rationale?: string;
  documentVersion?: string;
}): Promise<AuthorityDecisionSubmitResult> {
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
  const decision = mapDecision(data.decision);
  const audit = asRecord(data.audit_receipt);
  const comprehensionGate = asRecord(data.comprehension_gate);
  const understandingGate = asRecord(data.understanding_gate);
  const ledger = asRecord(data.ledger);
  notifyAuthorityDecisionChange(input.project);
  return {
    decision: {
      ...decision,
      action: decision.action || input.action,
      audit_receipt_id: decision.audit_receipt_id || asString(audit.audit_receipt_id),
    },
    audit_receipt_id: asString(audit.audit_receipt_id) || decision.audit_receipt_id,
    comprehension_entry_allowed: asBoolean(comprehensionGate.entry_allowed),
    understanding_gate_status: asString(understandingGate.status),
    ledger_decision_count: Number(ledger.decision_count) || 0,
  };
}
