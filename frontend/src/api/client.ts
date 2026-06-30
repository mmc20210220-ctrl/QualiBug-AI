const API_BASE = '/api';

async function fetchJSON<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    ...options,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`API ${res.status}: ${text.slice(0, 200)}`);
  }
  return res.json();
}

export async function getFindings(projectId: string) {
  return fetchJSON<any>(`${API_BASE}/findings?project=${encodeURIComponent(projectId)}`);
}
export async function getOverview(projectId: string) {
  return fetchJSON<any>(`${API_BASE}/pilot/overview?project=${encodeURIComponent(projectId)}`);
}
export async function getKnowledge(projectId: string) {
  return fetchJSON<any>(`${API_BASE}/knowledge/asset?project=${encodeURIComponent(projectId)}`);
}
export async function getControlPlane(projectId: string) {
  return fetchJSON<any>(`${API_BASE}/control-plane/overview?project=${encodeURIComponent(projectId)}`);
}
export async function getReleaseDashboard(projectId: string) {
  return fetchJSON<any>(`${API_BASE}/release/dashboard?project=${encodeURIComponent(projectId)}`);
}

export async function runScan(projectId: string) {
  return fetchJSON<any>(`${API_BASE}/scan/run`, {
    method: 'POST',
    body: JSON.stringify({ project_id: projectId }),
  });
}

export async function saveSettings(body: Record<string, any>) {
  return fetchJSON<any>(`${API_BASE}/settings/save`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export async function saveEnvConfig(body: Record<string, any>) {
  return fetchJSON<any>(`${API_BASE}/environment/config`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export async function ingestKnowledge(projectId: string, file: File, type: string) {
  return new Promise<any>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = async () => {
      try {
        const b64 = (reader.result as string).split(',')[1];
        const result = await fetchJSON<any>(`${API_BASE}/knowledge/ingest`, {
          method: 'POST',
          body: JSON.stringify({
            project_id: projectId,
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
  return fetchJSON<any>(`${API_BASE}/knowledge/delete`, {
    method: 'POST',
    body: JSON.stringify({ project_id: projectId, source_id: sourceId }),
  });
}
