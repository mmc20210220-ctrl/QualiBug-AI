import { currentToken, getSession } from './client';

type JsonRecord = Record<string, unknown>;

export type ConnectorAcceptanceCheck = {
  check_id: string;
  status: 'PASS' | 'FAIL' | string;
  severity: 'BLOCKER' | 'INFO' | string;
  observed?: unknown;
  expected?: unknown;
  detail?: string;
};

export type ConnectorAcceptanceSummary = {
  check_count: number;
  blocker_failure_count: number;
  executed_run_count: number;
  required_run_count: number;
  maximum_run_duration_seconds: number;
  minimum_coverage_ratio: number;
  maximum_discovered_resource_count: number;
};

export type ConnectorAcceptanceReportSummary = {
  report_id: string;
  acceptance_id: string;
  profile: string;
  verdict: 'PASS' | 'FAIL' | string;
  acceptance_ready: boolean;
  started_at_utc?: string;
  completed_at_utc?: string;
  summary: ConnectorAcceptanceSummary;
};

export type ConnectorAcceptanceReport = ConnectorAcceptanceReportSummary & {
  project_id: string;
  connector_instance_id: string;
  checks: ConnectorAcceptanceCheck[];
  governance: {
    customer_material_access?: string;
    customer_material_mutation_executed?: boolean | null;
    deletion_policy?: string;
    customer_source_content_in_report?: boolean | null;
    raw_cursor_values_in_report?: boolean | null;
    credential_values_in_report?: boolean | null;
  };
  source_content_returned: false;
  raw_cursor_returned: false;
  credential_values_returned: false;
  filesystem_path_returned: false;
};

export type ConnectorAcceptanceInventory = {
  project_id: string;
  connector_instance_id: string;
  reports: ConnectorAcceptanceReportSummary[];
  summary: {
    report_count: number;
    passing_report_count: number;
    failing_report_count: number;
  };
};

const asRecord = (value: unknown): JsonRecord => (
  value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as JsonRecord
    : {}
);
const asArray = (value: unknown): unknown[] => Array.isArray(value) ? value : [];
const asString = (value: unknown): string => typeof value === 'string' ? value : '';
const asBoolean = (value: unknown): boolean => value === true;
const asNumber = (value: unknown): number => typeof value === 'number' && Number.isFinite(value) ? value : 0;

