import { runV12Scan } from './client';

export type JsonRecord = Record<string, unknown>;

/**
 * Result of a controlled standard scan run from the 运行中心 (Run Center).
 *
 * This talks to the SINGLE backend (the private pilot service on :8088 that also
 * serves the SPA) via the shared /api/v1/scan endpoint — there is no separate
 * enterprise API host. The runtime execution evidence the backend returns
 * (auto_har + execution_evidence_summary) is surfaced verbatim so the run is
 * observable and reproducible from the product itself.
 */
export type EnterpriseScanResult = {
  ok?: boolean;
  success?: boolean;
  scan_id?: string;
  project?: string;
  grade?: string;
  score?: number;
  coverage?: number;
  total_findings?: number;
  execution_status?: string;
  auto_har?: JsonRecord;
  execution_evidence_summary?: JsonRecord;
  runtime_contract?: JsonRecord;
  campaign?: JsonRecord;
  report_path?: string;
  error?: string;
  message?: string;
};

export async function runEnterpriseScan(
  projectId: string,
  options?: { api_doc?: string; base_url?: string; scope_id?: string; environment_ref?: string },
): Promise<EnterpriseScanResult> {
  const result = await runV12Scan(projectId, options);
  return result as unknown as EnterpriseScanResult;
}
