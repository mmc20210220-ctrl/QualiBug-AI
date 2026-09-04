import { beforeEach, describe, expect, it, vi } from 'vitest';

const fetchJSONMock = vi.fn();
const resolveProjectIdMock = vi.fn();

vi.mock('../session', () => {
  class MockApiError extends Error {
    readonly status: number;

    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  }

  return {
    API_BASE: '/api',
    API_V1_BASE: '/api/v1',
    ApiError: MockApiError,
    asBoolean: (value: unknown, fallback = false) => typeof value === 'boolean' ? value : fallback,
    fetchJSON: (...args: unknown[]) => fetchJSONMock(...args),
    fetchPublicJSON: vi.fn(),
    resolveProjectId: (...args: unknown[]) => resolveProjectIdMock(...args),
    authStorageEvent: vi.fn(),
    clearDevToken: vi.fn(),
    currentToken: vi.fn(),
    fetchWithAuth: vi.fn(),
    getProjects: vi.fn(),
    getSession: vi.fn(),
    hasUsableAuth: vi.fn(),
    isAuthenticated: vi.fn(),
    login: vi.fn(),
    loginDetailed: vi.fn(),
    logout: vi.fn(),
    register: vi.fn(),
    resetPassword: vi.fn(),
    setAuthenticatedToken: vi.fn(),
  };
});

import {
  getFindings,
  getKnowledgeAsset,
  getScanPreflight,
  listConnectors,
} from '../client';

describe('frontend display read paths', () => {
  beforeEach(() => {
    fetchJSONMock.mockReset();
    resolveProjectIdMock.mockReset();
    fetchJSONMock.mockImplementation((url: unknown) => {
      const path = String(url || '');
      if (path === '/api/v1/projects/acme/command-center') {
        return Promise.resolve({ data: { project_name: 'Acme' } });
      }
      if (path === '/api/knowledge/summary?project=acme') {
        return Promise.resolve({ project_id: 'acme', summary: { active_source_count: 0 }, sources: [] });
      }
      if (path === '/api/connectors/list?project=acme') {
        return Promise.resolve({ connectors: [] });
      }
      if (path === '/api/v1/scan/preflight?project=acme') {
        return Promise.resolve({ ok: true, ready: true, reasons: [] });
      }
      return Promise.reject(new Error(`unexpected request: ${path}`));
    });
  });

  it('starts command-center immediately without serial /projects resolution', async () => {
    const payload = await getFindings(' acme ');

    expect(payload.resolvedProjectId).toBe('acme');
    expect(fetchJSONMock).toHaveBeenCalledWith('/api/v1/projects/acme/command-center');
    expect(resolveProjectIdMock).not.toHaveBeenCalled();
  });

  it('starts independent display reads directly from the selected project id', async () => {
    await Promise.all([
      getKnowledgeAsset(' acme '),
      listConnectors(' acme '),
      getScanPreflight(' acme '),
    ]);

    expect(fetchJSONMock.mock.calls.map((call) => call[0])).toEqual([
      '/api/knowledge/summary?project=acme',
      '/api/connectors/list?project=acme',
      '/api/v1/scan/preflight?project=acme',
    ]);
    expect(resolveProjectIdMock).not.toHaveBeenCalled();
  });
});
