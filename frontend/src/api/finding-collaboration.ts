export type FindingHandlingStatus =
  | 'new'
  | 'triaged'
  | 'in_progress'
  | 'fix_ready'
  | 'risk_review'
  | 'false_positive_review';

export type FindingDisposition = 'none' | 'accepted_risk' | 'false_positive';

export type FindingCollaboration = {
  finding_persistence_id: string;
  verification_status: string;
  handling_status: FindingHandlingStatus;
  assignee: string;
  fix_version: string;
  developer_feedback: string;
  disposition: FindingDisposition;
  disposition_note: string;
  external_issue_url: string;
  updated_by: string;
  created_at: string;
  updated_at: string;
};

export type CollaborativeFindingProjection = {
  finding_persistence_id?: string;
  verification_status?: string;
  collaboration?: Partial<FindingCollaboration>;
};

export type FindingCollaborationPatch = Partial<Pick<
  FindingCollaboration,
  | 'handling_status'
  | 'assignee'
  | 'fix_version'
  | 'developer_feedback'
  | 'disposition'
  | 'disposition_note'
  | 'external_issue_url'
>>;

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function asText(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function asHandlingStatus(value: unknown): FindingHandlingStatus {
  const text = asText(value);
  if (
    text === 'triaged'
    || text === 'in_progress'
    || text === 'fix_ready'
    || text === 'risk_review'
    || text === 'false_positive_review'
  ) return text;
  return 'new';
}

function asDisposition(value: unknown): FindingDisposition {
  const text = asText(value);
  if (text === 'accepted_risk' || text === 'false_positive') return text;
  return 'none';
}

function parseItem(value: unknown): FindingCollaboration {
  const item = asRecord(value);
  return {
    finding_persistence_id: asText(item.finding_persistence_id),
    verification_status: asText(item.verification_status) || 'open',
    handling_status: asHandlingStatus(item.handling_status),
    assignee: asText(item.assignee),
    fix_version: asText(item.fix_version),
    developer_feedback: asText(item.developer_feedback),
    disposition: asDisposition(item.disposition),
    disposition_note: asText(item.disposition_note),
    external_issue_url: asText(item.external_issue_url),
    updated_by: asText(item.updated_by),
    created_at: asText(item.created_at),
    updated_at: asText(item.updated_at),
  };
}

export async function updateFindingCollaboration(
  projectId: string,
  findingPersistenceId: string,
  patch: FindingCollaborationPatch,
): Promise<FindingCollaboration> {
  const project = projectId.trim();
  const findingId = findingPersistenceId.trim();
  if (!project) throw new Error('缺少项目 ID，无法保存协作记录。');
  if (!findingId) throw new Error('当前问题未绑定持久化 ID，无法安全保存协作记录。');

  const response = await fetch('/api/v1/findings/collaboration', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    cache: 'no-store',
    body: JSON.stringify({
      project_id: project,
      finding_persistence_id: findingId,
      patch,
    }),
  });

  const payload = asRecord(await response.json().catch(() => ({})));
  if (!response.ok || payload.ok !== true) {
    throw new Error(
      asText(payload.message)
      || asText(payload.error)
      || `协作记录保存失败：HTTP ${response.status}`,
    );
  }
  const item = parseItem(payload.item);
  if (!item.finding_persistence_id) {
    throw new Error('协作接口未返回持久化 Finding ID。');
  }
  return item;
}
