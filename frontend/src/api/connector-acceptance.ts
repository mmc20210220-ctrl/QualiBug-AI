import { currentToken, getSession } from './client';
import { asArray, asRecord, asStrictNumber, asString } from '../lib/value-guards';

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
  arbitrary_diagnostic_text_returned: false;
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

export type ConnectorAcceptanceJob = {
  job_id: string;
  project_id: string;
  connector_instance_id: string;
  profile: string;
  status: 'NOT_STARTED' | 'PENDING' | 'RUNNING' | 'COMPLETE' | 'FAILED' | 'INTERRUPTED' | string;
  requested_at_utc?: string;
  started_at_utc?: string;
  completed_at_utc?: string;
  report_id?: string;
  verdict?: string;
  acceptance_ready: boolean;
  error_type?: string;
  terminal: boolean;
  governance: {
    customer_material_access?: string;
    deletion_policy?: string;
    source_content_returned: false;
    raw_cursor_returned: false;
    credential_values_returned: false;
    filesystem_path_returned: false;
    background_execution?: boolean;
  };
};

const asBoolean = (value: unknown): boolean => value === true;

function friendlyAcceptanceError(rawMessage: string, status: number): string {
  const message = rawMessage.toLowerCase();
  if (
    message.includes('already_running')
    || message.includes('lock_held')
    || message.includes('owner_active')
    || message.includes('transaction_busy')
  ) {
    return '在线资料正在同步或验收，请在当前任务完成后重试。';
  }
  if (message.includes('permission') || message.includes('forbidden')) {
    return '连接器只读权限不足，请检查Manifest声明的授权范围。';
  }
  if (message.includes('not_found') || status === 404) {
    return '尚未找到验收任务或报告，请先运行一次 Pilot 验收。';
  }
  if (message.includes('profile') || message.includes('credential') || message.includes('access_token')) {
    return '连接器信息未通过验收，请重新检查授权配置。';
  }
  if (message.includes('transport') || message.includes('api_failed') || status >= 500) {
    return '连接器服务暂时不可用，已有资料不受影响，请稍后重试验收。';
  }
  return '在线资料验收未完成，请检查连接和同步状态后重试。';
}

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
    throw new Error(friendlyAcceptanceError(message, response.status));
  }
  return payload;
}

function connectorPath(projectId: string, connectorId: string, suffix: string): string {
  const project = projectId.trim();
  const connector = connectorId.trim();
  if (!project || !connector) throw new Error('缺少项目或在线连接器标识。');
  return `/api/v1/projects/${encodeURIComponent(project)}/knowledge-connectors/${encodeURIComponent(connector)}${suffix}`;
}

function toSummary(value: unknown): ConnectorAcceptanceSummary {
  const row = asRecord(value);
  return {
    check_count: asStrictNumber(row.check_count),
    blocker_failure_count: asStrictNumber(row.blocker_failure_count),
    executed_run_count: asStrictNumber(row.executed_run_count),
    required_run_count: asStrictNumber(row.required_run_count),
    maximum_run_duration_seconds: asStrictNumber(row.maximum_run_duration_seconds),
    minimum_coverage_ratio: asStrictNumber(row.minimum_coverage_ratio),
    maximum_discovered_resource_count: asStrictNumber(row.maximum_discovered_resource_count),
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

function assertFalseFields(row: JsonRecord, fields: string[], label: string): void {
  for (const field of fields) {
    if (row[field] !== false) {
      throw new Error(`${label}缺少完整的安全证明，已拒绝在页面展示。`);
    }
  }
}

function toReport(value: unknown): ConnectorAcceptanceReport {
  const row = asRecord(value);
  assertFalseFields(
    row,
    [
      'source_content_returned',
      'raw_cursor_returned',
      'credential_values_returned',
      'filesystem_path_returned',
      'arbitrary_diagnostic_text_returned',
    ],
    '验收报告',
  );
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
    arbitrary_diagnostic_text_returned: false,
  };
}

function toJob(value: unknown): ConnectorAcceptanceJob {
  const row = asRecord(value);
  const governance = asRecord(row.governance);
  assertFalseFields(
    governance,
    [
      'source_content_returned',
      'raw_cursor_returned',
      'credential_values_returned',
      'filesystem_path_returned',
    ],
    '验收任务',
  );
  return {
    job_id: asString(row.job_id),
    project_id: asString(row.project_id),
    connector_instance_id: asString(row.connector_instance_id),
    profile: asString(row.profile),
    status: asString(row.status) || 'NOT_STARTED',
    requested_at_utc: asString(row.requested_at_utc) || undefined,
    started_at_utc: asString(row.started_at_utc) || undefined,
    completed_at_utc: asString(row.completed_at_utc) || undefined,
    report_id: asString(row.report_id) || undefined,
    verdict: asString(row.verdict) || undefined,
    acceptance_ready: asBoolean(row.acceptance_ready),
    error_type: asString(row.error_type) || undefined,
    terminal: asBoolean(row.terminal),
    governance: {
      customer_material_access: asString(governance.customer_material_access) || undefined,
      deletion_policy: asString(governance.deletion_policy) || undefined,
      source_content_returned: false,
      raw_cursor_returned: false,
      credential_values_returned: false,
      filesystem_path_returned: false,
      background_execution: asBoolean(governance.background_execution),
    },
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
      report_count: asStrictNumber(summary.report_count),
      passing_report_count: asStrictNumber(summary.passing_report_count),
      failing_report_count: asStrictNumber(summary.failing_report_count),
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

export async function getConnectorAcceptanceJob(
  projectId: string,
  connectorId: string,
  jobId = 'current',
): Promise<ConnectorAcceptanceJob> {
  const payload = await acceptanceRequest(
    connectorPath(projectId, connectorId, `/acceptance-jobs/${encodeURIComponent(jobId)}`),
  );
  return toJob(asRecord(payload.data));
}

export async function startConnectorAcceptance(
  projectId: string,
  connectorId: string,
  profile: 'smoke' | 'pilot' | 'enterprise' = 'pilot',
): Promise<ConnectorAcceptanceJob> {
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
  return toJob(asRecord(payload.data));
}
