export const API_BASE = '/api';
import { resolveFindingTaxonomy } from '../lib/finding-taxonomy';

const API_V1_BASE = '/api/v1';
const TOKEN_KEY = 'qualibug_token';
type JsonRecord = Record<string, unknown>;
export type LoginResult = {
  ok: boolean;
  token: string;
  tenantId: string;
  role: string;
};

async function ensureAuth(): Promise<void> {
  if (localStorage.getItem(TOKEN_KEY)) return;

  // Development mode: allow auto-login via qualibug_dev_token in localStorage
  const devToken = localStorage.getItem('qualibug_dev_token');
  if (devToken) {
    localStorage.setItem(TOKEN_KEY, devToken);
    return;
  }

  // Production: must have a token. Throw a clear error so the UI can show
  // a login prompt instead of silently guessing credentials.
  throw new Error(
    '未登录。请在浏览器控制台设置登录令牌，或联系管理员配置认证。\n' +
    '示例: localStorage.setItem("qualibug_dev_token", "<your-jwt-token>")'
  );
}

export async function loginDetailed(username: string, password: string): Promise<LoginResult | null> {
  const resp = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  const data = await resp.json() as Record<string, unknown>;
  if (data.token) {
    localStorage.setItem(TOKEN_KEY, data.token as string);
    authStorageEvent();
    return {
      ok: Boolean(data.ok ?? true),
      token: String(data.token),
      tenantId: String(data.tenant_id || data.tenantId || username),
      role: String(data.role || ''),
    };
  }
  if (!resp.ok) throw new Error(asString(data.message) || asString(data.error) || `HTTP ${resp.status}`);
  return null;
}

export async function login(username: string, password: string): Promise<boolean> {
  const result = await loginDetailed(username, password);
  return Boolean(result?.token);
}

export type RegisterResult = {
  ok: boolean;
  tenantId: string;
  username: string;
  role: string;
};

export async function register({
  tenantId,
  name,
  username,
  password,
  role = 'admin',
}: {
  tenantId: string;
  name: string;
  username: string;
  password: string;
  role?: string;
}): Promise<RegisterResult | null> {
  const resp = await fetch('/api/tenants/create', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      tenant_id: tenantId,
      name,
      username,
      password,
      role: role || 'admin',
    }),
  });
  const data = await resp.json() as Record<string, unknown>;
  if (!resp.ok) throw new Error(asString(data.message) || asString(data.error) || `HTTP ${resp.status}`);
  if (data.ok) {
    return {
      ok: true,
      tenantId: String(data.tenant_id || tenantId),
      username: String(data.username || username),
      role: String(data.role || 'admin'),
    };
  }
  return null;
}

export function currentToken(): string {
  return localStorage.getItem(TOKEN_KEY) || '';
}

export function clearDevToken(): void {
  localStorage.removeItem('qualibug_dev_token');
}

export function authStorageEvent(): void {
  window.dispatchEvent(new Event('qualibug-auth-change'));
}

export function setAuthenticatedToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
  authStorageEvent();
}

export function hasUsableAuth(): boolean {
  if (currentToken()) return true;
  if (localStorage.getItem('qualibug_dev_token')) return true;
  return false;
}

export function logout(): void {
  localStorage.removeItem(TOKEN_KEY);
  clearDevToken();
  authStorageEvent();
}

export function isAuthenticated(): boolean {
  return hasUsableAuth();
}

async function fetchWithTenant(url: string, init?: RequestInit): Promise<unknown> {
  await ensureAuth();
  const headers: Record<string, string> = { ...(init?.headers as Record<string, string> || {}) };
  const token = localStorage.getItem(TOKEN_KEY);
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const resp = await fetch(url, { ...init, headers });
  if (!resp.ok) throw new Error(parseApiErrorMessage(resp.status, await resp.text()));
  return resp.json();
}

function fetchJSON<T>(url: string, init?: RequestInit): Promise<T> {
  return fetchWithTenant(url, init) as Promise<T>;
}
type ProjectSummary = {
  project_id?: string;
  customer_name?: string;
  project_name?: string;
  system_name?: string;
  industry?: string;
};
export type CustomerWorkspace = ProjectSummary;
export type ConnectorRecord = {
  connector_id: string;
  kind: string;
  display_name: string;
  enabled: boolean;
  system_name?: string;
  module_name?: string;
  endpoint_ref?: string;
  credential_ref?: string;
  external_ref?: string;
  created_at_utc?: string;
  last_sync_at_utc?: string;
  last_sync_status?: string;
};
let projectsCache: Promise<ProjectSummary[]> | null = null;

