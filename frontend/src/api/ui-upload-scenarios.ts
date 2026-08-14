import { fetchWithAuth } from './client';

export type UploadScenarioRecord = {
  scenario_id: string;
  scenario_ref?: string;
  title?: string;
  status?: 'active' | 'revoked' | string;
  authority?: 'source_declared_candidate' | 'approved_copy' | string;
  source_id?: string;
  source_version_id?: string;
  source_hash?: string;
  source_locator?: string;
  contract_id?: string;
  contract_sha256?: string;
  fixture_binding_refs?: string[];
  submission_mode?: 'click_submit' | 'auto_on_file_selection' | string;
  business_cleanup_required?: boolean;
  cleanup_action?: string;
  safe_prerequisite_method?: string;
  actor_role?: string;
  raw_selectors_included?: boolean;
  raw_assertion_text_included?: boolean;
  raw_probe_urls_included?: boolean;
  created_at_utc?: string;
  created_by?: string;
  approved_at_utc?: string;
  approved_by?: string;
  approved_from_scenario_id?: string;
  revoked_at_utc?: string;
  revoked_by?: string;
  revocation_reason?: string;
};

export type UploadScenarioInput = {
  title: string;
  source_id: string;
  source_locator: string;
  operation_ref: string;
  actor_role: string;
  start_url: string;
  fixture_binding_refs: string[];
  upload_selector: string;
  submission_mode: 'click_submit' | 'auto_on_file_selection';
  submit_selector?: string;
  cleanup_selector: string;
  assertion_selector: string;
  assertion_text: string;
  rendered_probe_selector: string;
  persistent_probe_url: string;
  persistent_json_pointer: string;
  frame_selector?: string;
  frame_origin?: string;
};

export type UploadScenarioList = {
  ok: boolean;
  scenarios: UploadScenarioRecord[];
  summary?: {
    active_count?: number;
    revoked_count?: number;
    candidate_count?: number;
    approved_count?: number;
  };
};

type RegistryResult = {
  ok: boolean;
  status?: string;
  scenario?: UploadScenarioRecord;
  revoked_records?: UploadScenarioRecord[];
  error?: string;
  message?: string;
};

function endpoint(projectId: string): string {
  const project = projectId.trim();
  if (!project) throw new Error('请先选择客户项目。');
  return `/api/v1/projects/${encodeURIComponent(project)}/ui-upload-scenarios`;
}

async function responseJson<T>(response: Response): Promise<T> {
  const text = await response.text();
  let payload: Record<string, unknown> = {};
  if (text.trim()) {
    try {
      const parsed = JSON.parse(text) as unknown;
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
        payload = parsed as Record<string, unknown>;
      }
    } catch {
      // Bounded raw response below remains useful.
    }
  }
  if (!response.ok) {
    const message = typeof payload.message === 'string'
      ? payload.message
      : typeof payload.error === 'string'
        ? payload.error
        : text.slice(0, 200) || `API ${response.status}`;
    throw new Error(message);
  }
  return payload as unknown as T;
}

export async function listUploadScenarios(
  projectId: string,
  includeRevoked = false,
): Promise<UploadScenarioList> {
  const query = includeRevoked ? '?include_revoked=true' : '';
  const response = await fetchWithAuth(`${endpoint(projectId)}${query}`);
  const payload = await responseJson<UploadScenarioList>(response);
  return {
    ...payload,
    scenarios: Array.isArray(payload.scenarios) ? payload.scenarios : [],
  };
}

export async function registerUploadScenario(
  projectId: string,
  payload: UploadScenarioInput,
): Promise<RegistryResult> {
  const response = await fetchWithAuth(endpoint(projectId), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action: 'register', payload }),
  });
  return responseJson<RegistryResult>(response);
}

async function mutateScenario(
  projectId: string,
  action: 'approve' | 'revoke',
  scenarioId: string,
  reason = '',
): Promise<RegistryResult> {
  const response = await fetchWithAuth(endpoint(projectId), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      action,
      scenario_id: scenarioId,
      ...(reason.trim() ? { reason: reason.trim() } : {}),
    }),
  });
  return responseJson<RegistryResult>(response);
}

export function approveUploadScenario(
  projectId: string,
  scenarioId: string,
): Promise<RegistryResult> {
  return mutateScenario(projectId, 'approve', scenarioId);
}

export function revokeUploadScenario(
  projectId: string,
  scenarioId: string,
  reason: string,
): Promise<RegistryResult> {
  if (!reason.trim()) return Promise.reject(new Error('撤销必须填写原因。'));
  return mutateScenario(projectId, 'revoke', scenarioId, reason);
}