async function acceptanceRequest(path: string, init?: RequestInit): Promise<JsonRecord> {
  const session = await getSession();
  if (!session) throw new Error('未登录或会话已失效，请重新登录。');
  const headers = new Headers(init?.headers);
  if (init?.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  const token = currentToken();
  if (token) headers.set('Authorization', `Bearer ${token}`);
  const response = await fetch(path, {
    ...init,
    headers,
    credentials: 'include',
    cache: 'no-store',
  });
  const raw = await response.text();
  let payload: JsonRecord = {};
  if (raw.trim()) {
    try {
      payload = asRecord(JSON.parse(raw));
    } catch {
      throw new Error('验收服务返回异常，请稍后重试。');
    }
  }
  if (!response.ok) {
    const message = asString(payload.message) || asString(payload.error);
    throw new Error(message || '飞书资料验收未完成，请检查连接状态后重试。');
  }
  return payload;
}

function connectorPath(projectId: string, connectorId: string, suffix: string): string {
  const project = projectId.trim();
  const connector = connectorId.trim();
  if (!project || !connector) throw new Error('缺少项目或飞书连接器标识。');
  return `/api/v1/projects/${encodeURIComponent(project)}/knowledge-connectors/${encodeURIComponent(connector)}${suffix}`;
}

function toSummary(value: unknown): ConnectorAcceptanceSummary {
  const row = asRecord(value);
  return {
    check_count: asNumber(row.check_count),
    blocker_failure_count: asNumber(row.blocker_failure_count),
    executed_run_count: asNumber(row.executed_run_count),
    required_run_count: asNumber(row.required_run_count),
    maximum_run_duration_seconds: asNumber(row.maximum_run_duration_seconds),
    minimum_coverage_ratio: asNumber(row.minimum_coverage_ratio),
    maximum_discovered_resource_count: asNumber(row.maximum_discovered_resource_count),
  };
}

function toReportSummary(value: unknown): ConnectorAcceptanceReportSummary {
  const row = asRecord(value);
  return {
    report_id: asString(row.report_id),
    acceptance_id: asString(row.acceptance_id),
    profile: asString(row.profile),
    verdict: asString(row.verdict),
    acceptance_ready: asBoolean(row.acceptance_ready),
    started_at_utc: asString(row.started_at_utc) || undefined,
    completed_at_utc: asString(row.completed_at_utc) || undefined,
    summary: toSummary(row.summary),
  };
}

function toCheck(value: unknown): ConnectorAcceptanceCheck {
  const row = asRecord(value);
  return {
    check_id: asString(row.check_id),
    status: asString(row.status),
    severity: asString(row.severity),
    observed: row.observed,
    expected: row.expected,
    detail: asString(row.detail) || undefined,
  };
}

function toReport(value: unknown): ConnectorAcceptanceReport {
  const row = asRecord(value);
  const governance = asRecord(row.governance);
  return {
    ...toReportSummary(row),
    project_id: asString(row.project_id),
    connector_instance_id: asString(row.connector_instance_id),
    checks: asArray(row.checks).map(toCheck).filter((item) => Boolean(item.check_id)),
    governance: {
      customer_material_access: asString(governance.customer_material_access) || undefined,
      customer_material_mutation_executed: typeof governance.customer_material_mutation_executed === 'boolean'
        ? governance.customer_material_mutation_executed
        : null,
      deletion_policy: asString(governance.deletion_policy) || undefined,
      customer_source_content_in_report: typeof governance.customer_source_content_in_report === 'boolean'
        ? governance.customer_source_content_in_report
        : null,
      raw_cursor_values_in_report: typeof governance.raw_cursor_values_in_report === 'boolean'
        ? governance.raw_cursor_values_in_report
        : null,
      credential_values_in_report: typeof governance.credential_values_in_report === 'boolean'
        ? governance.credential_values_in_report
        : null,
    },
    source_content_returned: false,
    raw_cursor_returned: false,
    credential_values_returned: false,
    filesystem_path_returned: false,
  };
}

export async function listConnectorAcceptanceReports(
  projectId: string,
  connectorId: string,
): Promise<ConnectorAcceptanceInventory> {
  const payload = await acceptanceRequest(
    connectorPath(projectId, connectorId, '/acceptance-reports'),
  );
  const data = asRecord(payload.data);
  const summary = asRecord(data.summary);
  return {
    project_id: asString(data.project_id) || projectId,
    connector_instance_id: asString(data.connector_instance_id) || connectorId,
    reports: asArray(data.reports).map(toReportSummary).filter((item) => Boolean(item.report_id)),
    summary: {
      report_count: asNumber(summary.report_count),
      passing_report_count: asNumber(summary.passing_report_count),
      failing_report_count: asNumber(summary.failing_report_count),
    },
  };
}

export async function getConnectorAcceptanceReport(
  projectId: string,
  connectorId: string,
  reportId: string,
): Promise<ConnectorAcceptanceReport> {
  const payload = await acceptanceRequest(
    connectorPath(projectId, connectorId, `/acceptance-reports/${encodeURIComponent(reportId)}`),
  );
  return toReport(asRecord(payload.data));
}

export async function runConnectorAcceptance(
  projectId: string,
  connectorId: string,
  profile: 'smoke' | 'pilot' | 'enterprise' = 'pilot',
): Promise<ConnectorAcceptanceReport> {
  const payload = await acceptanceRequest(
    connectorPath(projectId, connectorId, '/acceptance'),
    {
      method: 'POST',
      body: JSON.stringify({
        profile,
        allow_raw_text_fallback: false,
      }),
    },
  );
  return toReport(asRecord(payload.data));
}