function asRecord(value: unknown): JsonRecord {
  return value && typeof value === 'object' ? (value as JsonRecord) : {};
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function asString(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function asNumber(value: unknown, fallback = 0): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}

function asBoolean(value: unknown): boolean {
  if (typeof value === 'boolean') return value;
  if (typeof value === 'number') return value !== 0;
  if (typeof value === 'string') {
    const normalized = value.trim().toLowerCase();
    return normalized === 'true' || normalized === '1' || normalized === 'yes';
  }
  return false;
}

function parseApiErrorMessage(status: number, text: string) {
  const trimmed = text.trim();
  if (!trimmed) return `API ${status}`;
  try {
    const payload = asRecord(JSON.parse(trimmed));
    const message = asString(payload.message) || asString(payload.error);
    if (message) return `API ${status}: ${message}`;
  } catch {
    // fall back to raw text
  }
  return `API ${status}: ${trimmed.slice(0, 200)}`;
}

async function listProjects() {
  if (!projectsCache) {
    projectsCache = fetchJSON<unknown>(`${API_V1_BASE}/projects`)
      .then((payload) => {
        const data = asRecord(payload).data;
        return asArray(data).map((item) => asRecord(item) as ProjectSummary);
      })
      .catch((error) => {
        projectsCache = null;
        throw error;
      });
  }
  return projectsCache;
}

export async function getProjects(options?: { force?: boolean }) {
  if (options?.force) projectsCache = null;
  return listProjects();
}

async function resolveProjectId(projectId: string) {
  const normalized = String(projectId || '').trim();
  if (!normalized) return '';
  try {
    const projects = await listProjects();
    return projects.some((item) => item.project_id === normalized) ? normalized : normalized;
  } catch {
    return normalized; // Trust URL param on failure
  }
}

export type FindingsSnapshot = {
  resolvedProjectId: string;
  projectId: string;
  projectName: string;
  status: string;
  updatedAt: string;
  industry: string;
  knowledgeSummary: {
    activeSourceCount: number;
    ruleCount: number;
    riskDomainCount: number;
    oracleCount: number;
    businessObjectCount: number;
    stateMachineCount: number;
    knowledgeReady: boolean;
  };
  findings: ReturnType<typeof toLegacyFinding>[];
  executiveSummary: {
    totalFindings: number;
    totalBugsFound: number;
    criticalBugs: number;
    highPriorityBugs: number;
    llmPoweredAnalyses: number;
    systemGrade: string;
    overallScore: number;
  };
  runtimeVerification: {
    totalProbes: number;
    confirmed: number;
  };
  dbVerification: {
    total: number;
    confirmed: number;
    hitRate: number;
  };
  scanMeta: {
    scanId: string;
    runCount: number;
    firstScanAt: string;
    lastScanAt: string;
    totalMs: number;
    totalFindings: number;
    grade: string;
    score: number;
    reportPath: string;
  };
  valueMetrics: JsonRecord;
  businessFlowSummary: JsonRecord;
  discoveryFunnel: JsonRecord;
  fullSpectrumCapabilityMatrix: JsonRecord;
  bugFamilyCoverage: JsonRecord;
  continuousDiscoveryCampaign: JsonRecord;
  continuousDiscoveryMetrics: JsonRecord;
  spectrum: JsonRecord;
};

function normalizeSeverity(input: string | undefined) {
  const value = String(input || '').toLowerCase();
  if (value === 'critical' || value === 'p0') return 'P0';
  if (value === 'high' || value === 'p1') return 'P1';
  if (value === 'medium' || value === 'p2') return 'P2';
  return 'P2';
}

function toLegacyFinding(risk: JsonRecord) {
  const businessFlow = asRecord(risk.affected_business_flow);
  const flow = asString(businessFlow.name) || asString(businessFlow.business_flow_id) || asString(risk.impact_area);
  const evidence = asRecord(risk.evidence);
  const proof = asRecord(risk.proof) || asRecord(risk.evidence_pack);
  const evidenceText = [
    asString(risk.technical_title),
    asString(risk.summary),
    asString(risk.title),
    asString(evidence.summary),
    asString(evidence.path),
    asString(evidence.path_template),
    asString(risk.path),
  ].filter(Boolean).join(' ');
  const methodMatch = evidenceText.match(/\b(GET|POST|PUT|PATCH|DELETE)\b/i);
  const pathMatch = evidenceText.match(/(\/api\/[A-Za-z0-9_./:{}-]+|\/[A-Za-z0-9_./:{}-]+\{[^\s]*\}|\/[A-Za-z0-9_./:{}-]+)/i);
  const method = (
    asString(risk._api_method)
    || asString(risk.method)
    || asString(evidence.method)
    || asString(proof.method)
    || methodMatch?.[1]
    || ''
  ).toUpperCase();
  const path = asString(risk._api_path)
    || asString(risk.path)
    || asString(evidence.path)
    || asString(evidence.path_template)
    || asString(proof.path)
    || pathMatch?.[1]
    || '';
  const title = asString(risk.title) || asString(risk.technical_title) || '未命名缺陷';
  const status = asString(risk.status) || asString(risk.validation_verdict) || asString(risk.bug_confirmation);
  const normalizedStatus = status.toLowerCase();
  const riskType = asString(risk.risk_type) || asString(risk.category) || 'unknown';
  const businessImpactText = asString(risk.business_impact) || asString(risk.impact) || asString(risk.description);
  const summary = asString(risk.summary) || asString(evidence.summary) || businessImpactText;
  const expectedBehavior = asString(risk.expected_behavior)
    || asString(risk.expected)
    || asString(evidence.expected)
    || asString(risk.suggested_action)
    || '未关联到明确的预期规则；需要回链 PRD / API 规范补强。';
  const actualBehavior = asString(risk.actual_behavior)
    || asString(risk.actual)
    || asString(evidence.actual)
    || businessImpactText
    || summary
    || '已发现风险线索，但缺少真实响应体、截图或日志片段。';
  const confidenceScore = asNumber(risk.confidence_score, asNumber(risk.evidence_score, asNumber(risk.confidence, 0.5)));
  const reproducibilityScore = asNumber(risk.reproducibility_score, asNumber(risk.reproducibility_confidence, 0));
  const firstSeenAt = asString(risk.first_seen_at) || asString(risk.created_at_utc) || asString(risk.generated_at_utc);
  const lastVerifiedAt = asString(risk.last_verified_at) || asString(risk.updated_at_utc) || asString(risk.timestamp);
  const riskId = asString(risk.risk_id)
    || asString(risk.finding_id)
    || asString(risk.issue_id)
    || asString(risk.bug_id)
    || `${method || 'RISK'}:${path || title}`;
  const affectedModules = asArray(risk.affected_modules).filter((item): item is string => typeof item === 'string');
  const affectedRoles = asArray(risk.affected_roles).filter((item): item is string => typeof item === 'string');
  const docRefs = asArray(risk._doc_refs).filter((item): item is JsonRecord => Boolean(item) && typeof item === 'object')
    || asArray(risk.doc_refs).filter((item): item is JsonRecord => Boolean(item) && typeof item === 'object');
  const rawSteps = asArray(risk.reproduction_steps).length ? asArray(risk.reproduction_steps) : asArray(risk.reproduce_steps_business);
  const reproductionSteps = rawSteps.map((item) => String(item)).filter(Boolean);
  if (!reproductionSteps.length) {
    reproductionSteps.push(
      path ? `在测试环境触发 ${method || 'GET'} ${path}` : `进入业务流 ${flow || '核心链路'} 触发相关场景`,
      '记录请求参数、响应状态码、响应体、时间戳和业务主键',
      `对比预期规则：${expectedBehavior}`,
      `观察实际结果：${actualBehavior}`,
    );
  }
  const taxonomy = resolveFindingTaxonomy({
    title,
    risk_type: riskType,
    defect_family: asString(risk.defect_family),
    reporting_bucket: asString(risk.reporting_bucket),
    repro_path: path,
    quality_assurance_gap: asBoolean(risk.quality_assurance_gap),
  });
  const confirmed = ['confirmed', 'validated', 'reproduced', 'reproducible'].some((token) => normalizedStatus.includes(token));
  const sourceFile = asString(risk.source_file) || asString(evidence.source_file) || asString(evidence.report_path);
  const evidenceHint = asString(risk.evidence_hint)
    || asString(evidence.hint)
    || sourceFile
    || (path ? `${method || 'GET'} ${path}` : summary);
  const moduleName = affectedModules[0] || asString(risk.module) || asString(evidence.module) || flow || '核心业务';

  return {
    bug_title: title,
    title,
    severity: normalizeSeverity(asString(risk.severity)),
    defect_family: taxonomy.defect_family,
    defect_family_label: taxonomy.defect_family_label,
    verdict: confirmed ? 'confirmed' : 'pending',
    bug_confirmation: confirmed ? 'confirmed' : 'unconfirmed_candidate',
    confidence_score: confidenceScore,
    actual_behavior: actualBehavior,
    expected_behavior: expectedBehavior,
    description: businessImpactText || summary,
    source: asString(risk.source) || sourceFile || 'phase104_command_center',
    impact_area: flow,
    source_entity: affectedModules.join(' / ') || moduleName,
    source_value: affectedRoles.join(', ') || asString(risk.source_value) || asString(evidence.assurance_unit_id) || asString(evidence.operation_id),
    risk_type: riskType,
    reporting_bucket: taxonomy.reporting_bucket,
    reporting_bucket_label: taxonomy.reporting_bucket_label,
    quality_assurance_gap: taxonomy.quality_assurance_gap,
    timestamp: lastVerifiedAt || firstSeenAt || new Date().toISOString(),
    validation_task_id: riskId,
    reproduction_steps: reproductionSteps,
    reproducibility: {
      reproducible: confirmed || reproducibilityScore >= 0.75,
      reproduction_confidence: Math.max(reproducibilityScore, confirmed ? 0.9 : 0),
    },
    evidence: {
      ...evidence,
      hash: asString(evidence.hash) || riskId,
      path,
      method,
      summary: asString(evidence.summary) || summary || title,
      source_file: sourceFile,
      status_code: evidence.status_code || evidence.response_status || risk.response_status,
      expected: expectedBehavior,
      actual: actualBehavior,
    },
    path,
    method,
    _api_path: path,
    _api_method: method,
    evidence_hint: evidenceHint,
    business_impact: {
      summary: businessImpactText || actualBehavior,
      urgency: normalizeSeverity(asString(risk.severity)),
      module: moduleName,
    },
    investigation_guidance: {
      primary_area: moduleName,
      relevant_apis: path ? [`${method || 'GET'} ${path}`] : [],
      relevant_tables: asArray(risk.relevant_tables).map((item) => String(item)).filter(Boolean),
      log_search: evidenceHint || (path ? `${method || 'GET'} ${path}` : title),
      sql_verify: asString(risk.sql_verify) || '按业务主键核对请求前后状态、金额、库存、权限或审批记录是否一致。',
      trace_id: asString(risk.trace_id) || asString(evidence.trace_id),
    },
    reproduce_steps_business: reproductionSteps,
    _doc_refs: docRefs,
  };
}

async function buildFindingsSnapshot(projectId: string): Promise<FindingsSnapshot> {
  const resolvedProjectId = await resolveProjectId(projectId);
  if (!resolvedProjectId) return emptyFindingsSnapshot('');

  try {
    const snapshotEnvelope = await fetchJSON<unknown>(
      `${API_V1_BASE}/projects/${encodeURIComponent(resolvedProjectId)}/command-center`
    );

    const snapshot = asRecord(asRecord(snapshotEnvelope).data);
    const liveMap = asRecord(snapshot.live_map);
    const scanMeta = asRecord(snapshot.scan_meta);
    const valueMetrics = asRecord(snapshot.value_metrics);
    const businessFlowSummary = asRecord(snapshot.business_flow_summary);
    const knowledgeSummary = asRecord(snapshot.knowledge_summary);
    const risks = asArray(snapshot.risks).map(asRecord);
    const findings = risks.map(toLegacyFinding);
    const p0 = findings.filter((item) => item.severity === 'P0').length;
    const p1 = findings.filter((item) => item.severity === 'P1').length;
    const evidenceTrustScore = asNumber(valueMetrics.evidence_trust_score, 0);
    const aiEquivalentTestPoints = asNumber(valueMetrics.ai_equivalent_test_points, 0);
    const totalBusinessFlows = asNumber(businessFlowSummary.total, 0);

    return {
      resolvedProjectId,
      projectId: resolvedProjectId,
      projectName: asString(snapshot.project_name) || resolvedProjectId,
      status: asString(liveMap.status) === 'running' ? 'running' : 'completed',
      updatedAt: asString(snapshot.updated_at) || '',
      industry: asString(snapshot.industry),
      knowledgeSummary: {
        activeSourceCount: asNumber(knowledgeSummary.active_source_count, 0),
        ruleCount: asNumber(knowledgeSummary.rule_count, 0),
        riskDomainCount: asNumber(knowledgeSummary.risk_domain_count, 0),
        oracleCount: asNumber(knowledgeSummary.oracle_count, 0),
        businessObjectCount: asNumber(knowledgeSummary.business_object_count, 0),
        stateMachineCount: asNumber(knowledgeSummary.state_machine_count, 0),
        knowledgeReady: asBoolean(knowledgeSummary.knowledge_ready),
      },
      findings,
      executiveSummary: {
        totalFindings: findings.length,
        totalBugsFound: findings.length,
        criticalBugs: p0,
        highPriorityBugs: p1,
        llmPoweredAnalyses: aiEquivalentTestPoints,
        systemGrade: asString(asRecord(snapshot.executive_summary).system_grade),
        overallScore: asNumber(asRecord(snapshot.executive_summary).overall_score, 0),
      },
      runtimeVerification: {
        totalProbes: totalBusinessFlows,
        confirmed: findings.filter((item) => item.verdict === 'confirmed').length,
      },
      dbVerification: {
        total: findings.length,
        confirmed: findings.filter((item) => item.defect_family === 'data_integrity').length,
        hitRate: evidenceTrustScore ? Math.round(evidenceTrustScore * 100) : 0,
      },
      scanMeta: {
        scanId: asString(scanMeta.scan_id),
        runCount: asNumber(scanMeta.run_count, 0),
        firstScanAt: asString(scanMeta.first_scan_at),
        lastScanAt: asString(scanMeta.last_scan_at) || asString(snapshot.updated_at),
        totalMs: asNumber(scanMeta.total_ms, 0),
        totalFindings: asNumber(scanMeta.total_findings, findings.length),
        grade: asString(scanMeta.grade),
        score: asNumber(scanMeta.score, 0),
        reportPath: asString(scanMeta.report_path),
      },
      valueMetrics,
      businessFlowSummary,
      discoveryFunnel: asRecord(snapshot.discovery_funnel),
      fullSpectrumCapabilityMatrix: asRecord(snapshot.full_spectrum_capability_matrix),
      bugFamilyCoverage: asRecord(snapshot.bug_family_coverage),
      continuousDiscoveryCampaign: asRecord(snapshot.continuous_discovery_campaign),
      continuousDiscoveryMetrics: asRecord(snapshot.continuous_discovery_metrics),
      spectrum: asRecord(snapshot.spectrum),
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (message.includes('404')) {
      return emptyFindingsSnapshot(resolvedProjectId);
    }
    throw error;
  }
}

function emptyFindingsSnapshot(projectId: string): FindingsSnapshot {
  return {
    resolvedProjectId: projectId,
    projectId,
    projectName: projectId,
    status: 'idle',
    updatedAt: '',
    industry: '',
    knowledgeSummary: {
      activeSourceCount: 0,
      ruleCount: 0,
      riskDomainCount: 0,
      oracleCount: 0,
      businessObjectCount: 0,
      stateMachineCount: 0,
      knowledgeReady: false,
    },
    findings: [],
    executiveSummary: {
      totalFindings: 0,
      totalBugsFound: 0,
      criticalBugs: 0,
      highPriorityBugs: 0,
      llmPoweredAnalyses: 0,
      systemGrade: '',
      overallScore: 0,
    },
    runtimeVerification: {
      totalProbes: 0,
      confirmed: 0,
    },
    dbVerification: {
      total: 0,
      confirmed: 0,
      hitRate: 0,
    },
    scanMeta: {
      scanId: '',
      runCount: 0,
      firstScanAt: '',
      lastScanAt: '',
      totalMs: 0,
      totalFindings: 0,
      grade: '',
      score: 0,
      reportPath: '',
    },
    valueMetrics: {},
    businessFlowSummary: {},
    discoveryFunnel: {},
    fullSpectrumCapabilityMatrix: {},
    bugFamilyCoverage: {},
    continuousDiscoveryCampaign: {},
    continuousDiscoveryMetrics: {},
    spectrum: {},
  };
}

export async function getFindings(projectId: string) {
  return buildFindingsSnapshot(projectId);
}

export async function getKnowledgeAsset(projectId: string) {
  const resolvedProjectId = await resolveProjectId(projectId);
  if (!resolvedProjectId) return { knowledge_asset: { project_id: '', sources: [] } };
  return fetchJSON<unknown>(`${API_BASE}/knowledge/asset?project=${encodeURIComponent(resolvedProjectId)}`);
}

export async function getKnowledgePreview(sourceId: string) {
  return fetchJSON<unknown>(`${API_BASE}/knowledge/preview?sourceId=${encodeURIComponent(sourceId)}`);
}

export async function saveSettings(body: Record<string, unknown>) {
  return fetchJSON<unknown>(`${API_BASE}/settings/save`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export async function getHealth() {
  return fetchJSON<unknown>(`${API_BASE}/health`);
}

export async function listConnectors(projectId: string): Promise<ConnectorRecord[]> {
  const project = String(projectId || '').trim();
  if (!project) return [];
  const payload = await fetchJSON<unknown>(`${API_BASE}/connectors/list?project=${encodeURIComponent(project)}`);
  const connectors = asArray(asRecord(payload).connectors).map((item) => asRecord(item));
  return connectors
    .filter((item) => Boolean(asString(item.connector_id)))
    .map((item) => ({
      connector_id: asString(item.connector_id),
      kind: asString(item.kind),
      display_name: asString(item.display_name),
      enabled: Boolean(item.enabled),
      system_name: asString(item.system_name) || undefined,
      module_name: asString(item.module_name) || undefined,
      endpoint_ref: asString(item.endpoint_ref) || undefined,
      credential_ref: asString(item.credential_ref) || undefined,
      external_ref: asString(item.external_ref) || undefined,
      created_at_utc: asString(item.created_at_utc) || undefined,
      last_sync_at_utc: asString(item.last_sync_at_utc) || undefined,
      last_sync_status: asString(item.last_sync_status) || undefined,
    }));
}

export async function registerConnector(body: Record<string, unknown>) {
  return fetchJSON<unknown>(`${API_BASE}/connectors/register`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export async function ingestKnowledge(projectId: string, file: File, type: string) {
  return new Promise<unknown>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = async () => {
      try {
        const resolvedProjectId = await resolveProjectId(projectId);
        if (!resolvedProjectId) {
          throw new Error('未选择有效项目，无法导入资料');
        }
        const b64 = (reader.result as string).split(',')[1];
        const result = await fetchJSON<unknown>(`${API_BASE}/knowledge/ingest`, {
          method: 'POST',
          body: JSON.stringify({
            project_id: resolvedProjectId,
            type,
            filename: file.name,
            content: b64,
          }),
        });
        resolve(result);
      } catch (e) {
        reject(e);
      }
    };
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

export async function deleteKnowledge(projectId: string, sourceId: string) {
  return fetchJSON<unknown>(`${API_BASE}/knowledge/delete`, {
    method: 'POST',
    body: JSON.stringify({ project_id: projectId, source_id: sourceId }),
  });
}

// V12 Unified Scan
export type V12ScanResult = {
  ok: boolean;
  scan_id?: string;
  grade?: string;
  score?: number;
  coverage?: number;
  total_findings?: number;
  total_ms?: number;
  layers?: Record<string, { findings: number; ms: number; tool?: string }>;
  spectrum?: { capabilities_run: number; total_findings: number };
  auto_har?: Record<string, unknown>;
  report_path?: string;
  error?: string;
  message?: string;
};

export async function runV12Scan(projectId: string, options?: { api_doc?: string; base_url?: string }): Promise<V12ScanResult> {
  return fetchJSON<V12ScanResult>(`${API_BASE}/v1/scan`, {
    method: 'POST',
    body: JSON.stringify({
      project_id: projectId,
      api_doc: options?.api_doc || undefined,
      base_url: options?.base_url || undefined,
    }),
  });
}

export async function testDbConnection(dsn: string): Promise<{ ok: boolean; message?: string; error?: string; db_type?: string; host?: string; port?: number }> {
  return fetchJSON(`${API_BASE}/v1/db-test`, {
    method: 'POST',
    body: JSON.stringify({ dsn }),
  });
}

export async function getServiceCredentials(projectId: string) {
  return fetchJSON(`${API_BASE}/v1/services/credentials?project=${encodeURIComponent(projectId)}`);
}

export async function saveServiceCredentials(body: Record<string, unknown>) {
  return fetchJSON(`${API_BASE}/v1/services/credentials`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
}
