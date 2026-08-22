import { fetchWithAuth } from './client';
import { asArray, asNum, asRecord, asString } from '../lib/value-guards';

export type ContinuousRunRow = {
  scan_id: string;
  timestamp: string;
  findings: number;
  coverage: number;
  grade: string;
  duration_ms: number;
};

export type ContinuousState = {
  status: string;
  converged: boolean;
  runs: ContinuousRunRow[];
  totalRuns: number;
  lastScan: string;
  lastFindings: number;
  lastCoverage: number;
  message: string;
};

function parseRun(value: unknown): ContinuousRunRow {
  const row = asRecord(value);
  return {
    scan_id: asString(row.scan_id),
    timestamp: asString(row.timestamp),
    findings: asNum(row.findings),
    coverage: asNum(row.coverage),
    grade: asString(row.grade),
    duration_ms: asNum(row.duration_ms),
  };
}

export async function getContinuousState(projectId: string): Promise<ContinuousState> {
  const project = projectId.trim();
  if (!project) {
    return { status: 'idle', converged: false, runs: [], totalRuns: 0, lastScan: '', lastFindings: 0, lastCoverage: 0, message: '未选择有效项目。' };
  }
  const response = await fetchWithAuth('/api/v1/continuous/status', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ project_id: project }),
  });
  if (!response.ok) {
    throw new Error(`持续检测状态读取失败：HTTP ${response.status}`);
  }
  const payload = asRecord(await response.json());
  return {
    status: asString(payload.status) || 'idle',
    converged: payload.converged === true,
    runs: asArray(payload.runs).slice(-10).map(parseRun),
    totalRuns: asNum(payload.total_runs),
    lastScan: asString(payload.last_scan),
    lastFindings: asNum(payload.last_findings),
    lastCoverage: asNum(payload.last_coverage),
    message: asString(payload.message),
  };
}

export async function startContinuousScan(projectId: string, intervalSeconds = 60): Promise<{ ok: boolean; message: string }> {
  const project = projectId.trim();
  if (!project) return { ok: false, message: '未选择有效项目。' };
  const response = await fetchWithAuth('/api/v1/continuous/start', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ project_id: project, interval_s: intervalSeconds }),
  });
  const payload = asRecord(await response.json().catch(() => ({})));
  return { ok: response.ok && payload.ok !== false, message: asString(payload.message) || (response.ok ? '持续检测已启动。' : '持续检测启动失败。') };
}

export async function stopContinuousScan(projectId: string): Promise<{ ok: boolean; message: string }> {
  const project = projectId.trim();
  if (!project) return { ok: false, message: '未选择有效项目。' };
  const response = await fetchWithAuth('/api/v1/continuous/stop', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ project_id: project }),
  });
  const payload = asRecord(await response.json().catch(() => ({})));
  return { ok: response.ok && payload.ok !== false, message: asString(payload.message) || (response.ok ? '持续检测已停止。' : '持续检测停止失败。') };
}
