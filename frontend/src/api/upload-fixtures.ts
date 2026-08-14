import { fetchWithAuth } from './client';

export type UploadFixtureRecord = {
  fixture_id: string;
  binding_ref?: string;
  fixture_name?: string;
  ref?: string;
  namespace?: string;
  status?: 'active' | 'revoked' | string;
  authority?: 'source_registered' | 'approved_copy' | string;
  sha256?: string;
  size_bytes?: number;
  content_type?: string;
  file_suffix?: string;
  created_at_utc?: string;
  created_by?: string;
  approved_from_fixture_id?: string;
  revoked_at_utc?: string;
  revoked_by?: string;
  revocation_reason?: string;
};

export type UploadFixtureList = {
  ok: boolean;
  schema_version?: string;
  project_id?: string;
  fixtures: UploadFixtureRecord[];
  summary?: {
    active_count?: number;
    revoked_count?: number;
    source_registered_count?: number;
    approved_copy_count?: number;
  };
};

type RegistryResult = {
  ok: boolean;
  status?: string;
  fixture?: UploadFixtureRecord;
  revoked_records?: UploadFixtureRecord[];
  error?: string;
  message?: string;
};

function endpoint(projectId: string, upload = false): string {
  const project = projectId.trim();
  if (!project) throw new Error('请先选择客户项目。');
  return `/api/v1/projects/${encodeURIComponent(project)}/ui-upload-fixtures${upload ? '/upload' : ''}`;
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
      // The bounded raw body below remains more useful than a JSON parser error.
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

export async function listUploadFixtures(
  projectId: string,
  includeRevoked = false,
): Promise<UploadFixtureList> {
  const query = includeRevoked ? '?include_revoked=true' : '';
  const response = await fetchWithAuth(`${endpoint(projectId)}${query}`);
  const payload = await responseJson<UploadFixtureList>(response);
  return {
    ...payload,
    fixtures: Array.isArray(payload.fixtures) ? payload.fixtures : [],
  };
}

export async function uploadFixtureFile(
  projectId: string,
  file: File,
  fixtureName: string,
): Promise<RegistryResult> {
  if (!file || file.size < 1) throw new Error('请选择非空测试文件。');
  if (file.size > 10 * 1024 * 1024) {
    throw new Error('浏览器直接上传上限为 10MiB；更大文件请先放入项目输入目录后登记。');
  }
  const response = await fetchWithAuth(endpoint(projectId, true), {
    method: 'POST',
    headers: {
      'Content-Type': file.type || 'application/octet-stream',
      'X-QualiBug-Filename': encodeURIComponent(file.name),
      'X-QualiBug-Fixture-Name': encodeURIComponent(fixtureName.trim() || file.name),
    },
    body: file,
  });
  return responseJson<RegistryResult>(response);
}

async function mutateFixture(
  projectId: string,
  action: 'approve' | 'revoke',
  fixtureId: string,
  reason = '',
): Promise<RegistryResult> {
  const response = await fetchWithAuth(endpoint(projectId), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      action,
      fixture_id: fixtureId,
      ...(reason.trim() ? { reason: reason.trim() } : {}),
    }),
  });
  return responseJson<RegistryResult>(response);
}

export function approveUploadFixture(
  projectId: string,
  fixtureId: string,
): Promise<RegistryResult> {
  return mutateFixture(projectId, 'approve', fixtureId);
}

export function revokeUploadFixture(
  projectId: string,
  fixtureId: string,
  reason: string,
): Promise<RegistryResult> {
  if (!reason.trim()) return Promise.reject(new Error('撤销必须填写原因。'));
  return mutateFixture(projectId, 'revoke', fixtureId, reason);
}
